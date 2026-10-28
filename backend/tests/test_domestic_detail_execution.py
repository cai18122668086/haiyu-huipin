# ruff: noqa: E501
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.aurora_detail_runtime import (
    detail_workspace,
    normalize_native_plan_for_harness,
)
from app.domain import JobStatus, PlanValidationStatus
from app.models import (
    AuditEventRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    utc_now,
)
from app.services import canonical_hash
from app.xinghe_agent import (
    IMAGE_SIZE,
    FakeXingheGateway,
    PlannerRequest,
    XingheAgentError,
    XingheImageProvider,
    _accept_aurora_completion_module,
    _fake_plan,
    _merge_aurora_plan_repair,
    _planner_prompt,
    build_resume_signature,
    compile_page_prompt,
    validate_planner_payload,
)
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset

AUTO_CONFIRM_ACTION = "detail_plan.auto_confirmed"

@pytest.fixture
def legacy_whole_plan_path(client, monkeypatch):
    """Retain coverage of existing saved-candidate completion and retry behavior."""
    planner = client.app.state.task_runner.coordinator.detail_planner
    monkeypatch.setattr(type(planner.domestic), "_bounded_authoring_request", lambda self, request: request)




def _create_detail_job(
    client: TestClient,
    asset_id: str,
    *,
    market_region: str,
    auto_start_after_plan: bool,
    page_count: int = 8,
    platform_code: str | None = None,
    reference_asset_ids: list[str] | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=idem(),
        json={
            "type": "detail_pack",
            "intent": "submit",
            "display_name": f"{market_region} detail execution boundary",
            "input": {
                "product_groups": [{"group_key": "primary", "asset_ids": [asset_id]}],
                "reference_asset_ids": reference_asset_ids or [],
            },
            "configuration": {
                "product_name": "Execution boundary product",
                "brandless_confirmation": "我确认这个合成测试商品无品牌，不添加独立标志",
                "selling_points": ["durable plan", "bounded generation"],
                "platform_code": platform_code
                or ("tmall" if market_region == "domestic" else "amazon"),
                "market_region": market_region,
                "language": "zh-CN" if market_region == "domestic" else "en",
                "page_count": page_count,
                "auto_start_after_plan": auto_start_after_plan,
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _gateway(client: TestClient) -> FakeXingheGateway:
    planner = client.app.state.task_runner.coordinator.detail_planner
    assert planner is not None
    gateway = planner.gateway
    assert isinstance(gateway, FakeXingheGateway)
    return gateway


def _auto_confirm_audit_count(client: TestClient, job_id: str) -> int:
    with client.app.state.session_factory() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(
                    AuditEventRecord.action == AUTO_CONFIRM_ACTION,
                    AuditEventRecord.request_id == job_id,
                )
            )
            or 0
        )


def _aurora_approval_payload(planned: dict[str, object]) -> dict[str, object]:
    """Return the exact native Aurora v4.3 approval packet for a planned job."""

    plan = planned.get("plan")
    assert isinstance(plan, dict)
    payload = plan.get("payload")
    assert isinstance(payload, dict)
    aurora = payload.get("aurora_v4_3")
    assert isinstance(aurora, dict)
    return {
        "approval_protocol": payload["native_approval"]["version"],
        "native_approval_document_sha256": payload["native_approval"]["document_sha256"],
        "expected_revision": plan["revision"],
        "approval_text": "我已审阅完整 group→screen 审批包，并批准按 3:8 pair 画布生成。",
        "approval_document_sha256": aurora["approval_document_sha256"],
        "approved_module_count": aurora["approved_module_count"],
        "approved_pair_canvas_aspect_ratio": aurora[
            "approved_pair_canvas_aspect_ratio"
        ],
    }


def test_domestic_valid_plan_requires_approval_then_waits_for_final_review(
    client: TestClient,
) -> None:
    source = upload_asset(client, filename="domestic-auto-confirm.png")
    job = _create_detail_job(
        client,
        source["id"],
        market_region="domestic",
        auto_start_after_plan=True,
    )
    gateway = _gateway(client)
    # Aurora's native pair canvas is 1440×3840 (5.53 MP); keep the test
    # harness limit above that Skill contract before approving generation.
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000

    dispatch_count = dispatch_pending(client)
    assert dispatch_count in (0, 1)
    planned = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()

    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert planned["plan"]["confirmed_at"] is None
    assert len(gateway.planner_calls) == 6
    assert gateway.image_calls == []
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["status"] == JobStatus.PROCESSING.value
    # The test fixture uses inline dispatch, so the route's background
    # outbox publisher may already have completed generation before the
    # response is observed.  A second publish is therefore optional.
    dispatch_count = dispatch_pending(client)
    awaiting_review = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert awaiting_review["status"] == JobStatus.AWAITING_FINAL_REVIEW.value
    assert awaiting_review["stage"] == "awaiting_user_final_review"
    assert all(item["status"] == "succeeded" for item in awaiting_review["items"])
    assert len(gateway.image_calls) == 4
    assert all(call.size == "1440x3840" for call in gateway.image_calls)
    assert all("PAIR_PLAN_MERGE" in call.prompt for call in gateway.image_calls)
    with client.app.state.session_factory() as session:
        plan = session.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == job["id"])
        )
        audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.action == AUTO_CONFIRM_ACTION,
                AuditEventRecord.request_id == job["id"],
            )
        )
        attempt_count = session.scalar(
            select(func.count())
            .select_from(JobAttemptRecord)
            .where(JobAttemptRecord.job_id == job["id"])
        )
    assert plan is not None and plan.confirmed_at is not None
    assert audit is None
    assert attempt_count == 8

    assert (
        asyncio.run(client.app.state.task_runner.run(str(job["id"])))
        == JobStatus.AWAITING_FINAL_REVIEW
    )
    assert len(gateway.planner_calls) == 6
    assert len(gateway.image_calls) == 4
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0
    with client.app.state.session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(JobAttemptRecord)
                .where(JobAttemptRecord.job_id == job["id"])
            )
            == 8
        )


