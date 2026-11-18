import asyncio
import hashlib
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from jsonschema import ValidationError
from PIL import Image
from sqlalchemy import select

from app.image_fidelity import (
    CHECKS,
    PREPARE,
    READBACK,
    FidelityGateway,
    readback_matches,
    selection_constraint,
    technical_contract,
    validate_preparation,
    validate_review,
)
from app.models import (
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    utc_now,
)
from app.provider import ProviderArtifact, ProviderOutcome
from app.xinghe_agent import (
    ImageCallResult,
    ImageEditRequest,
    PlannerCallResult,
    SourceImage,
)


def review():
    return {
        "checks": {k: {"passed": True, "evidence": "visible match"} for k in CHECKS},
        "unresolved": [],
        "observable_defects": [],
    }


def preparation():
    return {
        "selected_regions": [
            {
                "asset_id": "product",
                "box": [0, 0.5, 1, 1],
                "identity": "lower selected object",
                "visible_print": ["モモンガ"],
            }
        ],
        "protected_attributes": ["original short sleeves"],
        "allowed_changes": ["person identity"],
        "selection_summary": "lower object only",
    }


@pytest.mark.parametrize("check", ["physical_artwork_text", "protected_attributes"])
def test_changed_print_or_sleeves_rejected(check):
    value = review()
    value["checks"][check]["passed"] = False
    assert validate_review(value) == (False, [check])


@pytest.mark.parametrize("field", ["unresolved", "observable_defects"])
def test_all_true_cannot_override_unresolved_evidence(field):
    value = review()
    value[field] = ["Japanese lettering differs from source"]
    assert validate_review(value) == (False, ["unresolved_evidence"])


@pytest.mark.parametrize("transcription", ["モモソガ", "ちいかわ", "UNREADABLE"])
def test_independent_literal_readback_rejects_wrong_or_unknown_print(transcription):
    assert readback_matches({"text_runs": [transcription]}, preparation()) is False
    assert readback_matches({"text_runs": ["モ\nモ\nン\nガ"]}, preparation()) is True


def test_all_true_visual_review_cannot_override_candidate_only_ocr():
    class BlindReaderGateway(Gateway):
        async def plan(self, req):
            self.plans.append(req)
            value = preparation() if req.planning_profile == PREPARE else review()
            if req.planning_profile == READBACK:
                assert len(req.sources) == 1 and req.sources[0].asset_id == "candidate"
                assert "モモンガ" not in req.system_prompt
                assert "モモンガ" not in str(req.product_information)
                value = {"text_runs": ["ちいかわ"]}
            return PlannerCallResult(value, "reader", {}, {"reported": True}, 0.1, 2)

    gateway = BlindReaderGateway()
    result = asyncio.run(ScopedFidelity(gateway, None, None).edit(request(gateway)))
    assert result.error_code == "IMAGE_FIDELITY_REJECTED"
    assert result.raw_usage["metrics"]["image_fidelity"]["failed_checks"] == [
        "physical_text_readback"
    ]
    assert len(gateway.edits) == 1 and len(gateway.plans) == 3


@pytest.mark.parametrize("change", ["style_authority", "empty_box", "out_of_bounds"])
def test_selection_cannot_use_style_or_invalid_geometry(change):
    value = preparation()
    region = value["selected_regions"][0]
    if change == "style_authority":
        region["asset_id"] = "style"
    else:
        region["box"] = [0, 0, 0, 1] if change == "empty_box" else [-0.1, 0, 1, 1]
    with pytest.raises((ValueError, ValidationError)):
        validate_preparation(value, ("product",))


@pytest.mark.parametrize(
    "instruction,direction",
    [
        ("只需要下面的3个，不改变图案", "bottom"),
        ("保留上方的三个", "top"),
        ("只要左侧3件", "left"),
        ("选右边的三款", "right"),
    ],
)
def test_explicit_spatial_selection_is_verified_against_full_source_inventory(
    instruction, direction
):
    constraint = selection_constraint(instruction)
    assert constraint == {"direction": direction, "count": 3}
    value = preparation()
    inventory = []
    for row in range(2):
        for col in range(3):
            region = deepcopy(value["selected_regions"][0])
            region.update(
                box=[col / 3, row / 2, (col + 0.8) / 3, (row + 0.8) / 2],
                identity=f"original product {row}-{col}",
            )
            inventory.append(region)
    value["source_inventory"] = inventory
    axis = 1 if direction in {"bottom", "top"} else 0
    ordered = sorted(
        inventory,
        key=lambda r: r["box"][axis] + r["box"][axis + 2],
        reverse=direction in {"bottom", "right"},
    )
    value["selected_regions"] = ordered[:3]
    assert validate_preparation(value, ("product",), constraint) == value
    value["selected_regions"] = ordered[-3:]
    with pytest.raises(ValueError, match="spatial subset"):
        validate_preparation(value, ("product",), constraint)


