import asyncio
import hashlib
from collections.abc import Sequence
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

import app.masked_repair as masked_repair_module
from app.domain import AssetLifecycleStatus, AssetMediaKind, AssetOriginKind
from app.masked_repair import (
    MaskedEditCallResult,
    MaskedEditGatewayRequest,
    MaskedRepairError,
    MaskedVisualReviewCallResult,
    MaskedVisualReviewRequest,
)
from app.models import (
    AssetRecord,
    AuditEventRecord,
    JobAssetRecord,
    JobAttemptRecord,
    UsageEventRecord,
    WorkspaceRecord,
)
from app.outbox import publish_pending_events
from app.provider import (
    ProviderArtifact,
    ProviderOutcome,
    ProviderRequest,
    ProviderResult,
)
from app.task_runner import MockTaskRunner
from app.xinghe_agent import FakeXingheGateway, OperationRoutingProvider
from tests.test_api import WORKSPACE_ID, local_edit_payload


def _png(
    size: tuple[int, int],
    color: tuple[int, int, int, int],
) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _selection_png(
    size: tuple[int, int],
    *,
    selected: Sequence[tuple[int, int]],
) -> bytes:
    image = Image.new("L", size, 0)
    for point in selected:
        image.putpixel(point, 255)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _selection_rect_png(
    size: tuple[int, int],
    box: tuple[int, int, int, int],
) -> bytes:
    image = Image.new("L", size, 0)
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            image.putpixel((x, y), 255)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(
    client: TestClient,
    payload: bytes,
    *,
    media_kind: str,
    filename: str,
) -> dict:
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
        headers={"Idempotency-Key": str(uuid4())},
        data={"origin_kind": "upload", "media_kind": media_kind},
        files={"file": (filename, payload, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_job(
    client: TestClient,
    source_id: str,
    mask_id: str,
    *,
    instruction: str | None = None,
) -> tuple[int, dict]:
    payload = local_edit_payload(source_id, mask_id)
    if instruction is not None:
        payload["configuration"]["instruction"] = instruction
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers={"Idempotency-Key": str(uuid4())},
        json=payload,
    )
    return response.status_code, response.json()


def _artifact(payload: bytes) -> ProviderArtifact:
    with Image.open(BytesIO(payload)) as image:
        width, height = image.size
    return ProviderArtifact(
        payload=payload,
        filename="raw.png",
        content_type="image/png",
        width=width,
        height=height,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _usage() -> dict[str, int | bool | None]:
    return {
        "reported": True,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
    }


class OfflineMaskedGateway(FakeXingheGateway):
    provider_name = "tengli-offline"
    image_model = "gpt-image-2"
    pricing_version = "offline-test-v1"

    def __init__(
        self,
        outcomes: Sequence[str] = ("success",),
        *,
        review_success: bool | None = True,
        raw_size: tuple[int, int] | None = None,
        report_actual_cost: bool = True,
        raw_alpha: int | None = None,
    ) -> None:
        super().__init__()
        self.outcomes = list(outcomes)
        self.review_success = review_success
        self.raw_size = raw_size
        self.report_actual_cost = report_actual_cost
        self.raw_alpha = raw_alpha
        self.masked_calls: list[MaskedEditGatewayRequest] = []
        self.review_calls: list[MaskedVisualReviewRequest] = []

    async def edit_masked(
        self,
        request: MaskedEditGatewayRequest,
    ) -> MaskedEditCallResult:
        self.masked_calls.append(request)
        outcome = self.outcomes.pop(0)
        if outcome == "transient":
            return MaskedEditCallResult(
                outcome=ProviderOutcome.FAILED,
                provider_request_id="offline_transient",
                raw_usage={},
                normalized_usage=_usage(),
                actual_cost_micros=0,
                estimated_cost_micros=80_000,
                error_code="LOCAL_EDIT_REQUEST_FAILED",
                error_message="controlled HTTP 503",
                retryable=True,
                http_status=503,
            )
        if outcome == "bad_request":
            return MaskedEditCallResult(
                outcome=ProviderOutcome.FAILED,
                provider_request_id="offline_bad_request",
                raw_usage={},
                normalized_usage=_usage(),
                actual_cost_micros=0,
                estimated_cost_micros=80_000,
                error_code="LOCAL_EDIT_REQUEST_FAILED",
                error_message="controlled HTTP 400",
                retryable=False,
                http_status=400,
            )
        raw_size = self.raw_size or (request.width, request.height)
        raw_color = (
            (220, 20, 40, 255)
            if self.raw_alpha is None
            else (0, 0, 0, self.raw_alpha)
        )
        raw = _png(raw_size, raw_color)
        return MaskedEditCallResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id="offline_success",
            raw_usage={"actual_cost_micros": 12_000} if self.report_actual_cost else {},
            normalized_usage=_usage()
            if self.report_actual_cost
            else {"reported": False},
            actual_cost_micros=12_000 if self.report_actual_cost else None,
            estimated_cost_micros=80_000,
            artifact=_artifact(raw),
            http_status=200,
        )

    async def review_masked(
        self,
        request: MaskedVisualReviewRequest,
    ) -> MaskedVisualReviewCallResult:
        self.review_calls.append(request)
        return MaskedVisualReviewCallResult(
            provider_request_id="offline_review_success",
            raw_usage={"actual_cost_micros": 2_000} if self.report_actual_cost else {},
            normalized_usage=_usage()
            if self.report_actual_cost
            else {"reported": False},
            actual_cost_micros=2_000 if self.report_actual_cost else None,
            estimated_cost_micros=10_000,
            visual_success=self.review_success,
            summary="Controlled offline visual-review result.",
            reason_codes=(
                ("offline_review_passed",)
                if self.review_success
                else ("instruction_not_satisfied",)
            ),
            error_code=(
                "LOCAL_EDIT_VISUAL_REVIEW_UNAVAILABLE"
                if self.review_success is None
                else None
            ),
            error_message=(
                "Controlled review unavailable."
                if self.review_success is None
                else None
            ),
            http_status=200,
        )


class RetryThenSuccessReviewGateway(OfflineMaskedGateway):
    async def review_masked(
        self,
        request: MaskedVisualReviewRequest,
    ) -> MaskedVisualReviewCallResult:
        self.review_calls.append(request)
        if len(self.review_calls) == 1:
            return MaskedVisualReviewCallResult(
                provider_request_id="offline_review_retry",
                raw_usage={"actual_cost_micros": 1_000},
                normalized_usage={
                    "reported": True,
                    "input_tokens": 3,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "total_tokens": 3,
                },
                actual_cost_micros=1_000,
                estimated_cost_micros=10_000,
                visual_success=None,
                error_code="LOCAL_EDIT_VISUAL_REVIEW_REQUEST_FAILED",
                error_message="controlled HTTP 503",
                retryable=True,
                http_status=503,
            )
        return MaskedVisualReviewCallResult(
            provider_request_id="offline_review_success",
            raw_usage={"actual_cost_micros": 2_000},
            normalized_usage={
                "reported": True,
                "input_tokens": 5,
                "output_tokens": 2,
                "cached_tokens": 0,
                "total_tokens": 7,
            },
            actual_cost_micros=2_000,
            estimated_cost_micros=10_000,
            visual_success=True,
            summary="Controlled retry success.",
            reason_codes=("offline_review_passed",),
            http_status=200,
        )


def _install_runner(
    client: TestClient,
    gateway: OfflineMaskedGateway,
) -> None:
    client.app.state.task_runner = MockTaskRunner(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        xinghe_concurrency=2,
        budget_limit_micros=200_000_000,
    )


def _dispatch(client: TestClient) -> int:
    return publish_pending_events(
        client.app.state.settings,
        client.app.state.session_factory,
        client.app.state.task_runner,
    )


def _valid_inputs(client: TestClient) -> tuple[dict, dict, bytes]:
    source_bytes = _png((8, 8), (10, 40, 90, 255))
    selected = [(x, y) for x in range(2, 6) for y in range(2, 6)]
    mask_bytes = _selection_png((8, 8), selected=selected)
    source = _upload(
        client,
        source_bytes,
        media_kind="image",
        filename="source.png",
    )
    mask = _upload(
        client,
        mask_bytes,
        media_kind="mask",
        filename="selection.png",
    )
    return source, mask, source_bytes


def test_real_local_edit_locks_every_outside_pixel_and_records_raw(
    client: TestClient,
) -> None:
    gateway = OfflineMaskedGateway()
    _install_runner(client, gateway)
    source, mask, source_bytes = _valid_inputs(client)
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202

    assert _dispatch(client) == 1
    assert _dispatch(client) == 0

    with client.app.state.session_factory() as session:
        relations = session.scalars(
            select(JobAssetRecord)
            .where(JobAssetRecord.job_id == job["id"])
            .order_by(JobAssetRecord.id)
        ).all()
        raw_relation = next(item for item in relations if item.role == "provider_raw")
        output_relation = next(
            item for item in relations if item.role == "local_edit_result"
        )
        raw_asset = session.get(AssetRecord, raw_relation.asset_id)
        output_asset = session.get(AssetRecord, output_relation.asset_id)
        assert raw_asset is not None
        assert output_asset is not None
        assert raw_relation.is_deliverable is False
        assert output_relation.is_deliverable is True
        assert raw_asset.asset_metadata["immutable_provider_raw"] is True
        assert raw_asset.asset_metadata["source_sha256"] == source["sha256"]
        assert raw_asset.asset_metadata["mask_sha256"] == mask["sha256"]
        assert raw_asset.asset_metadata["provider_request_id"] == "offline_success"
        assert raw_asset.asset_metadata["raw_visual_success"] is True
        assert (
            raw_asset.asset_metadata["visual_review_request_id"]
            == "offline_review_success"
        )
        assert raw_relation.validation["raw_visual_success"] is True
        assert output_relation.validation["outside_changed_pixels"] == 0
        assert output_relation.validation["raw_visual_success"] is True
        output_key = output_asset.object_key

        assert (
            session.scalar(
                select(func.count())
                .select_from(JobAttemptRecord)
                .where(JobAttemptRecord.job_id == job["id"])
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageEventRecord)
                .where(UsageEventRecord.job_id == job["id"])
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(
                    AuditEventRecord.resource_id == job["items"][0]["id"],
                    AuditEventRecord.action == "masked_repair.image_reserved",
                )
            )
            == 1
        )

    delivered = asyncio.run(client.app.state.storage.read_bytes(output_key))
    with Image.open(BytesIO(source_bytes)) as source_image:
        source_rgba = source_image.convert("RGBA")
    with Image.open(BytesIO(delivered)) as result_image:
        result_rgba = result_image.convert("RGBA")
    assert result_rgba.size == source_rgba.size == (8, 8)
    for y in range(8):
        for x in range(8):
            if not (2 <= x < 6 and 2 <= y < 6):
                assert result_rgba.getpixel((x, y)) == source_rgba.getpixel((x, y))
    assert len(gateway.masked_calls) == 1
    masked_request = gateway.masked_calls[0]
    assert masked_request.idempotency_key
    assert "除非用户明确要求修改" in masked_request.prompt
    assert "Logo、标签、文字、图案和其他标记" in masked_request.prompt
    with Image.open(BytesIO(masked_request.source_png)) as provider_source_image:
        provider_source = provider_source_image.convert("RGBA")
    with Image.open(BytesIO(masked_request.api_mask_png)) as api_mask_image:
        api_mask_alpha = api_mask_image.convert("RGBA").getchannel("A")
    selected_point = (masked_request.width // 2, masked_request.height // 2)
    assert provider_source.getpixel(selected_point) == (10, 40, 90, 255)
    assert provider_source.getpixel((0, 0)) == (10, 40, 90, 255)
    assert api_mask_alpha.getpixel(selected_point) == 0
    assert api_mask_alpha.getpixel((0, 0)) == 255
    assert len(gateway.review_calls) == 1
    assert gateway.review_calls[0].source_png == source_bytes
    assert gateway.review_calls[0].authority_png
    assert gateway.review_calls[0].candidate_png


def test_transparent_provider_raw_is_materialized_before_review_and_locked(
    client: TestClient,
) -> None:
    gateway = OfflineMaskedGateway(raw_alpha=128)
    _install_runner(client, gateway)
    source, mask, source_bytes = _valid_inputs(client)
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202
    assert _dispatch(client) == 1

    with client.app.state.session_factory() as session:
        relations = session.scalars(
            select(JobAssetRecord).where(JobAssetRecord.job_id == job["id"])
        ).all()
        raw_relation = next(item for item in relations if item.role == "provider_raw")
        output_relation = next(
            item for item in relations if item.role == "local_edit_result"
        )
        raw_asset = session.get(AssetRecord, raw_relation.asset_id)
        output_asset = session.get(AssetRecord, output_relation.asset_id)
        assert raw_asset is not None
        assert output_asset is not None
        raw_key = raw_asset.object_key
        output_key = output_asset.object_key

    request = gateway.masked_calls[0]
    expected_raw = _png((request.width, request.height), (0, 0, 0, 128))
    persisted_raw = asyncio.run(client.app.state.storage.read_bytes(raw_key))
    assert persisted_raw == expected_raw
    assert raw_asset.sha256 == hashlib.sha256(expected_raw).hexdigest()

    with Image.open(BytesIO(source_bytes)) as source_image:
        source_rgba = source_image.convert("RGBA")
    expected_candidate = Image.alpha_composite(
        source_rgba,
        Image.new("RGBA", source_rgba.size, (0, 0, 0, 128)),
    )
    with Image.open(BytesIO(gateway.review_calls[0].candidate_png)) as candidate_image:
        candidate_rgba = candidate_image.convert("RGBA")
    assert candidate_rgba.getpixel((0, 0)) == expected_candidate.getpixel((0, 0))
    assert candidate_rgba.getpixel((0, 0)) != (0, 0, 0, 255)

    delivered = asyncio.run(client.app.state.storage.read_bytes(output_key))
    with Image.open(BytesIO(delivered)) as result_image:
        result_rgba = result_image.convert("RGBA")
    for y in range(8):
        for x in range(8):
            if not (2 <= x < 6 and 2 <= y < 6):
                assert result_rgba.getpixel((x, y)) == source_rgba.getpixel((x, y))


def test_visual_review_retries_once_without_regenerating_and_records_attempts(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        masked_repair_module,
        "_VISUAL_REVIEW_RETRY_DELAY_SECONDS",
        0,
    )
    gateway = RetryThenSuccessReviewGateway()
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202
    assert _dispatch(client) == 1
    body = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert body["api_request_count"] == 3

    with client.app.state.session_factory() as session:
        usage = session.scalar(
            select(UsageEventRecord).where(UsageEventRecord.job_id == job["id"])
        )
        assert usage is not None
        assert usage.actual_cost_micros == 15_000
        assert usage.estimated_cost_micros == 100_000
        assert usage.raw_usage["input_tokens"] == 8
        attempts = usage.raw_usage["metrics"]["visual_review_attempts"]

    assert len(gateway.masked_calls) == 1
    assert len(gateway.review_calls) == 2
    assert len({call.idempotency_key for call in gateway.review_calls}) == 1
    assert attempts == [
        {
            "attempt": 1,
            "provider_request_id": "offline_review_retry",
            "http_status": 503,
            "error_code": "LOCAL_EDIT_VISUAL_REVIEW_REQUEST_FAILED",
            "retryable": True,
            "visual_success": None,
            "estimated_cost_micros": 10_000,
            "actual_cost_micros": 1_000,
        },
        {
            "attempt": 2,
            "provider_request_id": "offline_review_success",
            "http_status": 200,
            "error_code": None,
            "retryable": False,
            "visual_success": True,
            "estimated_cost_micros": 10_000,
            "actual_cost_micros": 2_000,
        },
    ]


def test_terminal_quota_settlement_releases_unused_retry_without_fake_actual_cost(
    client: TestClient,
) -> None:
    settings = client.app.state.settings
    settings.task_runner = "tengli"
    settings.tengli_daily_budget_usd = 1.5
    settings.tengli_monthly_budget_usd = 5
    settings.tengli_daily_request_limit = 8
    settings.tengli_planner_reserve_micros = 250_000
    gateway = OfflineMaskedGateway(report_actual_cost=False)
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)

    status_code, first = _create_job(client, source["id"], mask["id"])
    assert status_code == 202
    assert _dispatch(client) == 1
    first_body = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{first['id']}"
    ).json()
    assert first_body["status"] == "completed"
    assert first_body["api_request_count"] == 2

    second_status, _ = _create_job(client, source["id"], mask["id"])
    assert second_status == 202
    with client.app.state.session_factory() as session:
        settlement = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == first["id"],
                AuditEventRecord.action == "provider.beta_job_settled",
            )
        )
        release = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == first["id"],
                AuditEventRecord.action == "provider.beta_job_released",
            )
        )
        attempts = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == first["id"],
                AuditEventRecord.action == "provider.beta_job_attempts_recorded",
            )
        )
        usage = session.scalar(
            select(UsageEventRecord).where(UsageEventRecord.job_id == first["id"])
        )
        assert settlement is not None
        assert release is not None
        assert attempts is not None
        assert usage is not None
        assert usage.actual_cost_micros is None
        assert usage.raw_usage["network_requests"] == 2
        assert usage.raw_usage["metrics"]["transport_attempts"] == 1
        assert usage.raw_usage["metrics"]["visual_review_requests"] == 1
        assert settlement.details["charged_cost_micros"] == 90_000
        assert settlement.details["actual_provider_request_count"] == 2
        assert settlement.details["cost_basis"] == "estimated_fallback"
        assert settlement.details["actual_cost_reported_count"] == 0
        assert release.details["released_cost_micros"] == 1_090_000
        assert release.details["released_request_count"] == 4
        assert attempts.details["attempts"][0]["actual_cost_reported"] is False


