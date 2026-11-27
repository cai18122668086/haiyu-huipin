import asyncio
import hashlib
import json
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
    BACKGROUND,
    CHECKS,
    MATTE,
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


@pytest.mark.parametrize(
    "runs,accepted",
    [
        (["12+", "ROSE & LILY", "12+", "MINT & SAGE"], True),
        (["12+", "ROSE LILY", "12+", "MINT & SAGE"], False),
        (["12+", "ROSE & LILV", "12+", "MINT & SAGE"], False),
        (["12+", "ROSE & LILY", "MINT & SAGE"], False),
        (["12+", "ROSE & LILY 12+", "MINT & SAGE"], False),
        (["12+", "ROSE & SAGE", "12+", "MINT & LILY"], False),
    ],
)
def test_ocr_word_grouping_preserves_each_products_complete_print(runs, accepted):
    groups = [["12+", "ROSE", "&", "LILY"], ["12+", "MINT", "&", "SAGE"]]
    source = {
        "selected_regions": [{"visible_print": group} for group in groups],
        "literal_print_ledger": groups[0] + groups[1],
    }
    assert readback_matches({"text_runs": runs}, source) is accepted


def test_character_name_cannot_replace_original_copyright():
    value = preparation()
    value["literal_print_ledger"] = ["モモンガ", "©nagano"]
    assert not readback_matches({"text_runs": ["モモンガ", "Momonga"]}, value)
    assert readback_matches({"text_runs": ["モモンガ", "©nagano"]}, value)
    with pytest.raises(ValidationError):
        validate_preparation(value, ("product",))
    assert validate_preparation(value, ("product",), allow_stored=True) == value


def test_repeated_physical_print_cannot_be_deduplicated_by_the_candidate_reader():
    value = preparation()
    value["literal_print_ledger"] = ["ちいかわ", "ちいかわ", "©nagano"]
    assert not readback_matches({"text_runs": ["ちいかわ", "©nagano"]}, value)
    assert readback_matches(
        {"text_runs": ["ちいかわ", "ちいかわ", "©nagano"]}, value
    )


def test_unreadable_annotation_is_a_contract_error_not_a_literal_print_string():
    value = preparation()
    value["selected_regions"][0]["visible_print"] = ["底部版权小字：UNREADABLE"]
    with pytest.raises(ValidationError):
        validate_preparation(value, ("product",))
    value["selected_regions"][0]["visible_print"] = ["UNREADABLE"]
    assert validate_preparation(value, ("product",)) == value


def test_all_true_visual_review_cannot_override_candidate_only_ocr():
    class BlindReaderGateway(Gateway):
        async def plan(self, req):
            self.plans.append(req)
            value = preparation() if req.planning_profile == PREPARE else review()
            if req.planning_profile == READBACK:
                assert len(req.sources) == 1
                assert "モモンガ" not in req.system_prompt
                assert "モモンガ" not in str(req.product_information)
                value = {
                    "text_runs": [
                        "ちいかわ"
                        if req.sources[0].asset_id == "candidate"
                        else "モモンガ"
                    ]
                }
            return PlannerCallResult(value, "reader", {}, {"reported": True}, 0.1, 2)

    gateway = BlindReaderGateway()
    result = asyncio.run(ScopedFidelity(gateway, None, None).edit(request(gateway)))
    assert result.error_code == "IMAGE_FIDELITY_REJECTED"
    assert result.raw_usage["metrics"]["image_fidelity"]["failed_checks"] == [
        "physical_text_readback"
    ]
    assert len(gateway.edits) == 1 and len(gateway.plans) == 4


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
        ("只使用下排三个", "bottom"),
        ("只使用上排3个", "top"),
        ("只要左侧3件", "left"),
        ("选右边的三款", "right"),
        ("Use only the bottom three pictured charms", "bottom"),
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
    wire = {k: deepcopy(v) for k, v in value.items() if k != "selected_regions"}
    wire["source_inventory"] = [
        {**r, "object_id": f"object-{i}"} for i, r in enumerate(inventory)
    ]
    wire["selected_object_ids"] = [f"object-{inventory.index(r)}" for r in ordered[:3]]
    assert validate_preparation(wire, ("product",), constraint) == value
    assert (
        validate_preparation(value, ("product",), constraint, allow_stored=True)
        == value
    )
    wire["selected_object_ids"] = [f"object-{inventory.index(r)}" for r in ordered[-3:]]
    with pytest.raises(ValidationError, match="spatial subset"):
        validate_preparation(wire, ("product",), constraint)


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
        if request.planning_profile == READBACK:
            value = {"text_runs": ["モモンガ"]}
        elif request.planning_profile != PREPARE:
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


@pytest.mark.parametrize("corrected", [False, True])
def test_preparation_contract_correction_is_bounded_before_image_dispatch(
    corrected,
):
    class AnnotatedPreparation(Gateway):
        async def plan(self, req):
            if req.planning_profile != PREPARE:
                return await super().plan(req)
            self.plans.append(req)
            value = preparation()
            if len(self.plans) == 1 or not corrected:
                value["selected_regions"][0]["visible_print"] = [
                    "small text: UNREADABLE"
                ]
            return PlannerCallResult(value, "source", {}, {"reported": True}, 0.1, 2)

    gateway = AnnotatedPreparation()
    guard = ScopedFidelity(gateway, None, None)
    result = asyncio.run(guard.edit(request(gateway)))
    prepare_calls = [p for p in gateway.plans if p.planning_profile == PREPARE]
    assert len(prepare_calls) == 2
    assert prepare_calls[0].sources == prepare_calls[1].sources
    assert prepare_calls[0].idempotency_key != prepare_calls[1].idempotency_key
    correction = json.loads(prepare_calls[1].product_information[0])
    assert correction["contract_feedback"]["diagnostic"]["path"] == (
        "/selected_regions/0/visible_print/0"
    )
    assert len(gateway.edits) == int(corrected)
    assert result.error_code == (
        "IMAGE_FIDELITY_REJECTED" if corrected else "IMAGE_FIDELITY_PREPARATION_FAILED"
    )
    assert result.actual_cost_micros == (
        2 * len(gateway.plans) + (10 if corrected else 0)
    )
    if not corrected:
        again = asyncio.run(guard.edit(request(gateway)))
        assert again.error_code == "IMAGE_FIDELITY_PREPARATION_FAILED"
        assert len(gateway.plans) == 2 and not gateway.edits