def test_domestic_plan_commit_recovery_preserves_confirmation_gate(
    client: TestClient,
) -> None:
    source = upload_asset(client, filename="domestic-auto-confirm-recovery.png")
    job = _create_detail_job(
        client,
        source["id"],
        market_region="domestic",
        auto_start_after_plan=True,
    )
    runner = client.app.state.task_runner
    coordinator = runner.coordinator
    planner = coordinator.detail_planner
    assert planner is not None
    gateway = _gateway(client)

    assert coordinator.prepare(str(job["id"])) == JobStatus.PLANNING
    assert (
        asyncio.run(planner.plan(str(job["id"])))
        == JobStatus.AWAITING_CONFIRMATION
    )
    with client.app.state.session_factory() as session:
        plan = session.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == job["id"])
        )
        assert plan is not None and plan.confirmed_at is None
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0
    assert len(gateway.planner_calls) == 6
    assert gateway.image_calls == []

    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    planned = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert confirmed.status_code == 202, confirmed.text
    assert asyncio.run(runner.run(str(job["id"]))) == JobStatus.AWAITING_FINAL_REVIEW
    assert len(gateway.planner_calls) == 6
    assert len(gateway.image_calls) == 4
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0

    assert asyncio.run(runner.run(str(job["id"]))) == JobStatus.AWAITING_FINAL_REVIEW
    assert len(gateway.planner_calls) == 6
    assert len(gateway.image_calls) == 4
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0


def test_domestic_unsupported_platform_fails_before_planner_provider_call(
    client: TestClient,
) -> None:
    source = upload_asset(client, filename="domestic-unsupported-platform.png")
    job = _create_detail_job(
        client,
        source["id"],
        market_region="domestic",
        auto_start_after_plan=True,
        platform_code="unsupported-marketplace",
    )
    gateway = _gateway(client)

    assert dispatch_pending(client) == 1
    failed = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()

    assert failed["status"] == JobStatus.FAILED.value
    assert failed["error_code"] == "DETAIL_PLATFORM_UNSUPPORTED"
    assert gateway.planner_calls == []
    assert gateway.image_calls == []
    assert failed.get("plan") is None
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0