def test_unverified_spatial_selection_stops_before_any_paid_image():
    class SpatialFidelity(ScopedFidelity):
        def _context(self, request):
            return {
                "job_type": "creative_image",
                "settings": {"instruction": "只需要下面的3个"},
            }

    gateway = Gateway()
    result = asyncio.run(SpatialFidelity(gateway, None, None).edit(request(gateway)))
    assert result.outcome == ProviderOutcome.FAILED
    assert gateway.edits == []
    assert "只需要下面的3个" in gateway.plans[0].system_prompt


class Gateway:
    provider_name = "synthetic"
    image_model = "synthetic-image"
    pricing_version = "test"

    def __init__(self, *, outcome=ProviderOutcome.SUCCEEDED, malformed=False):
        self.outcome, self.malformed = outcome, malformed
        self.edits, self.plans = [], []
        buf = BytesIO()
        Image.new("RGB", (40, 40), "red").save(buf, "PNG")
        payload = buf.getvalue()
        self.artifact = ProviderArtifact(
            payload=payload,
            filename="candidate.png",
            content_type="image/png",
            width=40,
            height=40,
            sha256=hashlib.sha256(payload).hexdigest(),
        )

    async def plan(self, request):
        self.plans.append(request)
        value = preparation() if request.planning_profile == PREPARE else review()
        if request.planning_profile != PREPARE:
            if self.malformed:
                value = {"checks": {}}
            else:
                value["checks"]["protected_attributes"]["passed"] = False
        return PlannerCallResult(value, "review", {}, {"reported": True}, 0.1, 2)

    async def edit(self, request):
        self.edits.append(request)
        return ImageCallResult(
            self.outcome,
            "original-task",
            {"network_requests": 1},
            {"reported": True},
            1,
            10,
            artifact=self.artifact
            if self.outcome == ProviderOutcome.SUCCEEDED
            else None,
        )


class ScopedFidelity(FidelityGateway):
    def _frozen(self, request, value=None):
        if value is not None:
            self.frozen = value
        return getattr(self, "frozen", None)

    def _context(self, request):
        return {"job_type": "product_image", "settings": {"instruction": "换模特"}}

    async def _retain(self, request, artifact):
        self.retained = artifact
        return "private-candidate"


def request(gateway):
    artifact = gateway.artifact
    return ImageEditRequest(
        "workspace",
        "job",
        "item",
        "attempt",
        1,
        "group",
        (
            SourceImage(
                "product",
                "group",
                "source.png",
                "image/png",
                artifact.sha256,
                artifact.payload,
                source_role="product_reference",
            ),
        ),
        "Change the person; retain source clothes",
        "high",
        "same-key",
        "40x40",
    )


@pytest.mark.parametrize("malformed,review_calls", [(False, 1), (True, 2)])
def test_rejected_or_malformed_review_never_regenerates(malformed, review_calls):
    gateway = Gateway(malformed=malformed)
    audited = ScopedFidelity(gateway, None, None)
    result = asyncio.run(audited.edit(request(gateway)))
    assert result.error_code == "IMAGE_FIDELITY_REJECTED"
    assert result.artifact is None and result.retryable is False
    assert len(gateway.edits) == 1
    assert len(gateway.plans) == 1 + review_calls
    assert audited.retained.sha256 == gateway.artifact.sha256
    assert result.actual_cost_micros == 10 + 2 * (1 + review_calls)
    assert len({p.idempotency_key for p in gateway.plans}) == len(gateway.plans)
    assert gateway.edits[0].sources[0].payload == request(gateway).sources[0].payload
    assert gateway.edits[0].sources[1].source_role == "product_detail"