def test_invalid_selection_geometry_is_corrected_with_the_original_sources():
    class InvertedBoxGateway(Gateway):
        async def plan(self, call):
            if self.plans or call.planning_profile != PREPARE:
                return await super().plan(call)
            self.plans.append(call)
            value = preparation()
            value["selected_regions"][0]["box"] = [0.8, 0.5, 0.1, 1]
            return PlannerCallResult(value, "source", {}, {"reported": True}, 0.1, 2)

    gateway = InvertedBoxGateway()
    asyncio.run(ScopedFidelity(gateway, None, None).edit(request(gateway)))
    first, correction = gateway.plans[:2]
    assert first.sources == correction.sources
    assert first.idempotency_key != correction.idempotency_key
    context = json.loads(correction.product_information[0])
    diagnostic = context["contract_feedback"]["diagnostic"]
    assert diagnostic["path"] == "/selected_regions/0/box"
    assert diagnostic["constraint"] == "invalid_object_bounds"
    assert len(gateway.edits) == 1


@pytest.mark.parametrize("focused_matches", [True, False, None])
def test_creative_print_check_uses_new_evidence_and_remains_fail_closed(
    focused_matches,
):
    from app.provider_diagnostics import retained_metrics

    class CreativeGuard(ScopedFidelity):
        def _context(self, call):
            return {"job_type": "creative_image", "settings": {"instruction": "换背景"}}

    class CreativeGateway(Gateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == PREPARE:
                quad = [0.2, 0.6, 0.8, 0.6, 0.8, 0.9, 0.2, 0.9]
                result.payload["selected_regions"][0]["print_regions"] = [
                    quad * (2 if focused_matches is None else 1),
                ]
            if (
                call.planning_profile == READBACK
                and call.idempotency_key.endswith(":source")
                and focused_matches is not None
            ):
                result.payload["text_runs"] = ["モモンガ", "UNREADABLE"]
            if (
                call.planning_profile == READBACK
                and call.idempotency_key.endswith(":source-physical-print-regions")
                and not focused_matches
            ):
                result.payload["text_runs"] = ["another product"]
            return result

    gateway = CreativeGateway()
    result = asyncio.run(CreativeGuard(gateway, None, None).edit(request(gateway)))
    readbacks = [
        call for call in gateway.plans if call.planning_profile == READBACK
    ]
    if focused_matches is None:
        assert len(gateway.edits) == 1
        assert not any(
            call.idempotency_key.endswith(":source-physical-print-regions")
            for call in readbacks
        )
        return
    first, focused, *_ = readbacks
    assert focused.sources[0].sha256 != first.sources[0].sha256
    assert json.loads(focused.product_information[0]).keys() == {"schema"}
    assert len(gateway.edits) == int(focused_matches)
    if not focused_matches:
        assert result.error_code == "IMAGE_FIDELITY_PREPARATION_FAILED"
        metrics = retained_metrics(result.raw_usage)
        assert metrics["image_fidelity"]["preparation_stage"] == "source_readback"
        assert metrics["image_fidelity"]["image_requests"] == 0
        assert metrics["image_fidelity"]["planning_requests"] == 3


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


@pytest.mark.parametrize(
    "error,repeats,expected_calls",
    [
        ("PLANNER_OUTPUT_TRUNCATED", False, 2),
        ("PLANNER_OUTPUT_TRUNCATED", True, 2),
        ("PLANNER_TRANSPORT_OUTCOME_UNKNOWN", False, 1),
    ],
)
def test_only_known_preparation_truncation_gets_one_complete_schema_correction(
    error, repeats, expected_calls
):
    class IncompleteSource(Gateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == PREPARE:
                count = sum(p.planning_profile == PREPARE for p in self.plans)
                if count == 1 or repeats:
                    return replace(result, payload=None, error_code=error)
            return result

    gateway = IncompleteSource()
    result = asyncio.run(ScopedFidelity(gateway, None, None).edit(request(gateway)))
    source_calls = [p for p in gateway.plans if p.planning_profile == PREPARE]
    assert len(source_calls) == expected_calls
    if expected_calls == 2:
        assert source_calls[0].sources == source_calls[1].sources
        feedback = json.loads(source_calls[1].product_information[0])[
            "contract_feedback"
        ]
        assert feedback["diagnostic"]["reason"] == "known_output_truncation"
    assert len(gateway.edits) == int(
        not repeats and error == "PLANNER_OUTPUT_TRUNCATED"
    )
    assert result.actual_cost_micros == 2 * len(gateway.plans) + 10 * len(gateway.edits)


@pytest.mark.parametrize(
    "status,receipt,expected_calls",
    [(502, "original-plan", 2), (502, None, 1), (400, "original-plan", 1)],
)
def test_only_acknowledged_transient_source_planning_repeats(
    status, receipt, expected_calls
):
    class SourcePlanner(Gateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == PREPARE and len(self.plans) == 1:
                return replace(
                    result,
                    payload=None,
                    provider_request_id=receipt,
                    error_code="PLANNER_REQUEST_FAILED",
                    http_status=status,
                )
            return result

    gateway = SourcePlanner()
    result = asyncio.run(ScopedFidelity(gateway, None, None).edit(request(gateway)))
    calls = [call for call in gateway.plans if call.planning_profile == PREPARE]
    assert len(calls) == expected_calls
    assert len(gateway.edits) == int(expected_calls == 2)
    if len(calls) == 2:
        assert calls[0].sources == calls[1].sources
        assert calls[0].product_information == calls[1].product_information
        assert calls[0].idempotency_key != calls[1].idempotency_key
    else:
        from app.provider_diagnostics import public_failure_message, retained_metrics

        details = retained_metrics(result.raw_usage)
        assert details["image_fidelity"]["planner_http_status"] == status
        if status == 400:
            message = public_failure_message(
                result.error_code, result.error_message, details
            )
            assert message and "商品识别服务拒绝" in message
    assert result.actual_cost_micros == 2 * len(gateway.plans) + 10 * len(gateway.edits)


def test_unknown_source_planning_is_not_repeated_by_another_suite_item():
    class UnknownSource(Gateway):
        async def plan(self, call):
            result = await super().plan(call)
            return replace(
                result, payload=None, error_code="PLANNER_TRANSPORT_OUTCOME_UNKNOWN"
            )

    gateway = UnknownSource()
    guard = ScopedFidelity(gateway, None, None)
    original = request(gateway)
    first = asyncio.run(guard.edit(original))
    second = asyncio.run(guard.edit(replace(
        original, job_item_id="different-suite-item", idempotency_key="different-key"
    )))
    assert first.error_code == second.error_code == "IMAGE_FIDELITY_PREPARATION_FAILED"
    assert len(gateway.plans) == 1 and gateway.edits == []
    assert first.actual_cost_micros == 2 and second.actual_cost_micros == 0


@pytest.mark.parametrize("malformed,review_calls", [(False, 1), (True, 2)])
def test_rejected_or_malformed_review_never_regenerates(malformed, review_calls):
    gateway = Gateway(malformed=malformed)
    audited = ScopedFidelity(gateway, None, None)
    result = asyncio.run(audited.edit(request(gateway)))
    assert result.error_code == "IMAGE_FIDELITY_REJECTED"
    assert result.artifact is None and result.retryable is False
    assert len(gateway.edits) == 1
    assert len(gateway.plans) == 2 + review_calls
    assert audited.retained.sha256 == gateway.artifact.sha256
    assert result.actual_cost_micros == 10 + 2 * (2 + review_calls)
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
    assert len(gateway.plans) == 2
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
    assert gateway.edits == [] and len(gateway.plans) == 3
    assert result.actual_cost_micros == 6
    assert result.raw_usage["network_requests"] == 3


def test_cached_all_true_review_cannot_bypass_physical_text_readback():
    class CachedReader(Gateway):
        async def plan(self, call):
            self.plans.append(call)
            value = preparation() if call.planning_profile == PREPARE else review()
            if call.planning_profile == READBACK:
                value = {
                    "text_runs": [
                        "ちいかわ"
                        if call.sources[0].asset_id == "candidate"
                        else "モモンガ"
                    ]
                }
            return PlannerCallResult(value, "reader", {}, {"reported": True}, 0.1, 2)

    gateway = CachedReader()
    call = request(gateway)
    result = asyncio.run(
        ScopedFidelity(gateway, None, None).review_existing(
            call,
            sources=call.sources,
            prompt=call.prompt,
            size=call.size,
            artifact=gateway.artifact,
        )
    )
    assert result.error_code == "IMAGE_FIDELITY_REJECTED"
    assert gateway.edits == []
    assert result.raw_usage["metrics"]["image_fidelity"]["failed_checks"] == [
        "physical_text_readback"
    ]


def test_native_child_requires_exact_signed_key_and_parent_scope(monkeypatch):
    from app.domestic_detail.generation import wire_idempotency_key

    class NativeScope(ScopedFidelity):
        def _context(self, call):
            return {"job_type": "detail_pack", "settings": {}}

    gateway = PixelGateway()
    guard = NativeScope(gateway, None, None)
    parent = request(gateway)
    signed = replace(
        parent,
        idempotency_key=wire_idempotency_key(parent.idempotency_key, "signed-token"),
    )
    captured = []

    async def inspect_only(bound, call):
        captured.append((bound._parent_key, bound._native_pair, call))
        return "scoped"

    monkeypatch.setattr(FidelityGateway, "edit", inspect_only)
    assert (
        asyncio.run(guard.edit_native(signed, parent, token="signed-token")) == "scoped"
    )
    assert captured == [(parent.idempotency_key, True, signed)]
    evidence_calls = []

    async def evidence_only(bound, call, candidate):
        evidence_calls.append((bound._parent_key, bound._native_pair, call, candidate))
        return ("exact-source-comparison",)

    monkeypatch.setattr(FidelityGateway, "paired_source_evidence", evidence_only)
    assert asyncio.run(guard.native_source_evidence(
        signed, parent, token="signed-token", candidate=gateway.artifact
    )) == ("exact-source-comparison",)
    assert evidence_calls == [
        (parent.idempotency_key, True, signed, gateway.artifact)
    ]
    for bad in (
        replace(signed, workspace_id="foreign"),
        replace(signed, attempt_id="other"),
        replace(signed, idempotency_key="guessed-key"),
    ):
        with pytest.raises(ValueError, match="binding"):
            asyncio.run(guard.edit_native(bad, parent, token="signed-token"))
        with pytest.raises(ValueError, match="binding"):
            asyncio.run(guard.native_source_evidence(
                bad, parent, token="signed-token", candidate=gateway.artifact
            ))
    assert len(evidence_calls) == 1
    assert len(captured) == 1 and gateway.edits == []


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
    evidence = restarted.selected_source_evidence(call)
    assert len(evidence) == 1
    assert evidence[0].source_role == "product_authority_detail"
    assert hashlib.sha256(evidence[0].payload).hexdigest() == evidence[0].sha256
    with pytest.raises(ValueError, match="bytes changed"):
        restarted.selected_source_evidence(
            replace(
                call,
                sources=(
                    replace(
                        call.sources[0], payload=call.sources[0].payload + b"tampered"
                    ),
                ),
            )
        )
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
    # Signed child calls share the actual durable parent authority, while their
    # immutable recipes and evidence remain distinct even in the same attempt.
    from app.domestic_detail.generation import wire_idempotency_key

    bound = FidelityGateway(gateway, sessions, storage)
    bound._parent_key = call.idempotency_key
    children = [
        replace(call, idempotency_key=wire_idempotency_key(call.idempotency_key, token))
        for token in ("signed-a", "signed-b")
    ]
    ids = []
    for child in children:
        with pytest.raises(ValueError, match="scope"):
            restarted._frozen(child)
        bound._frozen(child, value)
        ids.append(
            asyncio.run(
                bound._retain(
                    child, gateway.artifact, role="fidelity_source_cutout", ordinal=0
                )
            )
        )
        assert (
            asyncio.run(
                bound._retain(
                    child, gateway.artifact, role="fidelity_source_cutout", ordinal=0
                )
            )
            == ids[-1]
        )
    assert len(set(ids)) == 2
    with sessions() as session:
        records = session.scalars(
            select(JobAssetRecord).where(JobAssetRecord.asset_id.in_(ids))
        ).all()
        assert sorted(row.ordinal for row in records) == [0, 1]
        assert len({row.validation["fidelity_receipt"] for row in records}) == 2


class PixelGateway(Gateway):
    image_fidelity_source_pixels_enabled = True
    image_fidelity_source_model_path = None

    async def plan(self, call):
        self.plans.append(call)
        if call.planning_profile == PREPARE:
            payload = preparation()
        elif call.planning_profile == MATTE:
            payload = {
                name: True
                for name in (
                    "complete_objects",
                    "no_context_leak",
                    "no_clipped_print",
                    "edges_clean",
                )
            } | {
                "unresolved": [],
                "evidence": "Original complete source product",
                "repairs": [],
                "edge_feather_px": 0,
            }
        elif call.planning_profile == "image_fidelity_source_window":
            rows = json.loads(call.product_information[0])["placements_in_image_order"]
            payload = {"crops": [
                {"placement": row["index"], "window": row["source_window"],
                 "verdict": "matches", "evidence": "Synthetic approved crop"}
                for row in rows
            ]}
        elif call.planning_profile == BACKGROUND:
            payload = {
                "background_description": "Pure white empty background",
                "marketing_copy": [],
                "text_layout": [],
            }
        else:
            assert call.planning_profile != READBACK
            payload = review()
        return PlannerCallResult(payload, "review", {}, {"reported": True}, 0.1, 2)

    async def edit(self, call):
        from app.source_pixels import artifact

        self.edits.append(call)
        assert call.sources[0].asset_id == "source-pixel-format"
        assert "BACKGROUND PLATE ONLY" in call.prompt
        assert "モモンガ" not in call.prompt
        return ImageCallResult(
            self.outcome,
            "original-background-task",
            {"network_requests": 1},
            {"reported": True},
            1,
            10,
            artifact=artifact(Image.new("RGB", (40, 40), "blue"), "background.png")
            if self.outcome == ProviderOutcome.SUCCEEDED
            else None,
        )


class PixelGuard(ScopedFidelity):
    def __init__(self, *args):
        super().__init__(*args)
        self.evidence = {}

    def _context(self, request):
        return {
            "job_type": "product_image",
            "item_key": "image-001",
            "settings": {
                "task_type": "white_background",
                "instruction": "白底保留原图案",
            },
        }

    async def _retain(
        self, request, artifact, *, role="fidelity_candidate", ordinal=None
    ):
        key = role + str(ordinal)
        self.evidence[key] = artifact
        return key

    async def _source_layers(self, request, recipe, sources):
        return tuple(self.evidence[row["asset_id"]] for row in recipe["layers"])


def pixel_extraction(sources, preparation, model):
    from app.source_pixels import PixelLayer, artifact

    with Image.open(BytesIO(sources[0].payload)) as im:
        layer = artifact(im.convert("RGBA"), "source-original.png")
    return (PixelLayer(layer, sources[0].asset_id, sources[0].sha256, (0, 0, 40, 40)),)


@pytest.mark.parametrize("instruction", [
    "换背景，保留原商品图案", "增加图案桌布背景，不额外增加内容",
])
def test_creative_pixel_path_does_not_require_readback_crop_geometry(
    monkeypatch, instruction,
):
    from app import source_pixels

    class CreativePixelGuard(PixelGuard):
        def _context(self, call):
            return {
                "job_type": "creative_image",
                "item_key": "image-001",
                "settings": {"instruction": instruction},
            }

    monkeypatch.setattr(source_pixels, "extract_layers", pixel_extraction)
    gateway = PixelGateway()
    result = asyncio.run(CreativePixelGuard(gateway, None, None).edit(request(gateway)))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    selection_call = gateway.plans[0]
    assert selection_call.planning_profile == PREPARE
    content = json.loads(selection_call.product_information[0])
    fields = content["schema"]["properties"]["selected_regions"]["items"]["properties"]
    assert "print_regions" not in fields
    assert all(call.planning_profile != READBACK for call in gateway.plans)


@pytest.mark.parametrize("mask_accepted", [True, False, "repair"])
def test_local_silver_render_preserves_original_and_never_submits_an_image(
    mask_accepted,
):
    from app import source_pixels, source_recolor

    class GoldGateway(Gateway):
        image_fidelity_source_pixels_enabled = True

        async def plan(self, call):
            self.plans.append(call)
            if call.planning_profile == PREPARE:
                payload = preparation()
                payload["selected_regions"][0]["print_regions"] = [
                    [0.1, 0.6, 0.9, 0.6, 0.9, 0.9, 0.1, 0.9]
                ]
            elif call.planning_profile == READBACK:
                payload = {"text_runs": ["モモンガ"]}
            elif call.planning_profile == source_recolor.PROFILE:
                box = [0.2, 0.2, 0.35, 0.35]
                if mask_accepted == "repair":
                    context = json.loads(call.product_information[0])
                    if call.idempotency_key.endswith(":geometry-repair"):
                        violation = context["selection_feedback"]["violations"][0]
                        assert violation["region_index"] == 0
                        assert violation["reason"] == "protected_print"
                        assert violation["intersected_print_boxes"] == [
                            [0.1, 0.6, 0.9, 0.9]
                        ]
                    else:
                        box = [0.2, 0.55, 0.35, 0.68]
                payload = {
                    "pixel_inspected": bool(mask_accepted),
                    "unresolved": [],
                    "regions": [{"part": "metal_clasp", "box": box}],
                }
            else:
                payload = review()
            return PlannerCallResult(payload, "offline", {}, {"reported": True}, 0.1, 2)

    class GoldGuard(ScopedFidelity):
        def _context(self, call):
            return {
                "job_type": "product_image",
                "settings": {
                    "task_type": "change_color",
                    "target_color": "银色，仅金属配件",
                    "instruction": "只将金属扣改成银色，保留图案、文字和背景",
                },
            }

    gateway = GoldGateway()
    gateway.artifact = source_pixels.artifact(
        Image.new("RGB", (40, 40), (185, 145, 60)), "original.png"
    )
    result = asyncio.run(GoldGuard(gateway, None, None).edit(request(gateway)))
    assert gateway.edits == []
    if mask_accepted:
        assert result.outcome == ProviderOutcome.SUCCEEDED
        plan_count = 6 if mask_accepted == "repair" else 5
        assert result.actual_cost_micros == 2 * plan_count
        assert result.raw_usage["network_requests"] == plan_count
        assert result.raw_usage["source_recolor"]["outside_mask_changed_pixels"] == 0
        reader = [p for p in gateway.plans if p.planning_profile == READBACK][-1]
        assert all(s.source_role == "candidate_detail" for s in reader.sources)
        assert "モモンガ" not in str(reader.product_information)
        with Image.open(BytesIO(result.artifact.payload)) as actual:
            assert actual.getpixel((10, 10)) == (147, 147, 147)
            assert actual.getpixel((0, 0)) == (185, 145, 60)
    else:
        assert result.error_code == "IMAGE_FIDELITY_PREPARATION_FAILED"
        assert result.actual_cost_micros == 6


@pytest.mark.parametrize("recover", [True, False])
def test_invalid_text_projection_retries_once_before_buying_any_background(
    monkeypatch, recover, caplog
):
    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)

    class InvalidLayout(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == BACKGROUND:
                count = len([p for p in self.plans if p.planning_profile == BACKGROUND])
                if count == 1 or not recover:
                    result.payload["text_layout"] = [
                        {
                            "copy_index": 0,
                            "panel": 0,
                            "panel_box": [0.1, 0.1, 0.9, 0.2],
                            "role": "heading",
                            "color": "#333333",
                            "align": "left",
                        }
                    ]
            return result

    gateway = InvalidLayout()
    guard = PixelGuard(gateway, None, None)
    result = asyncio.run(guard.edit(request(gateway)))
    layouts = [p for p in gateway.plans if p.planning_profile == BACKGROUND]
    assert (
        len(layouts) == 2 and layouts[0].idempotency_key != layouts[1].idempotency_key
    )
    if recover:
        assert result.outcome == ProviderOutcome.SUCCEEDED
        assert len(gateway.edits) == 1
    else:
        assert result.error_code == "IMAGE_FIDELITY_PREPARATION_FAILED"
        assert gateway.edits == []
        rejected = [r for r in caplog.records
                    if "background_projection_rejected" in r.getMessage()]
        assert len(rejected) == 1 and "attempt=2" in rejected[0].getMessage()
        assert "APPROVED COPY" not in rejected[0].getMessage()


def test_source_product_rgb_is_composited_after_one_native_background(monkeypatch):
    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)
    gateway = PixelGateway()
    guard = PixelGuard(gateway, None, None)
    result = asyncio.run(guard.edit(request(gateway)))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert len(gateway.edits) == 1 and len(gateway.plans) == 4
    assert "fidelity_backgroundNone" in guard.evidence
    with Image.open(BytesIO(result.artifact.payload)) as im:
        assert im.getpixel((20, 20)) == (255, 0, 0, 255)
        assert im.getpixel((0, 0)) == (0, 0, 255, 255)
    assert (
        result.raw_usage["source_pixel_preservation"]["background_sha256"]
        == guard.evidence["fidelity_backgroundNone"].sha256
    )
    comparisons = asyncio.run(
        guard.paired_source_evidence(request(gateway), result.artifact)
    )
    assert [source.source_role for source in comparisons] == [
        "source_candidate_comparison",
    ]
    assert comparisons[0].sha256 == hashlib.sha256(comparisons[0].payload).hexdigest()
    final_review = next(
        p for p in gateway.plans if p.planning_profile.endswith("_review")
    )
    assert any(
        s.source_role == "source_candidate_comparison" for s in final_review.sources
    )


def test_approved_plain_white_has_no_supplier_image_and_retains_lineage(monkeypatch):
    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)

    class WhiteGateway(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == BACKGROUND:
                assert "plain_white_plate" in json.loads(call.product_information[0])[
                    "schema"
                ]["properties"]
                result.payload["plain_white_plate"] = True
            return result

    gateway = WhiteGateway()
    guard = PixelGuard(gateway, None, None)
    result = asyncio.run(guard.edit(request(gateway)))

    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert gateway.edits == []
    assert result.raw_usage["background_origin"] == "local_white"
    assert result.raw_usage["metrics"]["white_product_lineage"]
    assert result.actual_cost_micros == 2 * len(gateway.plans)
    with Image.open(BytesIO(result.artifact.payload)) as image:
        assert image.getpixel((0, 0)) == (255, 255, 255, 255)
        assert image.getpixel((20, 20)) == (255, 0, 0, 255)


def test_colliding_product_proposal_is_compiled_and_frozen_before_one_image(
    monkeypatch,
):
    from app import source_layout
    from tests.test_source_layout import proposal

    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)
    original = proposal()
    for row in original:
        row["layer_index"] = 0  # This synthetic fixture has one original layer.
    original.append(
        {"layer_index": 0, "panel": 1, "panel_box": [0.2, 0.3, 0.7, 0.9]}
    )

    class MainGateway(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == BACKGROUND:
                destination = json.loads(call.product_information[0])["schema"][
                    "properties"
                ]["placements"]["items"]["properties"]["panel_box"]["items"]
                assert destination["minimum"] == 0.04
                assert destination["maximum"] == 0.96
                result.payload["placements"] = deepcopy(original)
                self.original_projection = result.payload
            return result

        async def edit(self, call):
            from app.source_pixels import artifact

            result = await super().edit(call)
            return replace(result, artifact=artifact(
                Image.new("RGB", (40, 80), "blue"), "native-pair.png"
            ))

    class MainGuard(PixelGuard):
        def _context(self, call):
            return {"job_type": "detail_pack", "item_key": "M01",
                    "settings": {"instruction": "Keep the original product"}}

    gateway = MainGateway()
    guard = MainGuard(gateway, None, None)
    guard._native_pair = True
    result = asyncio.run(guard.edit(replace(request(gateway), size="40x80")))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert len(gateway.edits) == 1
    recipe = guard.frozen["pixel_recipe"]
    assert recipe["product_layout_adjustments"]["version"] == source_layout.VERSION
    assert recipe["background_projection"]["placements"] != original
    assert gateway.original_projection["placements"] == original
    assert recipe["source_windows"][-2] == original[-2]["source_window"]
    assert recipe["placement_indices"] == [0, 0, 0, 0, 0]


def test_final_comparison_uses_original_frozen_binding_not_the_projected_prompt(
    monkeypatch,
):
    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)
    gateway = PixelGateway()
    original = request(gateway)

    class BoundPixelGuard(PixelGuard):
        def _frozen(self, call, value=None):
            assert call == original
            return super()._frozen(call, value)

    result = asyncio.run(BoundPixelGuard(gateway, None, None).edit(original))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert gateway.edits[0].prompt != original.prompt


@pytest.mark.parametrize("retention_fails", [False, True])
def test_comparison_failure_keeps_paid_original_and_cost_without_regeneration(
    monkeypatch, retention_fails,
):
    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)
    gateway = PixelGateway()

    class CorruptedEvidence(PixelGuard):
        async def paired_source_evidence(self, call, candidate):
            raise ValueError("Corrupted source receipt")

        async def _retain(self, call, artifact, **kwargs):
            if retention_fails and not kwargs:
                raise OSError("Storage unavailable")
            return await super()._retain(call, artifact, **kwargs)

    guard = CorruptedEvidence(gateway, None, None)
    result = asyncio.run(guard.edit(request(gateway)))
    assert result.error_code == "IMAGE_FIDELITY_EVIDENCE_FAILED"
    assert result.artifact is None and result.retryable is False
    assert len(gateway.edits) == 1 and len(gateway.plans) == 3
    assert result.actual_cost_micros == 16
    assert ("fidelity_candidateNone" in guard.evidence) is not retention_fails