def test_domestic_auto_start_false_stops_at_confirmation_gate(
    client: TestClient,
) -> None:
    source = upload_asset(client, filename="domestic-manual-confirm.png")
    job = _create_detail_job(
        client,
        source["id"],
        market_region="domestic",
        auto_start_after_plan=False,
    )
    gateway = _gateway(client)

    assert dispatch_pending(client) == 1
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()

    assert current["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert len(gateway.planner_calls) == 6
    assert gateway.image_calls == []
    assert current["plan"]["confirmed_at"] is None
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0


@pytest.mark.usefixtures("legacy_whole_plan_path")
def test_domestic_invalid_plan_never_reaches_image_provider(
    client: TestClient,
) -> None:
    gateway = _gateway(client)
    gateway.planner_failure = "schema"
    source = upload_asset(client, filename="domestic-invalid-plan.png")
    job = _create_detail_job(
        client,
        source["id"],
        market_region="domestic",
        auto_start_after_plan=True,
    )

    assert dispatch_pending(client) == 1
    failed = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()

    assert failed["status"] == JobStatus.FAILED.value
    assert failed["error_code"] == "AURORA_PLAN_SCHEMA_INVALID"
    # Invalid root contracts stop after one targeted campaign repair, before
    # any of the eight module-completion calls or image generation can start.
    assert len(gateway.planner_calls) == 3
    assert gateway.planner_calls[-1].planning_profile == "aurora_domestic_campaign_completion"
    assert gateway.image_calls == []
    assert failed["plan"]["validation_status"] == PlanValidationStatus.INVALID.value
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0


@pytest.mark.usefixtures("legacy_whole_plan_path")
def test_domestic_missing_campaign_contracts_repair_before_modules_and_preserve_valid_values(
    client: TestClient, monkeypatch,
) -> None:
    gateway = _gateway(client)
    original_plan = gateway.plan

    async def incomplete_plan(request):
        result = await original_plan(request)
        if request.planning_profile == "aurora_domestic_intake":
            return result
        payload = deepcopy(result.payload)
        if request.planning_profile == "aurora_domestic_detail":
            payload.pop("reference_expression_system")
            payload.pop("selling_point_depth_contract")
            payload["output_strategy"].pop("selling_point_clusters")
            payload["output_strategy"].pop("claim_coverage")
            payload["campaign_thesis"] = "保留原始有效的商品购买叙事。"
        elif request.planning_profile == "aurora_domestic_campaign_completion":
            payload["campaign_thesis"] = "修复响应不应覆盖这个已有效的字段。"
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", incomplete_plan)
    source = upload_asset(client, filename="missing-campaign.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert [r.planning_profile for r in gateway.planner_calls] == ["aurora_domestic_intake", "aurora_domestic_detail", "aurora_domestic_campaign_completion"]
    payload = planned["plan"]["payload"]
    assert len(payload["usage"]["planner"]) == 3
    assert payload["usage"]["planner"][2]["phase"] == "aurora_domestic_campaign_completion"
    assert payload["groups"][0]["plan_protocol_fields"]["aurora_plan"]["campaign_thesis"] == "保留原始有效的商品购买叙事。"
    assert gateway.image_calls == []


@pytest.mark.usefixtures("legacy_whole_plan_path")
def test_domestic_harness_preflight_repairs_bad_claim_mapping_before_approval(
    client: TestClient, monkeypatch,
) -> None:
    gateway = _gateway(client)
    original_plan = gateway.plan

    async def unmapped_plan(request):
        result = await original_plan(request)
        if request.planning_profile == "aurora_domestic_intake":
            return result
        payload = deepcopy(result.payload)
        if request.planning_profile == "aurora_domestic_detail":
            payload["output_strategy"]["claim_coverage"].append({
                "claim_id": "F99", "disposition": "LEAD", "module_ids": [],
                "rationale": "无商品证据和已批准事实的错误声明", "defer_reason_code": "NOT_DEFERRED",
            })
        elif request.planning_profile == "aurora_domestic_plan_repair":
            # The actual error needs only a corrected coverage list; the
            # already authored eight screens must survive a partial response.
            payload = {"output_strategy": {"claim_coverage": payload["output_strategy"]["claim_coverage"]}}
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", unmapped_plan)
    source = upload_asset(client, filename="mapping-preflight.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert [r.planning_profile for r in gateway.planner_calls] == ["aurora_domestic_intake", "aurora_domestic_detail", "aurora_domestic_plan_repair"]
    assert "selected claim F99 needs at least one assigned module" in str(gateway.planner_calls[-1].product_information)
    original_snapshot = detail_workspace(str(job["id"])) / "planning" / "aurora_domestic_plan_repair-campaign-request.json"
    before = json.loads(original_snapshot.read_text(encoding="utf-8"))
    assert before["plan"]["modules"][0]["duty"]
    assert "F99" in str(before["validation_errors"])
    assert "partial JSON update" in _planner_prompt(gateway.planner_calls[-1])
    assert "RESPONSE_CONTRACT_JSON=" in _planner_prompt(gateway.planner_calls[-1])
    assert gateway.image_calls == []


def test_partial_plan_repair_preserves_other_modules_and_matches_stable_ids() -> None:
    original = {"modules": [
        {"id": "M01", "duty": "首屏", "proof": {"visible": "原图外观", "relation": "完整产品"}},
        {"id": "M02", "duty": "细节", "proof": {"visible": "开孔", "relation": "局部"}},
    ]}
    repaired = _merge_aurora_plan_repair(original, {"modules": [{"id": "M02", "proof": {"relation": "开孔与接口对应"}}]})
    assert repaired["modules"][0] == original["modules"][0]
    assert repaired["modules"][1] == {"id": "M02", "duty": "细节", "proof": {"visible": "开孔", "relation": "开孔与接口对应"}}
    assert original["modules"][1]["proof"]["relation"] == "局部"
    for patches in ([{"id": "M99"}], [{"id": "M02"}, {"id": "M02"}]):
        with pytest.raises(XingheAgentError, match="unknown or duplicate module ID"):
            _merge_aurora_plan_repair(original, {"modules": patches})


@pytest.mark.parametrize("mode", ["patch", "zero_calls", "full", "stale_source", "pair_order"])
@pytest.mark.usefixtures("legacy_whole_plan_path")
def test_failed_planning_retry_recovers_verified_candidate_without_rebilling_old_calls(
    client: TestClient, monkeypatch, mode: str,
) -> None:
    gateway = _gateway(client)
    original_plan = gateway.plan
    authored_module = {}

    async def invalid_policy(request):
        result = await original_plan(request)
        if request.planning_profile == "aurora_domestic_intake":
            return result
        payload = deepcopy(result.payload)
        if request.planning_profile == "aurora_domestic_detail":
            authored_module.update(deepcopy(payload["modules"][2]))
            if mode == "pair_order":
                payload["output_strategy"]["continuous_pair_rendering"]["pair_ids"][2]["module_ids"] = ["M04", "M05"]
        elif request.planning_profile == "aurora_domestic_modules_completion":
            payload = {"modules": [deepcopy(authored_module)]}
        elif request.planning_profile == "aurora_domestic_plan_repair":
            payload = {"modules": [{"id": "M03", "expression": {}}]}
        for module in payload["modules"]:
            if module.get("id") == "M03":
                module["expression"]["evidence_subject_policy"] = "UNKNOWN_EVIDENCE_POLICY"
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", invalid_policy)
    asset = upload_asset(client, filename=f"planning-recovery-{mode}.png")
    job = _create_detail_job(client, asset["id"], market_region="domestic", auto_start_after_plan=False)
    dispatch_pending(client)
    failed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert failed["status"] == "failed"
    assert failed["error_code"] == "AURORA_PLAN_SCHEMA_INVALID"
    assert gateway.planner_calls[-1].planning_profile == "aurora_domestic_plan_repair"
    with client.app.state.session_factory() as session:
        prior = session.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == job["id"]))
        if mode in {"zero_calls", "stale_source", "pair_order"}:
            payload = deepcopy(prior.payload)
            if mode in {"zero_calls", "pair_order"}:
                payload["rejected_aurora_plan"]["modules"][2]["expression"]["evidence_subject_policy"] = "PRODUCT_DETAIL_DOMINANT"
            else:
                payload["groups"][0]["ordered_sources"][0]["sha256"] = "stale-source-hash"
            prior.payload = payload
            prior.payload_hash = canonical_hash(payload)
            session.commit()
    old_call_count = len(gateway.planner_calls)

    async def recover(request):
        if request.planning_profile == "aurora_domestic_intake":
            return await original_plan(request)
        assert mode != "zero_calls", "A migrated valid checkpoint must make no provider call"
        if mode in {"patch", "pair_order"}:
            assert request.planning_profile == "aurora_domestic_plan_repair"
            if mode == "patch":
                assert "UNKNOWN_EVIDENCE_POLICY" in str(request.product_information)
            else:
                assert "/pair_ids/2" in str(request.product_information)
                assert "expected_module_ids" in str(request.product_information)
                assert "align its seam carrier" in str(request.product_information)
        else:
            assert request.planning_profile == "aurora_domestic_detail"
        result = await original_plan(request)
        if mode == "patch":
            result = replace(result, payload={"modules": [{"id": "M03", "expression": {"evidence_subject_policy": "CLAIM_OBJECT_DOMINANT"}}]})
        elif mode == "pair_order":
            result = replace(result, payload={"output_strategy": {"continuous_pair_rendering": {"pair_ids": result.payload["output_strategy"]["continuous_pair_rendering"]["pair_ids"]}}})
        return result

    monkeypatch.setattr(gateway, "plan", recover)
    response = client.post(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/retry",
        headers=idem(), json={"scope": "full" if mode == "full" else "failed"})
    assert response.status_code == 201, response.text
    target_id = response.json()["id"]
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target_id}").json()
    assert planned["status"] == "awaiting_confirmation", planned
    payload = planned["plan"]["payload"]
    expected_new_calls = 0 if mode == "zero_calls" else 2 if mode == "full" else 1
    assert len(gateway.planner_calls) - old_call_count == expected_new_calls
    assert len(payload["usage"]["planner"]) == expected_new_calls
    assert payload["metrics"]["usage_missing_count"] == 0
    assert ("planning_recovery" in payload) == (mode in {"patch", "zero_calls", "pair_order"})
    if mode in {"patch", "zero_calls", "pair_order"}:
        assert payload["planning_recovery"]["source_job_id"] == job["id"]
        assert payload["planning_recovery"]["provider_request_count"] == 0
    assert gateway.image_calls == []


