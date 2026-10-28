"""Missing vision-envelope fields must not cause another image generation."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from app.aurora_detail_runtime import AuroraDetailHarnessRunner
from app.domestic_detail.review import approved_review_authority
from app.models import JobPlanRecord
from app.provider import ProviderRequest
from app.xinghe_agent import (
    FakeXingheGateway,
    PlannerCallResult,
    SourceImage,
    XingheAgentError,
    XingheImageProvider,
    _artifact_from_bytes,
    _planner_prompt,
    _planner_response_format,
    normalize_usage,
)
from tests.test_api import WORKSPACE_ID, dispatch_pending, upload_asset
from tests.test_domestic_detail_execution import _create_detail_job
from tests.test_domestic_detail_review_authority import compiled_review_session


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "partial_failed",
        "invalid_followup",
        "complete_failed",
        "uncertain_followup",
        "raw_missing",
        "raw_mismatch",
        "authority_missing",
        "authority_changed",
        "aliases_changed",
    ],
)
def test_native_identity_envelope_repair_is_bounded_and_preserves_failures(
    client, monkeypatch, tmp_path, mode
):
    asset = upload_asset(client, filename="identity-audit-contract.png")
    job = _create_detail_job(
        client, asset["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    with client.app.state.session_factory() as db:
        initial = db.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == job["id"])
        )
        prior_planner_calls = len(initial.payload["usage"]["planner"])
    monkeypatch.setattr(
        "app.xinghe_agent.detail_workspace", lambda job_id: tmp_path / job_id
    )

    class AuditGateway(FakeXingheGateway):
        provider_name = "offline-contract-test"

        async def plan(self, request):
            self.planner_calls.append(request)
            context = json.loads(request.product_information[0])
            assert request.product_name == authority["product_name"]
            if request.planning_profile == "aurora_detail_pair_audit":
                assert context["review_authority"] == authority
                assert (
                    context["product_information"] == authority["product_information"]
                )
                assert "Capacity 300 ml" not in context["product_information"]
                assert (
                    "777-hour performance claim" not in context["product_information"]
                )
            else:
                assert context["locked_identity"] == authority["locked_identity"]
                assert context["review_authority_sha256"] == authority["sha256"]
            if request.planning_profile == "aurora_detail_pair_audit":
                payload = provider._native_detail_judgment(native, artifact)
                payload["status"] = "MATERIAL_FAIL"
                payload["checks"]["internal_seam"]["pass"] = False
                payload["material_failures"] = [
                    {
                        "visible_evidence": (
                            "Black case edge does not cross the declared seam."
                        )
                    }
                ]
                if mode == "complete_failed":
                    payload["identity_silhouette"]["shape_consistent"] = False
                elif mode == "partial_failed":
                    payload["identity_silhouette"] = {"shape_consistent": False}
                else:
                    del payload["identity_silhouette"]
            else:
                assert request.planning_profile == "aurora_detail_identity_audit"
                assert "actual pixels" in _planner_prompt(request)
                schema = _planner_response_format(request)["json_schema"]["schema"]
                assert "shape_consistent" in schema["required"]
                payload = {
                    "audit_complete": True,
                    "comparison_scope": "VISIBLE_COMPLETE_PRODUCT",
                    "visible_features": [
                        "Observed rounded black frame matches the original edge"
                    ],
                    "shape_consistent": True,
                    "colorway_consistent": True,
                    "texture_consistent": True,
                    "visible_evidence": (
                        "Observed rounded black frame and mint polka-dot surface "
                        "match the originals."
                    ),
                }
                if mode == "invalid_followup":
                    del payload["texture_consistent"]
                elif mode == "uncertain_followup":
                    payload["audit_complete"] = False
                    payload["shape_consistent"] = None
                    payload["uncertainty"] = (
                        "The reference angle does not show the opening."
                    )
            return PlannerCallResult(
                payload=payload,
                provider_request_id=f"audit-{len(self.planner_calls)}",
                raw_usage={"total_tokens": 10},
                normalized_usage=normalize_usage({"total_tokens": 10}),
                api_latency_seconds=0.01,
                actual_cost_micros=1,
                audit_raw_content=(
                    None
                    if mode == "raw_missing"
                    else json.dumps(
                        {"status": "different"} if mode == "raw_mismatch" else payload,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                ),
            )

    gateway = AuditGateway()
    provider = XingheImageProvider(
        client.app.state.session_factory,
        gateway,
        storage=None,
        skill_root=AuroraDetailHarnessRunner().skill_root,
        max_concurrency=1,
        skill_id="aurora-domestic-ecommerce-detail-page",
    )
    buffer = BytesIO()
    Image.new("RGB", (24, 64), "#88cccc").save(buffer, format="PNG")
    artifact = _artifact_from_bytes(
        buffer.getvalue(),
        filename="pair.png",
        expected_size="1440x3840",
        preserve_provider_artifact=True,
    )
    source = SourceImage(
        asset_id="reference",
        group_key="primary",
        filename="reference.png",
        content_type="image/png",
        sha256=artifact.sha256,
        payload=artifact.payload,
        source_role="product_reference",
    )
    authority = approved_review_authority(compiled_review_session())
    native = {
        "review_authority": authority,
        "product_name": authority["product_name"],
        "product_information": authority["product_information"],
        "pair_id": "P01",
        "module_ids": ["M01", "M02"],
        "pair_render": {"internal_seam_method_id": "EDGE_BLEED"},
    }
    from tests.domestic_detail_atomic_fixtures import (
        attached_atomic_sources,
        create_atomic_context,
    )

    native.update(create_atomic_context(tmp_path / "atomic-pair", artifact))
    audit_sources = attached_atomic_sources(native, (source,))
    if mode == "authority_missing":
        native.pop("review_authority")
    elif mode == "authority_changed":
        native["review_authority"] = {**authority, "sha256": "0" * 64}
    elif mode == "aliases_changed":
        native["product_information"] = ["Capacity 300 ml"]
    request = ProviderRequest(
        workspace_id=WORKSPACE_ID,
        job_id=job["id"],
        job_item_id="item-test",
        attempt_id="attempt-test",
        operation="detail_pack",
        idempotency_key="audit-test",
        configuration={"group_key": "primary", "product_name": "Wrong raw setting"},
    )
    coroutine = provider._native_detail_judgment_with_review(
        request, native, audit_sources, artifact
    )
    expected_error = {
        "invalid_followup": "AURORA_NATIVE_IDENTITY_AUDIT_INVALID",
        "uncertain_followup": "AURORA_NATIVE_IDENTITY_AUDIT_INVALID",
        "raw_missing": "AURORA_NATIVE_REVIEW_RAW_REPLY_MISSING",
        "raw_mismatch": "AURORA_NATIVE_REVIEW_RAW_REPLY_MISMATCH",
        "authority_missing": "AURORA_NATIVE_REVIEW_AUTHORITY_INVALID",
        "authority_changed": "AURORA_NATIVE_REVIEW_AUTHORITY_INVALID",
        "aliases_changed": "AURORA_NATIVE_REVIEW_AUTHORITY_INVALID",
    }.get(mode)
    if expected_error:
        with pytest.raises(XingheAgentError) as error:
            asyncio.run(coroutine)
        assert error.value.code == expected_error
    else:
        judgment = asyncio.run(coroutine)
        assert judgment["identity_silhouette"]["shape_consistent"] is (
            mode == "missing"
        )
        assert judgment["checks"]["internal_seam"]["pass"] is False
        assert judgment["status"] == "MATERIAL_FAIL"
    calls = (
        0
        if mode in {"authority_missing", "authority_changed", "aliases_changed"}
        else 1
        if mode in {"complete_failed", "raw_missing", "raw_mismatch"}
        else 2
    )
    assert len(gateway.planner_calls) == calls
    assert gateway.image_calls == []
    assert all(
        call.sources[-1].sha256 == artifact.sha256 for call in gateway.planner_calls
    )
    with client.app.state.session_factory() as db:
        plan = db.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == job["id"]))
        assert len(plan.payload["usage"]["planner"]) == prior_planner_calls + calls
    repeat = provider._native_detail_judgment_with_review(
        replace(request, attempt_id="reclaimed-execution"),
        native,
        audit_sources,
        artifact,
    )
    if expected_error:
        with pytest.raises(XingheAgentError) as repeated_error:
            asyncio.run(repeat)
        assert repeated_error.value.code == expected_error
    else:
        assert asyncio.run(repeat) == judgment
    assert len(gateway.planner_calls) == calls
    with client.app.state.session_factory() as db:
        plan = db.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == job["id"]))
        assert len(plan.payload["usage"]["planner"]) == prior_planner_calls + calls
    evidence = list((tmp_path / job["id"] / "reviews").glob("*.response.json"))
    assert len(evidence) == calls
    if calls == 0:
        return
    for path in evidence:
        saved = json.loads(path.read_bytes())
        raw_path = path.with_name(path.name.replace(".response.json", ".reply.txt"))
        if mode == "raw_missing":
            assert saved["raw_reply_state"] == "predecoded_only"
            assert not raw_path.exists()
        else:
            assert raw_path.read_bytes() == saved["result"]["audit_raw_content"].encode(
                "utf-8"
            )
    original = next(
        p
        for p in evidence
        if json.loads(p.read_bytes())["result"]["phase"].startswith(
            "aurora_detail_pair_audit:"
        )
    )
    if mode in {"missing", "invalid_followup"}:
        assert (
            "identity_silhouette"
            not in json.loads(original.read_text(encoding="utf-8"))["response"]
        )