def test_visual_review_rejection_retains_raw_without_delivery_or_regeneration(
    client: TestClient,
) -> None:
    gateway = OfflineMaskedGateway(review_success=False)
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202

    assert _dispatch(client) == 1
    body = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert body["status"] == "failed"
    assert body["items"][0]["error_code"] == ("LOCAL_EDIT_VISUAL_REVIEW_REJECTED")

    with client.app.state.session_factory() as session:
        relations = session.scalars(
            select(JobAssetRecord).where(JobAssetRecord.job_id == job["id"])
        ).all()
        raw_relation = next(item for item in relations if item.role == "provider_raw")
        assert raw_relation.is_deliverable is False
        assert raw_relation.validation["raw_visual_success"] is False
        assert all(item.role != "local_edit_result" for item in relations)

    assert len(gateway.masked_calls) == 1
    assert len(gateway.review_calls) == 1


def test_non_multiple_source_and_relay_drift_map_back_without_authority_leak(
    client: TestClient,
) -> None:
    source_size = (1200, 1202)
    raw_size = (1275, 1233)
    gateway = OfflineMaskedGateway(raw_size=raw_size)
    _install_runner(client, gateway)
    source_bytes = _png(source_size, (10, 20, 30, 255))
    source = _upload(
        client,
        source_bytes,
        media_kind="image",
        filename="non-multiple-source.png",
    )
    mask = _upload(
        client,
        _selection_rect_png(source_size, (400, 400, 800, 800)),
        media_kind="mask",
        filename="non-multiple-selection.png",
    )
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202

    assert _dispatch(client) == 1
    request = gateway.masked_calls[0]
    assert request.width == 1200
    assert request.height == 1216
    assert request.width % 16 == request.height % 16 == 0

    with client.app.state.session_factory() as session:
        relations = session.scalars(
            select(JobAssetRecord).where(JobAssetRecord.job_id == job["id"])
        ).all()
        raw_relation = next(item for item in relations if item.role == "provider_raw")
        output_relation = next(
            item for item in relations if item.role == "local_edit_result"
        )
        raw_asset = session.get(AssetRecord, raw_relation.asset_id)
        output_asset = session.get(AssetRecord, output_relation.asset_id)
        assert raw_asset is not None
        assert output_asset is not None
        assert (raw_asset.width, raw_asset.height) == raw_size
        assert (output_asset.width, output_asset.height) == source_size
        assert output_relation.validation["outside_changed_pixels"] == 0
        usage = session.scalar(
            select(UsageEventRecord).where(UsageEventRecord.job_id == job["id"])
        )
        assert usage is not None
        metrics = usage.raw_usage["metrics"]
        assert metrics["provider_request_width"] == 1200
        assert metrics["provider_request_height"] == 1216
        assert metrics["provider_raw_width"] == 1275
        assert metrics["provider_raw_height"] == 1233
        assert metrics["geometry_mapping"] == "uniform_cover_center_crop_v1"


