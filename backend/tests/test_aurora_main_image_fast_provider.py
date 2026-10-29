"""File, scope, retry and paid-call invariants for fast main-image generation."""

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from app.aurora_main_image_fast_provider import (
    RECEIPTS_KEY,
    SCHEMA_VERSION,
    SKILL_ID,
    VERSION,
    FastMainImageProvider,
    generation_hash,
    image_prompt,
)
from app.aurora_main_image_planner import PAGE_ROLES
from app.models import (
    AssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    utc_now,
)
from app.provider import ProviderArtifact, ProviderOutcome, ProviderRequest
from app.services import canonical_hash
from app.xinghe_agent import ImageCallResult

from .test_replica import upload


class Gateway:
    provider_name = "synthetic-real-route"
    image_model = "test-image-model"
    pricing_version = "synthetic-zero"

    def __init__(self, *, outcome=ProviderOutcome.SUCCEEDED, size=(1024, 1024)):
        self.requests = []
        self.outcome = outcome
        self.size = size

    async def edit(self, request):
        self.requests.append(request)
        if self.outcome == "timeout":
            raise TimeoutError("submission outcome unknown")
        artifact = None
        if self.outcome == ProviderOutcome.SUCCEEDED:
            output = BytesIO()
            Image.new("RGB", self.size, "red").save(output, "PNG")
            payload = output.getvalue()
            artifact = ProviderArtifact(
                payload=payload,
                filename="native.png",
                content_type="image/png",
                width=self.size[0],
                height=self.size[1],
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        return ImageCallResult(
            outcome=self.outcome,
            provider_request_id="test-" + uuid4().hex,
            raw_usage=None,
            normalized_usage={"reported": True, "output_images": 1},
            api_latency_seconds=0.01,
            actual_cost_micros=None,
            estimated_cost_micros=None,
            artifact=artifact,
            error_code="KNOWN_REJECTION"
            if self.outcome == ProviderOutcome.FAILED
            else None,
        )


def setup(client, monkeypatch, gateway=None):
    monkeypatch.setattr(
        "app.aurora_main_image_fast_provider.BundledSkillManifestValidator.validate",
        lambda self, skill: "a" * 64,
    )
    asset_id = upload(client, "red")
    state = client.app.state
    with state.session_factory() as session:
        asset = session.get(AssetRecord, asset_id)
        plan = {
            "version": VERSION,
            "skill_id": SKILL_ID,
            "skill_content_hash": "a" * 64,
            "product_asset_ids": [asset_id],
            "source_bindings": [
                {
                    "asset_id": asset_id,
                    "sha256": asset.sha256,
                    "width": asset.width,
                    "height": asset.height,
                }
            ],
            "product_identity_lock": (
                "Red primary product; preserve exact label and shape."
            ),
            "confirmed_facts": ["用户授权的红色商品"],
            "user_facts": ["用户授权的红色商品"],
            "style_lock": "Consistent white and red lighting and typography.",
            "pages": [
                {
                    "page_index": i,
                    "page_role": role,
                    "visual_plan": (
                        "One differentiated commercial composition for this page."
                    ),
                    "visible_copy": ["真实商品"],
                    "forbidden_elements": "No invented claims.",
                }
                for i, role in enumerate(PAGE_ROLES)
            ],
            "canvas": [1024, 1024],
        }
    request = persist(client, plan)
    gateway = gateway or Gateway()
    provider = FastMainImageProvider(state.session_factory, gateway, state.storage)
    return provider, gateway, request, plan


def persist(
    client,
    plan,
    *,
    retry_of=None,
    receipts=None,
    speed="fast",
    schema_version=SCHEMA_VERSION,
):
    job_id, item_id, attempt_id = [prefix + uuid4().hex for prefix in ("j", "i", "a")]
    with client.app.state.session_factory() as session:
        session.add(
            JobRecord(
                id=job_id,
                workspace_id="ws_test",
                type="domestic_main_image",
                status="processing",
                retry_of_job_id=retry_of,
                configuration={
                    "input": {"product_asset_ids": plan["product_asset_ids"]},
                    "settings": {"main_image_speed": speed},
                    RECEIPTS_KEY: receipts or {},
                },
                expected_item_count=6,
            )
        )
        session.flush()
        session.add(
            JobItemRecord(
                id=item_id,
                workspace_id="ws_test",
                job_id=job_id,
                item_key="page-0",
                ordinal=0,
                status="running",
                quality="high",
            )
        )
        session.add(
            JobPlanRecord(
                id="p" + uuid4().hex,
                workspace_id="ws_test",
                job_id=job_id,
                revision=1,
                schema_version=schema_version,
                payload=deepcopy(plan),
                payload_hash=canonical_hash(plan),
                validation_status="valid",
                confirmed_at=utc_now(),
            )
        )
        session.flush()
        session.add(
            JobAttemptRecord(
                id=attempt_id,
                workspace_id="ws_test",
                job_id=job_id,
                job_item_id=item_id,
                attempt_no=1,
                status="running",
                lease_token="test-lease",
                leased_by="test",
                lease_expires_at=utc_now() + timedelta(minutes=5),
                operation="domestic_main_image",
                provider_idempotency_key="idem-" + attempt_id,
            )
        )
        session.commit()
    return ProviderRequest(
        workspace_id="ws_test",
        job_id=job_id,
        job_item_id=item_id,
        attempt_id=attempt_id,
        operation="domestic_main_image",
        idempotency_key="idem-" + attempt_id,
        configuration={},
    )


def receipt(client, request):
    with client.app.state.session_factory() as session:
        return deepcopy(
            session.get(JobRecord, request.job_id).configuration[RECEIPTS_KEY]
        )


def test_native_bytes_high_quality_source_identity_and_explicit_provider(
    client, monkeypatch
):
    provider, gateway, request, plan = setup(client, monkeypatch)
    result = asyncio.run(provider.execute(request))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert result.provider_name == gateway.provider_name
    assert result.model_name == gateway.image_model
    assert result.pricing_version == gateway.pricing_version
    assert result.actual_cost_micros is None
    assert result.raw_usage["review_status"] == "NOT_REVIEWED"
    assert result.raw_usage["technical_status"] == "STRUCTURALLY_VALIDATED"
    assert len(gateway.requests) == 1
    submitted = gateway.requests[0]
    assert submitted.quality == submitted.input_fidelity == "high"
    assert submitted.single_dispatch and submitted.preserve_provider_artifact
    assert submitted.size == "1024x1024"
    assert [source.source_role for source in submitted.sources] == ["product_reference"]
    assert submitted.prompt == image_prompt(plan, plan["pages"][0])
    saved = receipt(client, request)["0"]
    assert saved["state"] == "returned"
    assert saved["sha256"] == result.artifact.sha256
    assert (
        asyncio.run(client.app.state.storage.read_bytes(saved["object_key"]))
        == result.artifact.payload
    )


def test_submission_is_persisted_before_gateway_runs(client, monkeypatch):
    provider, gateway, request, _ = setup(client, monkeypatch)
    original_edit = gateway.edit

    async def observed_edit(submitted):
        saved = receipt(client, request)["0"]
        assert saved["state"] == "invoked"
        assert saved["attempt_id"] == request.attempt_id
        assert (
            saved["prompt_hash"]
            == hashlib.sha256(submitted.prompt.encode()).hexdigest()
        )
        return await original_edit(submitted)

    monkeypatch.setattr(gateway, "edit", observed_edit)
    assert asyncio.run(provider.execute(request)).outcome == ProviderOutcome.SUCCEEDED


def test_duplicate_execution_reuses_original_without_network_or_charge(
    client, monkeypatch
):
    provider, gateway, request, _ = setup(client, monkeypatch)
    first = asyncio.run(provider.execute(request))
    second = asyncio.run(provider.execute(request))
    assert first.artifact.payload == second.artifact.payload
    assert second.raw_usage["reused"] and second.raw_usage["network_requests"] == 0
    assert second.actual_cost_micros == 0 and len(gateway.requests) == 1


def test_delivery_failure_retry_reuses_original_across_metric_changes(
    client, monkeypatch
):
    provider, gateway, request, plan = setup(client, monkeypatch)
    first = asyncio.run(provider.execute(request))
    changed = {
        **plan,
        "state": "confirmed",
        "metrics": {"planning_reused": True},
        "cost": {},
    }
    retry = persist(
        client, changed, retry_of=request.job_id, receipts=receipt(client, request)
    )
    second = asyncio.run(provider.execute(retry))
    assert second.outcome == ProviderOutcome.SUCCEEDED
    assert second.artifact.sha256 == first.artifact.sha256
    assert len(gateway.requests) == 1


@pytest.mark.parametrize("outcome", ["timeout", ProviderOutcome.OUTCOME_UNKNOWN])
def test_unknown_submission_never_resubmits_in_original_or_retry(
    client, monkeypatch, outcome
):
    provider, gateway, request, plan = setup(
        client, monkeypatch, Gateway(outcome=outcome)
    )
    first = asyncio.run(provider.execute(request))
    assert first.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    second = asyncio.run(provider.execute(request))
    retry = persist(
        client, plan, retry_of=request.job_id, receipts=receipt(client, request)
    )
    third = asyncio.run(provider.execute(retry))
    assert second.error_code == third.error_code == "PROVIDER_OUTCOME_UNKNOWN"
    assert len(gateway.requests) == 1


def test_known_failure_allows_only_explicit_new_retry(client, monkeypatch):
    provider, gateway, request, plan = setup(
        client, monkeypatch, Gateway(outcome=ProviderOutcome.FAILED)
    )
    first = asyncio.run(provider.execute(request))
    second = asyncio.run(provider.execute(request))
    assert (
        first.error_code == "KNOWN_REJECTION"
        and second.error_code == "PROVIDER_OUTCOME_UNKNOWN"
    )
    retry = persist(
        client, plan, retry_of=request.job_id, receipts=receipt(client, request)
    )
    gateway.outcome = ProviderOutcome.SUCCEEDED
    third = asyncio.run(provider.execute(retry))
    assert third.outcome == ProviderOutcome.SUCCEEDED and len(gateway.requests) == 2


def test_invalid_native_canvas_kept_and_only_explicit_retry_regenerates(
    client, monkeypatch
):
    provider, gateway, request, plan = setup(
        client, monkeypatch, Gateway(size=(1024, 1200))
    )
    first = asyncio.run(provider.execute(request))
    saved = receipt(client, request)
    assert first.error_code == "FAST_MAIN_IMAGE_CANVAS_INVALID"
    assert saved["0"]["state"] == "rejected" and saved["0"]["object_key"]
    retry = persist(client, plan, retry_of=request.job_id, receipts=saved)
    gateway.size = (1024, 1024)
    second = asyncio.run(provider.execute(retry))
    assert second.outcome == ProviderOutcome.SUCCEEDED and len(gateway.requests) == 2


@pytest.mark.parametrize("mutate", ["scope", "hash", "skill", "source", "role"])
def test_scope_plan_skill_and_source_corruption_stop_before_paid_dispatch(
    client, monkeypatch, mutate
):
    provider, gateway, request, _ = setup(client, monkeypatch)
    if mutate == "scope":
        request = replace(request, workspace_id="other-workspace")
    elif mutate == "skill":
        monkeypatch.setattr(
            "app.aurora_main_image_fast_provider.BundledSkillManifestValidator.validate",
            lambda self, skill: "b" * 64,
        )
    else:
        from sqlalchemy import select

        with client.app.state.session_factory() as session:
            row = session.scalar(
                select(JobPlanRecord).where(JobPlanRecord.job_id == request.job_id)
            )
            changed = deepcopy(row.payload)
            if mutate == "hash":
                changed["style_lock"] = "Tampered direction"
            elif mutate == "source":
                changed["source_bindings"][0]["sha256"] = "b" * 64
            else:
                changed["pages"][0]["page_role"] = "WRONG_ROLE"
            row.payload = changed
            if mutate != "hash":
                row.payload_hash = canonical_hash(changed)
            session.commit()
    result = asyncio.run(provider.execute(request))
    assert result.outcome == ProviderOutcome.FAILED
    assert not gateway.requests


def test_cross_job_receipt_theft_and_frozen_prompt_changes_are_rejected(
    client, monkeypatch
):
    provider, gateway, request, plan = setup(client, monkeypatch)
    asyncio.run(provider.execute(request))
    stolen = persist(client, plan, receipts=receipt(client, request))
    assert (
        asyncio.run(provider.execute(stolen)).error_code
        == "FAST_MAIN_IMAGE_PLAN_CHANGED"
    )
    changed = deepcopy(plan)
    changed["pages"][0]["visual_plan"] = "A new expensive image prompt"
    retry = persist(
        client, changed, retry_of=request.job_id, receipts=receipt(client, request)
    )
    assert (
        asyncio.run(provider.execute(retry)).error_code
        == "FAST_MAIN_IMAGE_PLAN_CHANGED"
    )
    assert len(gateway.requests) == 1
    assert generation_hash(changed) != generation_hash(plan)


def test_original_and_saved_result_byte_integrity_block_paid_dispatch(
    client, monkeypatch
):
    provider, gateway, request, _ = setup(client, monkeypatch)
    read_original = client.app.state.storage.read_bytes

    async def broken_original(key):
        return b"changed-original"

    monkeypatch.setattr(client.app.state.storage, "read_bytes", broken_original)
    first = asyncio.run(provider.execute(request))
    assert first.error_code == "FAST_MAIN_IMAGE_SOURCE_CHANGED" and not gateway.requests
    monkeypatch.setattr(client.app.state.storage, "read_bytes", read_original)
    assert asyncio.run(provider.execute(request)).outcome == ProviderOutcome.SUCCEEDED

    async def broken_result(key):
        return (
            b"changed-result"
            if "/main-image-fast/" in key
            else await read_original(key)
        )

    monkeypatch.setattr(client.app.state.storage, "read_bytes", broken_result)
    second = asyncio.run(provider.execute(request))
    assert second.error_code == "FAST_MAIN_IMAGE_RESULT_CHANGED"
    assert len(gateway.requests) == 1