@pytest.mark.parametrize("pixel_failure", ["string_legibility", "seam", "identity"])
def test_native_string_audit_failures_allow_one_repair_then_finish_all_eight_screens(
    client: TestClient, monkeypatch, pixel_failure,
) -> None:
    async def review(self, request, native_request, sources, artifact):
        del request, sources
        judgment = self._native_detail_judgment(native_request, artifact)
        if native_request["pair_id"] == "P01":
            judgment["status"] = "MATERIAL_FAIL"
            judgment["checks"]["legibility"] = {
                "pass": False, "visible_evidence": "首对图片右侧的小字有一处笔画粘连，需要保留为审计问题。",
            }
            judgment["material_failures"] = ["首对图片右侧的小字有一处笔画粘连，需要保留为审计问题。"]
            if pixel_failure == "seam":
                judgment["internal_seam_evidence"]["hard_cut_absent"] = False
                judgment["internal_seam_evidence"]["method_specific_pass"] = False
            elif pixel_failure == "identity":
                judgment["identity_silhouette"]["shape_consistent"] = False
        return self._normalize_native_pair_judgment(judgment, native_request, artifact)

    monkeypatch.setattr(XingheImageProvider, "_native_detail_judgment_with_review", review)
    source = upload_asset(client, filename="string-audit-failures.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(), json=_aurora_approval_payload(planned),
    )
    assert confirmed.status_code == 202, confirmed.text
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert result["status"] == JobStatus.AWAITING_FINAL_REVIEW.value
    assert all(item["status"] == "succeeded" for item in result["items"])
    assert len(_gateway(client).image_calls) == 5
    native = json.loads((detail_workspace(job["id"]) / "session.json").read_text(encoding="utf-8"))
    first_pair = native["modules"][:2]
    assert [module["status"] for module in first_pair] == ["COMPLETE_WITH_RESIDUALS"] * 2
    assert all(len(module["attempts"]) == 2 for module in first_pair)
    if pixel_failure == "seam":
        assert all(module["attempts"][-1]["judgment"]["internal_seam_evidence"]["hard_cut_absent"] is False for module in first_pair)
    elif pixel_failure == "identity":
        assert all(module["attempts"][-1]["judgment"]["identity_silhouette"]["shape_consistent"] is False for module in first_pair)
    session = json.loads((detail_workspace(str(job["id"])) / "session.json").read_text(encoding="utf-8"))
    assert all(m["status"] == "COMPLETE_WITH_RESIDUALS" for m in session["modules"][:2])
    assert all(m["cycle_attempt_count"] == 2 for m in session["modules"][:2])
    assert "笔画粘连" in str(session["modules"][0]["residual_issues"])
    # The mutable current request may be replaced during repair; original
    # request bytes and both provider originals must remain independently read.
    import hashlib
    from pathlib import Path

    requests = session["generation_requests"]
    assert len({row["request"] for row in requests}) == 5
    for row in requests:
        assert hashlib.sha256(Path(row["request"]).read_bytes()).hexdigest() == row["request_sha256"]
    first_attempts = session["modules"][0]["attempts"]
    assert len({row["source_image"] for row in first_attempts}) == 2
    for row in first_attempts:
        assert hashlib.sha256(Path(row["source_image"]).read_bytes()).hexdigest() == row["source_image_sha256"]



def test_failed_detail_retry_recovers_audited_prefix_and_native_pair_without_new_calls(client, monkeypatch):
    from botocore.exceptions import ClientError

    source = upload_asset(client, filename="checkpoint-detail-retry.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    confirmed = client.post(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm", headers=idem(), json=_aurora_approval_payload(planned))
    assert confirmed.status_code == 202
    dispatch_pending(client)
    checkpoint_path = detail_workspace(job["id"]) / "session.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["state"], checkpoint["current_index"] = "AUTHORIZED", 6
    for module in checkpoint["modules"][6:]:
        module.update(status="PENDING", accepted_asset=None, attempts=[], cycle_attempt_count=0)
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
    with client.app.state.session_factory() as db:
        original = db.get(JobRecord, job["id"])
        original.status, original.stage = "partial", "partial"
        original.succeeded_item_count, original.failed_item_count = 4, 1
        original.finished_at = utc_now()
        items = db.scalars(select(JobItemRecord).where(JobItemRecord.job_id == job["id"]).order_by(JobItemRecord.ordinal)).all()
        for index, item in enumerate(items[4:], start=4):
            item.status = "failed" if index == 4 else "skipped"
            item.error_code = "PROVIDER_OUTCOME_UNKNOWN" if index == 4 else "AURORA_DETAIL_SEQUENCE_HALTED"
        db.commit()
    before = len(_gateway(client).image_calls)
    retried = client.post(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/retry", headers=idem(), json={"scope": "failed"})
    assert retried.status_code == 201, retried.text
    target = retried.json()
    original_read = client.app.state.storage.read_bytes
    async def s3_missing_response(key):
        try:
            return await original_read(key)
        except FileNotFoundError as exc:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject") from exc
    monkeypatch.setattr(client.app.state.storage, "read_bytes", s3_missing_response)
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target['id']}").json()
    assert result["status"] == "awaiting_final_review", result
    assert [item["status"] for item in result["items"]] == ["reused"] * 4 + ["succeeded"] * 4
    assert len(_gateway(client).image_calls) - before == 1
    assert len(_gateway(client).planner_calls) == 6
    assert json.loads(checkpoint_path.read_text(encoding="utf-8")) == checkpoint