def test_excessive_relay_drift_fails_before_visual_review_and_keeps_raw(
    client: TestClient,
) -> None:
    gateway = OfflineMaskedGateway(raw_size=(1800, 900), review_success=True)
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202

    assert _dispatch(client) == 1
    body = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert body["status"] == "failed"
    assert body["items"][0]["error_code"] == "LOCAL_EDIT_RAW_GEOMETRY_UNMAPPABLE"
    with client.app.state.session_factory() as session:
        relations = session.scalars(
            select(JobAssetRecord).where(JobAssetRecord.job_id == job["id"])
        ).all()
        raw_relation = next(item for item in relations if item.role == "provider_raw")
        assert raw_relation.validation["raw_visual_success"] is None
        assert all(item.role != "local_edit_result" for item in relations)
        usage = session.scalar(
            select(UsageEventRecord).where(UsageEventRecord.job_id == job["id"])
        )
        assert usage is not None
        metrics = usage.raw_usage["metrics"]
        assert metrics["raw_visual_success"] is None
        assert metrics["strict_lock_success"] is False
        assert metrics["delivery_status"] == "strict_lock_failed"
        assert metrics["provider_raw_width"] == 1800
        assert metrics["provider_raw_height"] == 900
    assert len(gateway.masked_calls) == 1
    assert metrics["visual_review_requests"] == 0
    assert len(gateway.review_calls) == 0


