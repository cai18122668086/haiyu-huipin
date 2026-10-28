"""Unknown observations never become evidence or authorize another image."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from app.domestic_detail.review import (
    DetailReviewError,
    ReviewStore,
    normalize_pair_observation,
)
from app.xinghe_agent import PlannerRequest, TengliOpenAICompatibleGateway
from tests.test_native_detail_failure_transition import pair_audit as pair_audit


@pytest.fixture
def complete_observation(pair_audit):
    from tests.domestic_detail_atomic_fixtures import review_context

    root, _, raw = pair_audit
    raw["status"] = "PASS"
    raw["material_failures"] = []
    raw.update(ocr_text=[], unauthorized_ocr_text=[], forbidden_text_hits=[])
    request = review_context(root)
    request["unused_plan_copy"] = "UNSEEN PLAN COPY"
    request["pixel_lineage"]["canvas"]["sha256"] = "a" * 64
    for row in raw["module_judgments"]:
        row["slice_binding"]["canvas_sha256"] = "a" * 64
    return raw, request


def normalize(raw, request):
    return normalize_pair_observation(
        raw,
        request,
        image_sha256="a" * 64,
        width=3,
        height=8,
    )


def test_complete_observation_is_detached_and_contains_no_plan_substitutions(
    complete_observation,
):
    raw, request = complete_observation
    raw["artifact_binding"]["image_sha256"] = "a" * 64
    before = deepcopy(raw)
    result = normalize(raw, request)
    assert result == raw == before
    raw["artifact_binding"]["image_sha256"] = "A" * 64
    assert normalize(raw, request)["artifact_binding"]["image_sha256"] == "A" * 64
    raw["artifact_binding"]["image_sha256"] = "a" * 64
    assert "UNSEEN PLAN COPY" not in json.dumps(result)
    result["screen_coverage"][0]["selling_points_visible"].append("changed")
    assert raw == before


@pytest.mark.parametrize(
    "field",
    [
        "pair_id",
        "module_ids",
        "status",
        "audit_complete",
        "artifact_binding",
        "pair_canvas",
        "checks",
        "screen_coverage",
        "internal_seam_evidence",
        "information_audit",
        "identity_silhouette",
        "material_failures",
        "ocr_text",
        "unauthorized_ocr_text",
        "forbidden_text_hits",
    ],
)
def test_missing_observation_is_rejected_without_defaults(complete_observation, field):
    raw, request = complete_observation
    raw["artifact_binding"]["image_sha256"] = "a" * 64
    del raw[field]
    before = deepcopy(raw)
    with pytest.raises(DetailReviewError) as error:
        normalize(raw, request)
    assert error.value.code == "AURORA_NATIVE_AUDIT_INVALID"
    assert str(error.value).startswith("/" + field + ":")
    assert raw == before


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_pair",
        "reordered_screens",
        "dimensions",
        "wrong_raster",
        "missing_check",
        "unknown_boolean",
        "missing_evidence",
        "bad_identity",
        "wrong_seam",
        "ocr_disagreement",
        "missing_visual_output",
        "false_coverage",
        "false_seam",
        "false_information",
        "false_identity",
        "uncertain_audit",
        "unexpected_status",
    ],
)
def test_mismatched_or_contradictory_observation_is_not_a_paid_repair(
    complete_observation, mutation
):
    raw, request = complete_observation
    raw["artifact_binding"]["image_sha256"] = "a" * 64
    if mutation == "wrong_pair":
        raw["pair_id"] = "P02"
    elif mutation == "reordered_screens":
        raw["screen_coverage"].reverse()
    elif mutation == "dimensions":
        raw["pair_canvas"]["height_px"] = 9
    elif mutation == "wrong_raster":
        raw["artifact_binding"]["image_sha256"] = "b" * 64
    elif mutation == "missing_check":
        del raw["checks"]["facts"]
    elif mutation == "unknown_boolean":
        raw["checks"]["facts"]["pass"] = "true"
    elif mutation == "missing_evidence":
        raw["checks"]["facts"]["visible_evidence"] = ""
    elif mutation == "bad_identity":
        raw["identity_silhouette"]["shape_consistent"] = None
    elif mutation == "wrong_seam":
        raw["internal_seam_evidence"]["method_id"] = "EDGE_BLEED"
    elif mutation == "ocr_disagreement":
        raw["ocr_text"] = ["unreported extra text"]
    elif mutation == "missing_visual_output":
        raw["artifact_binding"]["visual_inspection_output"] = {}
    elif mutation == "false_coverage":
        raw["screen_coverage"][0]["duty_visible"] = False
    elif mutation == "false_seam":
        raw["internal_seam_evidence"]["hard_cut_absent"] = False
    elif mutation == "false_information":
        raw["information_audit"]["hero_subject_clear"] = False
    elif mutation == "false_identity":
        raw["identity_silhouette"]["shape_consistent"] = False
    elif mutation == "uncertain_audit":
        raw["audit_complete"] = False
    else:
        raw["status"] = "UNKNOWN"
    with pytest.raises(DetailReviewError):
        normalize(raw, request)


def test_explicit_failures_keep_exact_ocr_coverage_and_seam_observations(
    complete_observation,
):
    raw, request = complete_observation
    raw["artifact_binding"]["image_sha256"] = "a" * 64
    raw["status"] = "MATERIAL_FAIL"
    raw["checks"]["legibility"]["pass"] = False
    raw["material_failures"] = ["Observed lower-right glyph strokes are merged."]
    raw["screen_coverage"][1]["duty_visible"] = False
    raw["screen_coverage"][1]["selling_points_visible"] = []
    raw["internal_seam_evidence"]["geometry"] = (
        "Observed light trail ends above the fixed boundary"
    )
    raw["internal_seam_evidence"]["method_specific_pass"] = False
    before = deepcopy(raw)
    result = normalize(raw, request)
    expected = deepcopy(raw)
    expected["material_failures"] = [{"visible_evidence": raw["material_failures"][0]}]
    assert result == expected
    assert raw == before


def test_unknown_review_is_never_redispatched_and_original_bytes_are_immutable(
    tmp_path,
):
    binding = {
        "phase": "pair",
        "render_token": "signed-token",
        "candidate_sha256": "a" * 64,
        "native_request_sha256": "b" * 64,
        "model": "isolated-test",
    }
    store = ReviewStore(tmp_path / "reviews", binding)
    assert store.completed() is None
    store.begin()
    with pytest.raises(DetailReviewError) as error:
        store.completed()
    assert error.value.code == "AURORA_NATIVE_REVIEW_OUTCOME_UNKNOWN"
    with pytest.raises(DetailReviewError):
        store.begin()
    raw = ' \n{ "说明" : "保留原始空白", "status":"UNKNOWN" }\r\n'
    result = {"payload": {"status": "UNKNOWN"}, "audit_raw_content": raw}
    store.finish(result)
    assert store.completed() == result
    assert store.stem.with_suffix(".reply.txt").read_bytes() == raw.encode("utf-8")
    with pytest.raises(FileExistsError):
        store.finish(result)
    assert store.completed() == result
    store.stem.with_suffix(".reply.txt").write_bytes(b"changed")
    with pytest.raises(DetailReviewError) as error:
        store.completed()
    assert error.value.code == "AURORA_NATIVE_REVIEW_CHANGED"


def test_changed_model_or_source_cannot_obtain_another_review_permit(tmp_path):
    binding = {
        "phase": "pair",
        "render_token": "signed-token",
        "candidate_sha256": "a" * 64,
        "native_request_sha256": "b" * 64,
        "model": "first",
        "sources": ["original"],
    }
    store = ReviewStore(tmp_path, binding)
    store.begin()
    for change in ({"model": "second"}, {"sources": ["changed"]}):
        with pytest.raises(DetailReviewError) as error:
            ReviewStore(tmp_path, {**binding, **change}).completed()
        assert error.value.code == "AURORA_NATIVE_REVIEW_BINDING_CHANGED"


@pytest.mark.parametrize(
    "profile",
    ["aurora_detail_pair_audit", "aurora_detail_identity_audit", "detail_pack"],
)
@pytest.mark.parametrize("valid_json", [True, False])
def test_gateway_retains_original_message_only_for_detail_audits(profile, valid_json):
    raw = (
        ' \n{ "status" : "PASS", "说明" : "原始回复" }\r\n'
        if valid_json
        else "  rejected non-JSON reply\r\n"
    )

    class Transport:
        async def post_json(self, **kwargs):
            return 200, {
                "id": "offline-only",
                "choices": [{"message": {"content": raw}}],
                "usage": {"total_tokens": 10},
            }

    gateway = TengliOpenAICompatibleGateway(
        api_key="offline-only",
        base_url="https://isolated.invalid/",
        planner_model="offline-vision",
        image_model="offline-image",
        transport=Transport(),
    )
    request = PlannerRequest(
        workspace_id="isolated",
        job_id="new-test-job",
        group_key="primary",
        sources=(),
        page_count=2,
        language="zh-CN",
        product_name="fixture",
        product_information=(),
        platform_code="domestic-detail",
        size="3x8",
        idempotency_key="isolated-audit",
        planning_profile=profile,
    )
    result = asyncio.run(gateway.plan(request))
    assert result.audit_raw_content == (raw if profile != "detail_pack" else None)
    assert bool(result.error_code) is (not valid_json)