def test_failed_detail_retry_restores_all_halted_screens_and_reuses_approved_plan(client):
    source = upload_asset(client, filename="halted-detail-retry.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    confirmed = client.post(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(), json=_aurora_approval_payload(planned))
    assert confirmed.status_code == 202
    with client.app.state.session_factory() as db:
        original = db.get(JobRecord, job["id"])
        original.status, original.stage = "failed", "failed"
        original.finished_at = utc_now()
        items = db.scalars(select(JobItemRecord).where(JobItemRecord.job_id == job["id"]).order_by(JobItemRecord.ordinal)).all()
        for index, item in enumerate(items):
            item.status = "failed" if index == 0 else "skipped"
            item.error_code = "AURORA_NATIVE_PAIR_AUDIT_FAILED" if index == 0 else "AURORA_DETAIL_SEQUENCE_HALTED"
        db.commit()
    retried = client.post(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/retry", headers=idem(), json={"scope": "failed"})
    assert retried.status_code == 201, retried.text
    target = retried.json()
    assert [item["status"] for item in target["items"]] == ["pending"] * 8
    assert target["plan"]["confirmed_at"] is not None
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target['id']}").json()
    assert result["status"] == "awaiting_final_review", result
    assert all(item["status"] == "succeeded" for item in result["items"])
    assert len(_gateway(client).image_calls) == 4
    assert len(_gateway(client).planner_calls) == 6


@pytest.mark.usefixtures("legacy_whole_plan_path")
def test_missing_middle_module_completes_only_that_id_without_reordering(
    client: TestClient, monkeypatch,
) -> None:
    gateway = _gateway(client)
    original_plan = gateway.plan
    authored = {}

    async def missing_middle(request):
        result = await original_plan(request)
        if request.planning_profile == "aurora_domestic_intake":
            return result
        payload = deepcopy(result.payload)
        if request.planning_profile == "aurora_domestic_detail":
            authored["M03"] = deepcopy(payload["modules"][2])
            payload["modules"].pop(2)
        elif request.planning_profile == "aurora_domestic_modules_completion":
            assert "TARGET_MODULE_ID=M03" in request.product_information
            payload = {"modules": [authored["M03"]]}
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", missing_middle)
    source = upload_asset(client, filename="middle-module.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert [r.planning_profile for r in gateway.planner_calls] == ["aurora_domestic_intake", "aurora_domestic_detail", "aurora_domestic_modules_completion"]
    assert len(planned["plan"]["payload"]["usage"]["planner"]) == 3


@pytest.mark.parametrize("evidence_shape", ["text", "list", "object"])
@pytest.mark.usefixtures("legacy_whole_plan_path")
def test_authored_visual_evidence_reaches_real_harness_without_replanning(
    client: TestClient, monkeypatch, evidence_shape: str,
) -> None:
    gateway = _gateway(client)
    original_plan = gateway.plan
    note = "商品参考图中可见的主体轮廓、边缘关系与局部结构。"
    authored = {}
    evidence = {
        "text": note,
        "list": [note],
        "object": {
            "evidence_class": "DIRECT_VERIFIED",
            "proof_device": "商品参考图中的可见关系",
            "visible_content": [note],
            "forbidden_inventions": ["不得增加来源图中不存在的结构"],
        },
    }[evidence_shape]

    async def authored_evidence(request):
        result = await original_plan(request)
        if request.planning_profile == "aurora_domestic_intake":
            return result
        payload = deepcopy(result.payload)
        for module in payload["modules"]:
            module["visual_evidence"] = deepcopy(evidence)
        authored.update(payload)
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", authored_evidence)
    source = upload_asset(client, filename=f"evidence-{evidence_shape}.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert len(gateway.planner_calls) == 2
    native = normalize_native_plan_for_harness(authored)
    assert all(m["visual_evidence"]["visible_content"] == [note] for m in native["modules"])
    assert gateway.image_calls == []


@pytest.mark.usefixtures("legacy_whole_plan_path")
def test_module_repair_receives_the_actual_invalid_enum_and_path(
    client: TestClient, monkeypatch,
) -> None:
    gateway = _gateway(client)
    original_plan = gateway.plan
    healthy_module = {}

    async def invalid_enum(request):
        result = await original_plan(request)
        if request.planning_profile == "aurora_domestic_intake":
            return result
        payload = deepcopy(result.payload)
        if request.planning_profile == "aurora_domestic_detail":
            healthy_module.update(deepcopy(payload["modules"][1]))
            payload["modules"][1]["secondary_subject_classes"] = ["MATERIAL_OR_INGREDIENT"]
        elif request.planning_profile == "aurora_domestic_modules_completion":
            issues = next(v for v in request.product_information if v.startswith("VALIDATION_ERRORS_JSON="))
            assert "/modules/1/secondary_subject_classes/0" in issues
            assert "MATERIAL_OR_INGREDIENT" in issues
            assert "INGREDIENT_OR_MATERIAL" in issues
            payload = {"modules": [healthy_module]}
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", invalid_enum)
    source = upload_asset(client, filename="enum-error-context.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert [r.planning_profile for r in gateway.planner_calls] == ["aurora_domestic_intake", "aurora_domestic_detail", "aurora_domestic_modules_completion"]
    assert "TARGET_MODULE_ID=M02" in gateway.planner_calls[-1].product_information
    assert gateway.image_calls == []


def test_domestic_product_and_style_sources_keep_distinct_durable_roles(
    client: TestClient,
) -> None:
    product = upload_asset(client, filename="domestic-role-product.png")
    style = upload_asset(client, filename="domestic-role-style.png")
    job = _create_detail_job(
        client,
        product["id"],
        market_region="domestic",
        auto_start_after_plan=True,
        reference_asset_ids=[style["id"]],
    )
    gateway = _gateway(client)

    assert dispatch_pending(client) == 1
    assert len(gateway.planner_calls) == 6
    planned = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert confirmed.status_code == 202, confirmed.text
    assert dispatch_pending(client) == 1
    reviewed = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert reviewed["status"] == JobStatus.AWAITING_FINAL_REVIEW.value
    assert len(gateway.image_calls) == 4
    assert [
        source.source_role for source in gateway.planner_calls[0].sources
    ] == ["product_reference", "style_reference"]
    image_roles = [source.source_role for source in gateway.image_calls[0].sources]
    assert "format_canvas" not in image_roles
    assert image_roles[0] == "product_reference"
    assert image_roles == ["product_reference"]
    planner_prompt = _planner_prompt(gateway.planner_calls[1])
    assert "concise ordered atomic" in planner_prompt
    assert "locked_intake" in planner_prompt
    assert "reference_materials" in planner_prompt
    image_prompt = gateway.image_calls[0].prompt
    assert image_prompt.startswith("CONTINUOUS_PAIR_RENDERING_LOCKED")
    assert "3:8" in image_prompt
    assert "PAIR_OUTPUT_LOCK" in image_prompt

    with client.app.state.session_factory() as session:
        item = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job["id"])
        )
        assert item is not None
        relations = session.scalars(
            select(JobAssetRecord)
            .where(JobAssetRecord.job_id == job["id"])
            .order_by(JobAssetRecord.role)
        ).all()
        reservation = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == item.id,
                AuditEventRecord.action == "xinghe.image_reserved",
            )
        )
    assert [source["role"] for source in item.configuration["source_assets"]] == [
        "product_reference",
        "style_reference",
    ]
    assert {(relation.asset_id, relation.role) for relation in relations} == {
        (product["id"], "product_reference"),
        (style["id"], "style_reference"),
    }
    assert reservation is not None
    assert reservation.details["source_role_counts"] == {
        "product_reference": 1,
    }