def test_source_geometry_precedes_brief_without_freezing_or_dispatching_an_image(
    monkeypatch,
):
    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)
    gateway = PixelGateway()
    guard = PixelGuard(gateway, None, None)
    call = request(gateway)
    identity = asyncio.run(guard.prepare_source_identity(call))
    assert identity["enabled"] is True
    assert len(identity["calls"]) == 2 and gateway.edits == []
    assert identity["geometry"][0]["width_over_height"] == 1
    assert getattr(guard, "frozen", None) is None
    # The subsequent approved brief is different from the preflight prompt.
    # Source selection is reused, and only that final image request is frozen.
    result = asyncio.run(guard.edit(replace(call, prompt="Final source-aware brief")))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert len(gateway.edits) == 1
    assert len([p for p in gateway.plans if p.planning_profile == PREPARE]) == 1


@pytest.mark.parametrize("repeats", [False, True])
def test_malformed_matte_has_one_schema_correction_before_dispatch(
    monkeypatch, repeats
):
    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)

    class MalformedMatte(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == MATTE and (
                repeats or not call.idempotency_key.endswith(":contract-1")
            ):
                result.payload["un"] = result.payload.pop("unresolved")
            return result

    gateway = MalformedMatte()
    result = asyncio.run(PixelGuard(gateway, None, None).edit(request(gateway)))
    audits = [p for p in gateway.plans if p.planning_profile == MATTE]
    assert len(audits) == 2 and audits[0].sources == audits[1].sources
    assert bool(gateway.edits) is not repeats
    assert (result.error_code is None) is not repeats


@pytest.mark.parametrize("accepted", [True, False])
def test_suite_reuses_only_identical_audited_source_pixels_and_never_fishes_failures(
    monkeypatch, accepted
):
    extractions = []

    def extraction(*args, tight_context=False):
        extractions.append(tight_context)
        return pixel_extraction(*args)

    monkeypatch.setattr("app.source_pixels.extract_layers", extraction)

    class CachedMatteGateway(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == MATTE and not accepted:
                result.payload.update(
                    complete_objects=False, unresolved=["Missing edge"]
                )
            return result

    gateway = CachedMatteGateway()
    first = PixelGuard(gateway, None, None)
    second = PixelGuard(gateway, None, None)
    second._prepared, second._locks, second._mattes = (
        first._prepared,
        first._locks,
        first._mattes,
    )
    one = asyncio.run(first.edit(request(gateway)))
    two = asyncio.run(
        second.edit(
            replace(
                request(gateway),
                job_item_id="second-item",
                idempotency_key="second-item-key",
            )
        )
    )
    assert extractions == ([False] if accepted else [False, True])
    assert len([p for p in gateway.plans if p.planning_profile == MATTE]) == 1
    if accepted:
        assert one.outcome == two.outcome == ProviderOutcome.SUCCEEDED
        assert len(gateway.edits) == 2
        assert (
            first.frozen["pixel_recipe"]["layers"]
            == second.frozen["pixel_recipe"]["layers"]
        )
    else:
        assert one.error_code == two.error_code == "SOURCE_PRODUCT_UNCLEAR"
        assert gateway.edits == []


@pytest.mark.parametrize("second_pass", [True, False])
@pytest.mark.parametrize("use_alternate", [True, False])
def test_one_changed_source_mask_is_reaudited_before_any_image(
    monkeypatch, second_pass, use_alternate
):
    from PIL import ImageDraw

    from app import source_pixels

    def extraction(sources, preparation, model, *, tight_context=False):
        layer = pixel_extraction(sources, preparation, model)[0]
        with Image.open(BytesIO(layer.artifact.payload)) as opened:
            rgba = opened.convert("RGBA")
        alpha = Image.new("L", rgba.size)
        draw = ImageDraw.Draw(alpha)
        draw.rectangle((18, 2, 22, 8), fill=255)
        draw.rectangle((8, 9, 32, 38), fill=255)
        alpha.putpixel((20, 5), 255 if tight_context else 0)
        rgba.putalpha(alpha)
        return (replace(layer, artifact=source_pixels.artifact(rgba, "source.png")),)

    monkeypatch.setattr(source_pixels, "extract_layers", extraction)

    class CorrectedGateway(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == MATTE:
                count = len([p for p in self.plans if p.planning_profile == MATTE])
                if count == 1:
                    result.payload.update(
                        complete_objects=False,
                        unresolved=["Missing source edge"],
                        repairs=[] if use_alternate else [
                            {
                                "layer_index": 0,
                                "action": "restore_foreground",
                                "box": [36 / 72, 21 / 72, 37 / 72, 22 / 72],
                            }
                        ],
                    )
                elif not second_pass:
                    result.payload.update(
                        complete_objects=False, unresolved=["Still incomplete"]
                    )
            return result

    gateway = CorrectedGateway()
    original = Image.new("RGB", (40, 40), "blue")
    draw = ImageDraw.Draw(original)
    draw.rectangle((18, 2, 22, 8), fill=(190, 145, 55))
    draw.rectangle((8, 9, 32, 38), fill="red")
    gateway.artifact = source_pixels.artifact(original, "source-gold.png")
    guard = PixelGuard(gateway, None, None)
    result = asyncio.run(guard.edit(request(gateway)))
    audits = [p for p in gateway.plans if p.planning_profile == MATTE]
    assert len(audits) == 2
    assert audits[0].idempotency_key != audits[1].idempotency_key
    assert audits[0].sources[-1].sha256 != audits[1].sources[-1].sha256
    if second_pass:
        assert result.outcome == ProviderOutcome.SUCCEEDED
        assert len(gateway.edits) == 1
        assert len(guard.frozen["pixel_recipe"]["mask_audits"]) == 2
    else:
        assert result.error_code == "SOURCE_PRODUCT_UNCLEAR"
        assert gateway.edits == []


@pytest.mark.parametrize("issue_stage", [PREPARE, MATTE])
def test_indistinct_source_stops_at_first_visual_finding(monkeypatch, issue_stage):
    from app.provider_diagnostics import SOURCE_UPLOAD_MESSAGES, SUBJECT_UNCLEAR

    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)

    class UnclearSource(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile != issue_stage:
                return result
            if issue_stage == PREPARE:
                return replace(result, payload={"source_issue": SUBJECT_UNCLEAR})
            result.payload.update(complete_objects=False, no_context_leak=False)
            return result

    gateway = UnclearSource()
    guard = PixelGuard(gateway, None, None)
    original = request(gateway)
    for item in ("first", "second"):
        result = asyncio.run(
            guard.edit(replace(original, job_item_id=item, idempotency_key=item))
        )
        assert result.error_code == SUBJECT_UNCLEAR
        assert result.error_message == SOURCE_UPLOAD_MESSAGES[SUBJECT_UNCLEAR]
        assert not result.retryable
    assert sum(p.planning_profile == issue_stage for p in gateway.plans) == 1
    assert not gateway.edits


@pytest.mark.parametrize("photo_problem", [True, False])
def test_empty_foreground_and_inspection_failure_have_distinct_recovery(
    monkeypatch, photo_problem
):
    from app.source_pixels import SourceForegroundUnclear

    def extract(*args):
        if photo_problem:
            raise SourceForegroundUnclear("No reliable foreground")
        raise OSError("Model file unavailable")

    monkeypatch.setattr("app.source_pixels.extract_layers", extract)
    gateway = PixelGateway()
    guard = PixelGuard(gateway, None, None)
    original = request(gateway)
    for item in ("first", "second"):
        result = asyncio.run(
            guard.edit(replace(original, job_item_id=item, idempotency_key=item))
        )
        assert result.error_code == (
            "SOURCE_PRODUCT_UNCLEAR"
            if photo_problem
            else "IMAGE_FIDELITY_PREPARATION_FAILED"
        )
        assert ("重新上传" in result.error_message) is photo_problem
    assert not gateway.edits


def test_unreadable_original_print_requests_a_closeup_without_dispatch():
    class Unreadable(Gateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == READBACK:
                result.payload["text_runs"] = ["UNREADABLE"]
            return result

    gateway = Unreadable()
    result = asyncio.run(ScopedFidelity(gateway, None, None).edit(request(gateway)))
    assert result.error_code == "SOURCE_PRINT_UNREADABLE"
    assert "对焦印字" in result.error_message
    assert sum(call.planning_profile == READBACK for call in gateway.plans) == 1
    assert not gateway.edits


def test_failed_source_preflight_retains_planner_receipt_without_buying_an_image(
    monkeypatch,
):
    class FailedSource(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            return replace(result, error_code="PLANNER_RESPONSE_INVALID")

    gateway = FailedSource()
    guard = PixelGuard(gateway, None, None)
    identity = asyncio.run(guard.prepare_source_identity(request(gateway)))
    assert identity["error"] == "SOURCE_IDENTITY_PREPARATION_FAILED"
    assert identity["calls"][0].actual_cost_micros == 2
    assert gateway.edits == [] and getattr(guard, "frozen", None) is None


def test_source_pixel_reconciliation_reuses_frozen_wire_prompt_and_mattes(monkeypatch):
    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)
    gateway = PixelGateway(outcome=ProviderOutcome.OUTCOME_UNKNOWN)
    guard = PixelGuard(gateway, None, None)
    call = request(gateway)
    assert asyncio.run(guard.edit(call)).outcome == ProviderOutcome.OUTCOME_UNKNOWN
    sent = gateway.edits[0]
    restarted = PixelGuard(gateway, None, None)
    restarted.frozen, restarted.evidence = guard.frozen, guard.evidence

    def must_not_run(*args, **kwargs):
        raise AssertionError(
            "Frozen image must never replan or reconstruct its wire recipe"
        )

    monkeypatch.setattr("app.source_pixels.extract_layers", must_not_run)
    monkeypatch.setattr("app.source_pixels.background_prompt", must_not_run)
    gateway.outcome = ProviderOutcome.SUCCEEDED
    result = asyncio.run(restarted.edit(replace(call, reconcile_only=True)))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert replace(gateway.edits[1], reconcile_only=False) == sent
    assert len([p for p in gateway.plans if p.planning_profile == MATTE]) == 1


@pytest.mark.parametrize("cross_seam", [False, True])
def test_native_pair_composes_only_approved_source_layers_after_one_background(
    monkeypatch, cross_seam
):
    monkeypatch.setattr("app.source_pixels.extract_layers", pixel_extraction)

    class PairGateway(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            if call.planning_profile == BACKGROUND:
                result.payload["placements"] = [
                    {
                        "layer_index": 0,
                        "panel": 0,
                        "panel_box": [0.1, 0.2, 0.9, 1.01 if cross_seam else 0.9],
                    },
                    {
                        "layer_index": 0,
                        "panel": 1,
                        "panel_box": [0.1, 0.2, 0.9, 0.9],
                        "source_window": [0.1, 0.45, 0.9, 0.65],
                    },
                ]
            return result

        async def edit(self, call):
            result = await super().edit(call)
            from app.source_pixels import artifact

            return replace(
                result,
                artifact=artifact(
                    Image.new("RGB", (40, 80), "blue"), "native-pair.png"
                ),
            )

    class PairGuard(PixelGuard):
        def _context(self, call):
            return {
                "job_type": "detail_pack",
                "item_key": "M01",
                "settings": {"instruction": "原图商品图案保持不变"},
            }

    gateway = PairGateway()
    guard = PairGuard(gateway, None, None)
    guard._native_pair = True
    original = replace(
        request(gateway), size="40x80", idempotency_key="signed-native-child"
    )
    result = asyncio.run(guard.edit(original))
    if cross_seam:
        assert result.error_code == "IMAGE_FIDELITY_PREPARATION_FAILED"
        assert gateway.edits == []
    else:
        assert result.outcome == ProviderOutcome.SUCCEEDED
        assert len(gateway.edits) == 1
        assert gateway.edits[0].idempotency_key == original.idempotency_key
        assert guard.frozen["pixel_recipe"]["placement_indices"] == [0, 0]
        assert guard.frozen["pixel_recipe"]["source_windows"] == [
            [0, 0, 1, 1],
            [0.1, 0.45, 0.9, 0.65],
        ]
        projection = [p for p in gateway.plans if p.planning_profile == BACKGROUND][0]
        assert any(
            s.source_role == "source_window_reference:0" for s in projection.sources
        )
        context = json.loads(projection.product_information[0])
        assert context["source_layer_inventory"][0]["width_over_height"] == 1
        with Image.open(BytesIO(result.artifact.payload)) as image:
            assert image.size == (40, 80)
            assert image.getpixel((20, 20)) == (255, 0, 0, 255)
            assert image.getpixel((20, 60)) == (255, 0, 0, 255)
        assert original.prompt == "Change the person; retain source clothes"