def test_reconciliation_keeps_original_request_and_no_preparation():
    gateway = Gateway(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    audited = ScopedFidelity(gateway, None, None)
    original = replace(request(gateway), reconcile_only=True)
    result = asyncio.run(audited.edit(original))
    assert result.outcome == ProviderOutcome.OUTCOME_UNKNOWN
    assert gateway.edits == [original]
    assert gateway.plans == []


def test_new_guard_reconciliation_reconstructs_frozen_prompt_and_crops():
    gateway = Gateway(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    audited = ScopedFidelity(gateway, None, None)
    original = request(gateway)
    asyncio.run(audited.edit(original))
    sent = gateway.edits[0]
    frozen = audited.frozen
    restarted = ScopedFidelity(gateway, None, None)
    restarted.frozen = frozen
    asyncio.run(restarted.edit(replace(original, reconcile_only=True)))
    assert len(gateway.plans) == 1
    assert replace(gateway.edits[1], reconcile_only=False) == sent


def test_missing_review_boolean_never_passes():
    value = review()
    del value["checks"]["canvas"]["passed"]
    with pytest.raises(ValidationError):
        validate_review(value)


def test_cached_original_review_never_generates_a_replacement():
    gateway = Gateway()
    audited = ScopedFidelity(gateway, None, None)
    original = request(gateway)
    result = asyncio.run(
        audited.review_existing(
            original,
            sources=original.sources,
            prompt=original.prompt,
            size=original.size,
            artifact=gateway.artifact,
        )
    )
    assert result.error_code == "IMAGE_FIDELITY_REJECTED"
    assert gateway.edits == [] and len(gateway.plans) == 1
    assert result.actual_cost_micros == 2
    assert result.raw_usage["network_requests"] == 1


@pytest.mark.parametrize(
    "key,size,target,valid",
    [
        ("MAIN-01", (1254, 1254), "2000x2000", True),
        ("MAIN-01", (1023, 1023), "2000x2000", False),
        ("A-DESKTOP-04", (1464, 600), "1464x600", True),
        ("A-DESKTOP-04", (1536, 630), "1464x600", True),
        ("A-DESKTOP-04", (1536, 1024), "1464x600", False),
        ("A-DESKTOP-04", (1464, 599), "1464x600", False),
    ],
)
def test_amazon_review_uses_unchanged_native_canvas_contract(key, size, target, valid):
    gateway = Gateway()
    buf = BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    data = buf.getvalue()
    artifact = replace(
        gateway.artifact,
        payload=data,
        width=size[0],
        height=size[1],
        sha256=hashlib.sha256(data).hexdigest(),
    )
    result = technical_contract(
        {"job_type": "amazon_visual_suite", "item_key": key},
        replace(request(gateway), size=target),
        artifact,
    )
    assert result["server_technical_checks_passed"] is valid


def test_durable_binding_and_private_candidate_survive_restart(client):
    from tests.test_api import WORKSPACE_ID, upload_asset

    source = upload_asset(client)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "type": "product_image",
            "intent": "submit",
            "input": {"product_asset_ids": [source["id"]]},
            "configuration": {
                "task_type": "change_background",
                "image_count": 1,
                "instruction": "仅更换背景，保留商品",
            },
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    sessions, storage = client.app.state.session_factory, client.app.state.storage
    gateway = Gateway()
    with sessions() as session:
        job = session.get(JobRecord, job_id)
        job.status = "processing"
        item = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job_id)
        )
        assert item is not None
        item.status = "processing"
        attempt_id = "attempt_" + uuid4().hex
        session.add(
            JobAttemptRecord(
                id=attempt_id,
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                job_item_id=item.id,
                attempt_no=1,
                status="running",
                lease_token="test",
                leased_by="test",
                lease_expires_at=utc_now() + timedelta(minutes=1),
                provider="test",
                operation="product_image",
                provider_idempotency_key="same-key",
            )
        )
        item_id = item.id
        session.commit()
    call = replace(
        request(gateway),
        workspace_id=WORKSPACE_ID,
        job_id=job_id,
        job_item_id=item_id,
        attempt_id=attempt_id,
    )
    audited = FidelityGateway(gateway, sessions, storage)
    assert audited._frozen(call) is None
    value = {"preparation": preparation(), "prompt": "Frozen complete instruction"}
    saved = audited._frozen(call, value)
    restarted = FidelityGateway(gateway, sessions, storage)
    assert restarted._frozen(call) == saved
    with pytest.raises(ValueError, match="changed"):
        restarted._frozen(replace(call, prompt="changed"))
    with pytest.raises(ValueError, match="active|scope"):
        restarted._frozen(replace(call, workspace_id="foreign"))
    asset_id = asyncio.run(restarted._retain(call, gateway.artifact))
    assert asyncio.run(restarted._retain(call, gateway.artifact)) == asset_id
    with sessions() as session:
        relation = session.scalar(
            select(JobAssetRecord).where(
                JobAssetRecord.asset_id == asset_id,
            )
        )
        assert relation is not None
        assert relation.direction == "intermediate" and relation.is_deliverable is False
    with pytest.raises(ValueError, match="scope"):
        asyncio.run(
            restarted._retain(replace(call, workspace_id="foreign"), gateway.artifact)
        )