def test_domestic_protocol_fields_are_preserved_and_compiled_into_prompt() -> None:
    request = PlannerRequest(
        workspace_id="ws-domestic",
        job_id="job-domestic",
        group_key="primary",
        sources=(),
        page_count=2,
        language="zh-CN",
        product_name="",
        product_information=(),
        platform_code="tmall",
        size="1200x1600",
        idempotency_key="offline",
        domestic_protocol=True,
    )
    payload = deepcopy(_fake_plan(request))
    payload["analysis"]["buyer_demand_map"] = {
        "priority": ["trust", "proof"]
    }
    payload["analysis"]["information_confidence"] = {
        "visible": ["shape"],
        "needs_confirmation": ["material"],
    }
    payload["plan"]["reference_visual_style_lock"] = {
        "hierarchy": "editorial",
        "page_rhythm": "impact then proof",
    }
    payload["plan"]["screens"][0].update(
        {
            "buyer_demand": "Can I trust what I see?",
            "matched_selling_point": "Visible structure",
            "visual_proof": {"method": "material macro"},
            "risk_reminder": "Do not infer material",
            "physical_logic_lock": {"contact": "grounded"},
        }
    )

    structured = validate_planner_payload(
        payload,
        2,
        preserve_protocol_fields=True,
    )
    assert structured["analysis"]["buyer_demand_map"] == {
        "priority": ["trust", "proof"]
    }
    assert structured["plan"]["reference_visual_style_lock"]["hierarchy"] == (
        "editorial"
    )
    assert structured["plan"]["screens"][0]["visual_proof"] == {
        "method": "material macro"
    }
    prompt = compile_page_prompt(
        structured_plan=structured,
        screen=structured["plan"]["screens"][0],
        page_number=1,
        page_count=2,
        language="zh-CN",
        product_name="",
        product_information=(),
        source_count=2,
        source_roles=("product_reference", "style_reference"),
        size="1200x1600",
        include_protocol_fields=True,
    )
    assert "visible product from supplied images" in prompt
    assert '"buyer_demand_map":{"priority":["trust","proof"]}' in prompt
    assert '"reference_visual_style_lock"' in prompt
    assert '"buyer_demand":"Can I trust what I see?"' in prompt
    assert '"visual_proof":{"method":"material macro"}' in prompt
    assert '"risk_reminder":"Do not infer material"' in prompt
    assert '"physical_logic_lock":{"contact":"grounded"}' in prompt


def test_domestic_protocol_fields_survive_planning_and_reach_generation(
    client: TestClient,
    monkeypatch,
) -> None:
    source = upload_asset(client, filename="domestic-protocol-fields.png")
    job = _create_detail_job(
        client,
        source["id"],
        market_region="domestic",
        auto_start_after_plan=True,
    )
    gateway = _gateway(client)
    original_plan = gateway.plan

    async def plan_with_protocol(request):
        result = await original_plan(request)
        if request.planning_profile == "aurora_domestic_intake":
            return result
        payload = deepcopy(result.payload)
        if request.authoring_stage == "story":
            payload["campaign_thesis"] = "以真实可见结构回答买家信任问题。"
            payload["modules"][0]["visual_proof_direction"] = "展示可见结构证据"
        elif request.authoring_stage == "pair":
            for module in payload["modules"]:
                if module["id"] == "M01":
                    module["visual_proof_direction"] = "展示可见结构证据"
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", plan_with_protocol)
    assert dispatch_pending(client) == 1

    with client.app.state.session_factory() as session:
        plan = session.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == job["id"])
        )
        item = session.scalar(
            select(JobItemRecord).where(JobItemRecord.job_id == job["id"])
        )
    assert plan is not None
    assert item is not None
    group = plan.payload["groups"][0]
    assert "aurora_plan" in group["plan_protocol_fields"], sorted(group["plan_protocol_fields"])
    assert "campaign_thesis" in group["plan_protocol_fields"]["aurora_plan"], sorted(group["plan_protocol_fields"]["aurora_plan"])
    assert group["plan_protocol_fields"]["aurora_plan"]["campaign_thesis"] == (
        "以真实可见结构回答买家信任问题。"
    )
    assert "aurora_module" in plan.payload["pages"][0], sorted(
        plan.payload["pages"][0]
    )
    assert plan.payload["pages"][0]["aurora_module"]["visual_proof_direction"] == (
        "展示可见结构证据"
    )
    assert "真实可见结构回答买家信任问题" in item.configuration["prompt"]
    assert "展示可见结构证据" in item.configuration["prompt"]