def test_visual_review_unavailable_stops_before_strict_lock_and_keeps_raw(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strict_called = False

    def unexpected_strict(*args: object, **kwargs: object) -> object:
        nonlocal strict_called
        strict_called = True
        raise AssertionError("strict lock must wait for a positive visual verdict")

    monkeypatch.setattr(
        masked_repair_module,
        "_strict_composite",
        unexpected_strict,
    )
    gateway = OfflineMaskedGateway(review_success=None)
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202

    assert _dispatch(client) == 1
    body = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert body["status"] == "failed"
    assert body["items"][0]["error_code"] == "LOCAL_EDIT_VISUAL_REVIEW_UNAVAILABLE"
    assert strict_called is False
    with client.app.state.session_factory() as session:
        relations = session.scalars(
            select(JobAssetRecord).where(JobAssetRecord.job_id == job["id"])
        ).all()
        raw_relation = next(item for item in relations if item.role == "provider_raw")
        assert raw_relation.validation["raw_visual_success"] is None
        assert all(item.role != "local_edit_result" for item in relations)
        usage = session.scalar(
            select(UsageEventRecord).where(UsageEventRecord.job_id == job["id"])
        )
        assert usage is not None
        metrics = usage.raw_usage["metrics"]
        assert metrics["raw_visual_success"] is None
        assert metrics["strict_lock_success"] is None
        assert metrics["delivery_status"] == "visual_review_unavailable"
    assert len(gateway.masked_calls) == 1
    assert len(gateway.review_calls) == 1


def test_visual_rejection_happens_before_strict_composite(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strict_called = False

    def unexpected_strict(*args: object, **kwargs: object) -> object:
        nonlocal strict_called
        strict_called = True
        raise AssertionError("strict composite must not run before review acceptance")

    monkeypatch.setattr(
        masked_repair_module,
        "_strict_composite",
        unexpected_strict,
    )
    gateway = OfflineMaskedGateway(review_success=False)
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202

    assert _dispatch(client) == 1
    body = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert body["items"][0]["error_code"] == "LOCAL_EDIT_VISUAL_REVIEW_REJECTED"
    assert strict_called is False


def test_raw_visual_success_is_preserved_when_strict_mapping_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_strict(*args: object, **kwargs: object) -> object:
        raise MaskedRepairError(
            "LOCAL_EDIT_PIXEL_LOCK_FAILED",
            "Controlled strict lock failure.",
        )

    monkeypatch.setattr(masked_repair_module, "_strict_composite", fail_strict)
    gateway = OfflineMaskedGateway(review_success=True)
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202

    assert _dispatch(client) == 1
    body = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert body["status"] == "failed"
    assert body["items"][0]["error_code"] == "LOCAL_EDIT_PIXEL_LOCK_FAILED"

    with client.app.state.session_factory() as session:
        relations = session.scalars(
            select(JobAssetRecord).where(JobAssetRecord.job_id == job["id"])
        ).all()
        raw_relation = next(item for item in relations if item.role == "provider_raw")
        assert raw_relation.validation["raw_visual_success"] is True
        assert all(item.role != "local_edit_result" for item in relations)
        usage = session.scalar(
            select(UsageEventRecord).where(UsageEventRecord.job_id == job["id"])
        )
        assert usage is not None
        assert usage.raw_usage["metrics"]["raw_visual_success"] is True
        assert usage.raw_usage["metrics"]["strict_lock_success"] is False
        assert usage.raw_usage["metrics"]["delivery_status"] == (
            "raw_visual_success_locked_failed"
        )
    assert len(gateway.masked_calls) == 1
    assert len(gateway.review_calls) == 1


@pytest.mark.parametrize(
    ("mask_payload", "expected_code"),
    [
        (_selection_png((8, 8), selected=[]), "LOCAL_EDIT_MASK_EMPTY"),
        (
            _selection_png(
                (8, 8),
                selected=[(x, y) for x in range(8) for y in range(8)],
            ),
            "LOCAL_EDIT_MASK_FULL_FRAME",
        ),
    ],
)
def test_empty_and_full_masks_fail_before_provider(
    client: TestClient,
    mask_payload: bytes,
    expected_code: str,
) -> None:
    gateway = OfflineMaskedGateway()
    _install_runner(client, gateway)
    source = _upload(
        client,
        _png((8, 8), (10, 20, 30, 255)),
        media_kind="image",
        filename="source.png",
    )
    mask = _upload(
        client,
        mask_payload,
        media_kind="mask",
        filename="selection.png",
    )
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202

    assert _dispatch(client) == 1
    response = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}")
    failed_item = response.json()["items"][0]
    assert failed_item["error_code"] == expected_code
    assert failed_item["error_details"]["needs_reselection"] is True
    assert gateway.masked_calls == []



def test_encoding_damaged_instruction_fails_before_provider_calls(
    client: TestClient,
) -> None:
    settings = client.app.state.settings
    settings.task_runner = "tengli"
    settings.tengli_daily_budget_usd = 2
    settings.tengli_monthly_budget_usd = 5
    settings.tengli_daily_request_limit = 20
    gateway = OfflineMaskedGateway()
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)

    status_code, job = _create_job(
        client,
        source["id"],
        mask["id"],
        instruction=(
            "???????????????;??????????POLARIS ????????????????"
        ),
    )
    assert status_code == 202
    assert _dispatch(client) == 1

    body = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert body["status"] == "failed"
    assert body["items"][0]["error_code"] == (
        "LOCAL_EDIT_INSTRUCTION_ENCODING_INVALID"
    )
    assert body["api_request_count"] == 0
    assert gateway.masked_calls == []
    assert gateway.review_calls == []
    with client.app.state.session_factory() as session:
        usage = session.scalar(
            select(UsageEventRecord).where(UsageEventRecord.job_id == job["id"])
        )
        assert usage is not None
        assert usage.raw_usage["network_requests"] == 0
        assert usage.actual_cost_micros == 0

    second_status, _ = _create_job(client, source["id"], mask["id"])
    assert second_status == 202
    with client.app.state.session_factory() as session:
        settlement = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job["id"],
                AuditEventRecord.action == "provider.beta_job_settled",
            )
        )
        release = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == job["id"],
                AuditEventRecord.action == "provider.beta_job_released",
            )
        )
        assert settlement is not None
        assert release is not None
        assert settlement.details["charged_cost_micros"] == 0
        assert settlement.details["actual_provider_request_count"] == 0
        assert release.details["released_request_count"] == 6