def test_domestic_resume_signature_includes_source_role() -> None:
    base = {
        "groups": [
            (
                "primary",
                [
                    ("product_reference", "product.png", "a" * 64),
                    ("style_reference", "style.png", "b" * 64),
                ],
            )
        ],
        "base_url": "https://api.example.test",
        "planner_model": "planner",
        "image_model": "image",
        "page_count": 8,
        "language": "zh-CN",
        "product_name": "visible",
        "product_information": [],
        "platform_code": "tmall",
        "size": IMAGE_SIZE,
        "medium_price_micros": 45_000,
        "high_price_micros": 90_000,
    }
    original = build_resume_signature(**base)
    changed = deepcopy(base)
    changed["groups"][0][1][1] = (
        "product_reference",
        "style.png",
        "b" * 64,
    )
    assert build_resume_signature(**changed) != original


def test_domestic_internal_empty_product_name_uses_visible_product_fallback(
    client: TestClient,
) -> None:
    source = upload_asset(client, filename="domestic-empty-name.png")
    job = _create_detail_job(
        client,
        source["id"],
        market_region="domestic",
        auto_start_after_plan=True,
    )
    with client.app.state.session_factory() as session:
        record = session.get(JobRecord, job["id"])
        assert record is not None
        configuration = deepcopy(record.configuration)
        configuration["settings"]["product_name"] = ""
        record.configuration = configuration
        session.commit()

    assert dispatch_pending(client) == 1
    gateway = _gateway(client)
    planned = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert confirmed.status_code == 202, confirmed.text
    assert dispatch_pending(client) == 1
    reviewed = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert reviewed["status"] == JobStatus.AWAITING_FINAL_REVIEW.value
    assert gateway.planner_calls[0].product_name == (
        "visible product from supplied images"
    )
    assert "visible product from supplied images" in gateway.image_calls[0].prompt


def test_international_plan_requires_confirmation_then_waits_for_manual_qa(
    client: TestClient,
) -> None:
    source = upload_asset(client, filename="international-manual-confirm.png")
    job = _create_detail_job(
        client,
        source["id"],
        market_region="international",
        auto_start_after_plan=False,
    )
    gateway = _gateway(client)

    assert dispatch_pending(client) == 1
    planned = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert planned["plan"]["confirmed_at"] is None
    assert len(gateway.planner_calls) == 1
    assert gateway.image_calls == []
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0

    assert (
        asyncio.run(client.app.state.task_runner.run(str(job["id"])))
        == JobStatus.AWAITING_CONFIRMATION
    )
    assert len(gateway.planner_calls) == 1
    assert gateway.image_calls == []
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0

    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json={"expected_revision": planned["plan"]["revision"]},
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["status"] == JobStatus.PROCESSING.value
    assert dispatch_pending(client) == 1

    completed = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert completed["status"] == JobStatus.AWAITING_FINAL_REVIEW.value
    assert completed["stage"] == "awaiting_user_final_review"
    assert len(gateway.image_calls) == 8
    assert len(gateway.planner_calls) == 1
    assert _auto_confirm_audit_count(client, str(job["id"])) == 0


def test_compact_completion_module_merges_without_runtime_derived_fields() -> None:
    request = PlannerRequest(
        workspace_id="ws_development",
        job_id="job_plan_contract",
        group_key="primary",
        sources=(),
        page_count=8,
        language="zh-CN",
        product_name="路斯冻干猫粮",
        product_information=("全价冻干多拼猫粮",),
        platform_code="tmall",
        size=IMAGE_SIZE,
        idempotency_key="plan-contract",
        planning_profile="aurora_domestic_detail",
    )
    module = _fake_plan(request)["modules"][0]
    module["visual_evidence"] = {
        "evidence_class": "DIRECT_VERIFIED",
        "proof_device": "用户商品照片",
        "visible_content": ["照片中的商品轮廓与结构"],
        "forbidden_inventions": ["不得增加未提供的结构"],
    }
    for key in (
        "order",
        "visual_proof_contract",
        "module_spatial_structure_contract",
        "copy_intent",
        "text_system",
        "reading_path",
        "copy_density",
        "visual_payload_density",
        "proof_relation_strength",
        "commercial_energy",
        "breathing_screen",
        "brand_anchor",
        "independent_logo_policy",
        "independent_logo_source_id",
        "inherited_dna_tokens",
        "output",
    ):
        module.pop(key, None)
    accepted = _accept_aurora_completion_module({"modules": [module]}, "M01")
    bare = _accept_aurora_completion_module(dict(module), "M01")
    foreign = dict(module)
    foreign["id"] = "M09"
    assert accepted is not None and accepted["id"] == "M01"
    assert bare is not None and bare["lead_conclusion"] == module["lead_conclusion"]
    assert _accept_aurora_completion_module({"modules": [foreign]}, "M01") is None
    assert _accept_aurora_completion_module(
        {"analysis": {}, "plan": {"screens": []}},
        "M01",
    ) is None
    prompt = _planner_prompt(request)
    assert "plan_format=COMPACT_V1_3" in prompt
    assert "Do not emit runtime-derived" in prompt