def test_dimensions_kind_and_cross_workspace_are_rejected_at_api(
    client: TestClient,
) -> None:
    source = _upload(
        client,
        _png((8, 8), (10, 20, 30, 255)),
        media_kind="image",
        filename="source.png",
    )
    wrong_size = _upload(
        client,
        _selection_png((7, 8), selected=[(2, 2)]),
        media_kind="mask",
        filename="wrong-size.png",
    )
    status_code, body = _create_job(client, source["id"], wrong_size["id"])
    assert status_code == 422
    assert body["error"]["code"] == "LOCAL_EDIT_DIMENSIONS_MISMATCH"

    wrong_kind = _upload(
        client,
        _selection_png((8, 8), selected=[(2, 2)]),
        media_kind="image",
        filename="wrong-kind.png",
    )
    status_code, body = _create_job(client, source["id"], wrong_kind["id"])
    assert status_code == 422
    assert body["error"]["code"] == "LOCAL_EDIT_MASK_KIND_INVALID"

    with client.app.state.session_factory() as session:
        session.add(
            WorkspaceRecord(
                id="ws_other",
                name="Other",
                created_by_user_id="usr_test",
            )
        )
        session.flush()
        session.add(
            AssetRecord(
                id="asset_other_mask",
                workspace_id="ws_other",
                media_kind=AssetMediaKind.MASK.value,
                origin_kind=AssetOriginKind.UPLOAD.value,
                lifecycle_status=AssetLifecycleStatus.READY.value,
                original_filename="other.png",
                content_type="image/png",
                byte_size=1,
                width=8,
                height=8,
                sha256="0" * 64,
                storage_provider="local",
                storage_bucket="local",
                object_key="ws_other/other.png",
                object_version="",
            )
        )
        session.commit()
    status_code, body = _create_job(client, source["id"], "asset_other_mask")
    assert status_code == 404
    assert body["error"]["code"] == "INPUT_ASSET_NOT_FOUND"


@pytest.mark.parametrize(
    ("outcomes", "expected_calls", "expected_status"),
    [
        (("transient", "success"), 2, "completed"),
        (("transient", "transient"), 2, "failed"),
        (("bad_request",), 1, "failed"),
    ],
)
def test_retry_policy_is_one_transient_retry_and_no_4xx_retry(
    client: TestClient,
    outcomes: Sequence[str],
    expected_calls: int,
    expected_status: str,
) -> None:
    gateway = OfflineMaskedGateway(outcomes)
    _install_runner(client, gateway)
    source, mask, _ = _valid_inputs(client)
    status_code, job = _create_job(client, source["id"], mask["id"])
    assert status_code == 202

    assert _dispatch(client) == 1
    response = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}")
    body = response.json()
    assert body["status"] == expected_status
    if expected_status == "failed":
        assert body["items"][0]["error_details"]["retryable"] is (
            outcomes[-1] == "transient"
        )
    assert len(gateway.masked_calls) == expected_calls
    assert len({call.idempotency_key for call in gateway.masked_calls}) == 1
    assert len(gateway.review_calls) == (1 if expected_status == "completed" else 0)


class RecordingProvider:
    name = "recording"
    model = "none"
    pricing_version = "test"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        self.calls += 1
        return ProviderResult(outcome=ProviderOutcome.SUCCEEDED)


def test_operation_router_keeps_mock_default_and_routes_real_local_provider() -> None:
    default = RecordingProvider()
    detail = RecordingProvider()
    local = RecordingProvider()
    request = ProviderRequest(
        workspace_id="ws",
        job_id="job",
        job_item_id="item",
        attempt_id="attempt",
        operation="local_edit",
        idempotency_key="idem",
        configuration={},
    )

    asyncio.run(OperationRoutingProvider(default, detail).execute(request))
    assert default.calls == 1
    assert local.calls == 0

    asyncio.run(OperationRoutingProvider(default, detail, local).execute(request))
    assert local.calls == 1