@pytest.mark.parametrize("region, expected_requests", [("domestic", 37), ("international", 17)])
def test_detail_provider_reservation_covers_bounded_authoring_and_pixel_reviews(monkeypatch, region, expected_requests):
    import app.routes_v1 as routes

    captured = {}
    monkeypatch.setattr(routes, "_ensure_detail_skill_provider_enabled", lambda *args: None)
    monkeypatch.setattr(routes, "reserve_provider_job_quota", lambda session, **kwargs: captured.update(kwargs))
    settings = SimpleNamespace(task_runner="tengli", tengli_planner_reserve_micros=250000,
        tengli_daily_budget_usd=100, tengli_monthly_budget_usd=1000, tengli_daily_request_limit=1000)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    job = JobRecord(id="job_reservation", type="detail_pack", expected_item_count=8,
        estimated_cost_max_micros=720000,
        configuration={"input": {"product_groups": [{"group_key": "primary"}]},
                       "settings": {"market_region": region, "page_count": 8}})
    routes._reserve_real_provider_job_if_enabled(None, request, job, "user_test")
    assert captured["provider_request_count"] == expected_requests
    if region == "domestic":
        assert captured["estimated_cost_micros"] >= 19 * 250000 + 8 * 90000


def test_full_pair_retry_uses_new_canvas_while_second_slice_only_retry_uses_source_canvas(client):
    from app.domain import RetryScope
    from app.routes_v1 import _copy_job_for_retry, _copy_job_relations_for_retry

    source = upload_asset(client, filename="retry-canvas-source.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    dispatch_pending(client)
    with client.app.state.session_factory() as session:
        source_job = session.get(JobRecord, job["id"])
        items = session.scalars(select(JobItemRecord).where(JobItemRecord.job_id == job["id"]).order_by(JobItemRecord.ordinal)).all()
        full = _copy_job_for_retry(session, source_job, RetryScope.FULL, [i.id for i in items])
        session.add(full)
        session.flush()
        _copy_job_relations_for_retry(session, source_job, full, [i.id for i in items])
        second = _copy_job_for_retry(session, source_job, RetryScope.SELECTED, [items[1].id])
        session.add(second)
        session.flush()
        _copy_job_relations_for_retry(session, source_job, second, [items[1].id])
        session.flush()
        full_items = session.scalars(select(JobItemRecord).where(JobItemRecord.job_id == full.id)).all()
        second_item = session.scalar(select(JobItemRecord).where(JobItemRecord.job_id == second.id, JobItemRecord.ordinal == 1))
        assert all("aurora_pair_source_job_id" not in i.configuration for i in full_items)
        assert second_item.configuration["aurora_pair_source_job_id"] == source_job.id


@pytest.mark.parametrize("page_count", [8, 10, 12])
@pytest.mark.parametrize("reason", ["UNSUPPORTED_INVENTION", "HIGH_RISK_UNDOCUMENTED"])
@pytest.mark.usefixtures("legacy_whole_plan_path")
def test_deferred_reason_passes_real_compiler_without_replanning_or_generation(
    client: TestClient, monkeypatch, page_count: int, reason: str,
) -> None:
    """Exercise the same compiler boundary as production with synthetic inputs."""
    gateway = _gateway(client)
    original_plan = gateway.plan

    async def with_deferred_claim(request):
        result = await original_plan(request)
        if request.planning_profile == "aurora_domestic_intake":
            return result
        payload = deepcopy(result.payload)
        if request.planning_profile == "aurora_domestic_detail":
            strategy = payload["output_strategy"]
            strategy["claim_coverage"] = [row for row in strategy["claim_coverage"] if row["claim_id"] != "F02"]
            for module in payload["modules"]:
                module["supporting_claim_ids"] = [value for value in module["supporting_claim_ids"] if value != "F02"]
            strategy["claim_coverage"].append({
                "claim_id": "F02", "disposition": "DEFERRED", "module_ids": [],
                "rationale": "This unsupported performance claim must remain deferred.",
                "defer_reason_code": reason,
            })
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", with_deferred_claim)
    source = upload_asset(client, filename="synthetic-deferred-claim.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic",
        auto_start_after_plan=False, page_count=page_count,
    )
    with client.app.state.session_factory() as session:
        record = session.get(JobRecord, job["id"])
        configuration = deepcopy(record.configuration)
        configuration["settings"]["selling_points"] = ["Visible product identity from the test source."]
        configuration["settings"]["verified_facts"] = ["Unsupported performance claim held out of all screens."]
        record.configuration = configuration
        session.commit()
    dispatch_pending(client)
    planned = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    assert planned["plan"]["confirmed_at"] is None
    assert [r.planning_profile for r in gateway.planner_calls] == ["aurora_domestic_intake", "aurora_domestic_detail"]
    assert gateway.image_calls == []
    native = planned["plan"]["payload"]["groups"][0]["plan_protocol_fields"]["aurora_plan"]
    coverage = native["output_strategy"]["claim_coverage"]
    claim = next(item for item in coverage if item["claim_id"] == "F02")
    assert claim["disposition"] == "DEFERRED"
    assert claim["defer_reason_code"] == reason
    assert claim["module_ids"] == []


@pytest.mark.parametrize("missing_observation", ["checks", "screen_coverage", "information_audit"])
def test_unknown_native_review_stops_after_one_image_without_paid_repair(
    client: TestClient, monkeypatch, missing_observation,
) -> None:
    async def incomplete(self, request, native_request, sources, artifact):
        del request, sources
        raw = self._native_detail_judgment(native_request, artifact)
        del raw[missing_observation]
        return self._normalize_native_pair_judgment(raw, native_request, artifact)

    monkeypatch.setattr(XingheImageProvider, "_native_detail_judgment_with_review", incomplete)
    source = upload_asset(client, filename="unknown-audit.png")
    job = _create_detail_job(client, source["id"], market_region="domestic", auto_start_after_plan=False)
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(), json=_aurora_approval_payload(planned),
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert current["status"] == "failed"
    assert current["items"][0]["error_code"] == "AURORA_NATIVE_AUDIT_INVALID"
    assert len(_gateway(client).image_calls) == 1
    native = json.loads((detail_workspace(job["id"]) / "session.json").read_text(encoding="utf-8"))
    assert native["state"] == "AWAITING_PIXEL_AUDIT"
    assert all(len(module["attempts"]) == 1 and not module.get("accepted_asset") for module in native["modules"][:2])
