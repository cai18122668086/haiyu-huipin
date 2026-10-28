"""A real public attempt owns semantic preflight and a separate native fork."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
from app.domestic_detail.generation import generation_binding
from app.domestic_detail.revision_authorization import REVISION_APPROVAL_TEXT
from app.domestic_detail.revision_jobs import create_revision_job
from app.domestic_detail.revision_protocol import SCOPE_PROFILE, RevisionScopeError
from app.domestic_detail.revision_runtime import prepare_revision
from app.models import (
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    WorkspaceMemberRecord,
    utc_now,
)
from app.provider import ProviderRequest
from app.services import new_id
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_automatic_delivery import authorize_body
from tests.test_domestic_detail_delivery import (
    REVIEWS,
    explicit_synthetic_delivery,  # noqa: F401
    ready,
)
from tests.test_domestic_detail_execution import _gateway
from tests.test_domestic_detail_plans import planned_job
from tests.test_domestic_detail_revision_scope import model_result, observation
from tests.test_native_detail_local_adjustment import adjustment, inventory


@pytest.mark.parametrize(
    "automatic,scope_overrun,layout_override,pixel_review",
    [
        (False, False, False, "none"),
        (True, False, False, "none"),
        (False, True, False, "none"),
        (False, False, True, "complete"),
        (False, False, True, "repair_identity"),
        (False, False, True, "missing_local"),
        (False, False, True, "overrun"),
    ],
)
def test_public_lease_current_authority_then_one_scope_call_and_owned_fork(
    client, automatic, scope_overrun, layout_override, pixel_review, monkeypatch
):
    if automatic:
        planned = planned_job(client)
        client.app.state.settings.max_image_pixels = 6_000_000
        client.app.state.storage.validator.max_image_pixels = 6_000_000
        prefix, _, body = authorize_body(client, planned)
        response = client.post(prefix + "/plan/confirm", headers=idem(), json=body)
        assert response.status_code == 202, response.text
        dispatch_pending(client)
        response = client.post(prefix + "/detail-final-review")
        assert response.status_code == 200, response.text
        source_id, fingerprint = planned["id"], response.json()["sha256"]
    else:
        source = ready(client)
        source_id, fingerprint = source["id"], REVIEWS[source["id"]]
        prefix = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source_id}"
    client.app.state.settings.provider_quota_enabled = True
    gateway = _gateway(client)
    if pixel_review != "none":
        monkeypatch.setattr(gateway, "provider_name", "offline-local-review")
    delta = adjustment("M04")
    response = client.post(
        prefix + "/local-revision/quote",
        json={"final_review_sha256": fingerprint, "adjustment": delta},
    )
    assert response.status_code == 200, response.text
    quote = response.json()
    factory = client.app.state.session_factory
    gateway = _gateway(client)
    original_inventory = inventory(detail_workspace(source_id))
    with factory() as db:
        source_record = db.get(JobRecord, source_id)
        original_source_configuration = deepcopy(source_record.configuration)
        source_plan = db.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == source_id)
        )
        original_source_payload = deepcopy(source_plan.payload)
        child = asyncio.run(
            create_revision_job(
                db,
                source_record,
                client.app.state.storage,
                client.app.state.settings,
                gateway,
                actor_id=quote["actor_user_id"],
                fingerprint=fingerprint,
                adjustment=delta,
                quote_sha256=quote["sha256"],
                approval_text=REVISION_APPROVAL_TEXT,
            )
        )
        db.flush()
        child_id = child.id
        item = db.scalar(
            select(JobItemRecord).where(
                JobItemRecord.job_id == child_id, JobItemRecord.ordinal == 2
            )
        )
        child.status = "processing"
        item.status = "running"
        attempt = JobAttemptRecord(
            id=new_id("attempt"),
            workspace_id=WORKSPACE_ID,
            job_id=child_id,
            job_item_id=item.id,
            attempt_no=1,
            status="running",
            lease_token="explicit-offline-lease",
            leased_by="local-isolated-test",
            lease_expires_at=utc_now() + timedelta(hours=1),
            provider="synthetic",
            operation="detail_pack",
            provider_idempotency_key="scope-original-public-attempt",
        )
        db.add(attempt)
        db.flush()
        attempt_id = attempt.id
        child_plan = db.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == child_id)
        )
        group = child_plan.payload["source_asset_manifest"][0]
        binding = generation_binding(
            child_plan.payload,
            child.configuration["settings"],
            group["assets"],
            group_key=group["group_key"],
            job_input=child.configuration["input"],
        )
        request = ProviderRequest(
            workspace_id=WORKSPACE_ID,
            job_id=child_id,
            job_item_id=item.id,
            attempt_id=attempt.id,
            operation="detail_pack",
            idempotency_key=attempt.provider_idempotency_key,
            configuration={
                "job": deepcopy(child.configuration),
                "item": deepcopy(item.configuration),
            },
        )
        db.commit()
    before_calls = len(gateway.planner_calls), len(gateway.image_calls)
    original_plan_call = gateway.plan

    async def plan(call):
        if call.planning_profile == SCOPE_PROFILE:
            gateway.planner_calls.append(call)
            from dataclasses import replace

            observation_value = observation(call)
            if layout_override:
                context = json.loads(call.product_information[0])
                selected = context["effective_module_contracts"][1]["contract"]
                old = selected["text_system"]["anchor_zone"]
                observation_value["changes"][0]["contract_effect"] = (
                    "OVERRIDE_LOCAL_FIELDS"
                )
                observation_value["contract_overrides"] = [
                    {
                        "module_id": "M04",
                        "change_index": 0,
                        "path": "/text_system/anchor_zone",
                        "original_value": old,
                        "replacement_value": "edge_left"
                        if old != "edge_left"
                        else "lower_left",
                    }
                ]
            value = model_result(call, observation_value)
            return (
                replace(value, actual_cost_micros=quote["review_reserve_micros"] + 1)
                if scope_overrun
                else value
            )
        return await original_plan_call(call)

    gateway.plan = plan

    def run():
        return asyncio.run(
            prepare_revision(
                factory=factory,
                request=request,
                settings=client.app.state.settings,
                storage=client.app.state.storage,
                gateway=gateway,
                runner=AuroraDetailHarnessRunner(),
                binding=binding,
            )
        )

    # These fail before source rehydration or any new supplier dispatch.
    for invalid in ["lease", "cancel", "membership"]:
        with factory() as db:
            if invalid == "lease":
                db.get(JobAttemptRecord, attempt_id).lease_expires_at = (
                    utc_now() - timedelta(seconds=1)
                )
            elif invalid == "cancel":
                db.get(JobRecord, child_id).cancel_requested_at = utc_now()
            else:
                db.get(
                    WorkspaceMemberRecord, (WORKSPACE_ID, quote["actor_user_id"])
                ).role = "viewer"
            db.commit()
        with pytest.raises(RevisionScopeError):
            run()
        assert (len(gateway.planner_calls), len(gateway.image_calls)) == before_calls
        with factory() as db:
            if invalid == "lease":
                db.get(JobAttemptRecord, attempt_id).lease_expires_at = (
                    utc_now() + timedelta(hours=1)
                )
            elif invalid == "cancel":
                db.get(JobRecord, child_id).cancel_requested_at = None
            else:
                db.get(
                    WorkspaceMemberRecord, (WORKSPACE_ID, quote["actor_user_id"])
                ).role = "owner"
            db.commit()
    if scope_overrun:
        for _ in range(2):
            with pytest.raises(RevisionScopeError, match="single-call"):
                run()
        root = detail_workspace(child_id)
        assert (root / "local-scope-model-result.json").exists()
        assert not (root / "session.json").exists()
        with factory() as db:
            plan = db.scalar(
                select(JobPlanRecord).where(JobPlanRecord.job_id == child_id)
            )
            assert len(plan.payload["usage"]["planner"]) == 1
            assert (
                plan.payload["usage"]["planner"][0]["actual_cost_micros"]
                == quote["review_reserve_micros"] + 1
            )
        assert inventory(detail_workspace(source_id)) == original_inventory
        assert len(gateway.planner_calls) == before_calls[0] + 1
        assert len(gateway.image_calls) == before_calls[1]
        return
    if layout_override:
        from app.domestic_detail import revision_accounts

        with monkeypatch.context() as patcher:

            def unavailable(gateway):
                raise RevisionScopeError(
                    "ACCOUNT_FIXTURE", "Account configuration unavailable"
                )

            patcher.setattr(
                revision_accounts, "configured_revision_accounts", unavailable
            )
            with pytest.raises(RevisionScopeError, match="configuration unavailable"):
                run()
        assert (len(gateway.planner_calls), len(gateway.image_calls)) == before_calls
        assert not (
            detail_workspace(child_id) / "local-scope-model-result.json"
        ).exists()
    assert run()
    root = detail_workspace(child_id)
    configured = json.loads(
        (root / "local-revision-account-configuration.json").read_bytes()
    )
    assert (
        configured["intent_sha256"]
        == request.configuration["job"]["local_revision"]["sha256"]
    )
    assert configured["quote_sha256"] == quote["sha256"]
    assert configured["accounts"]["configuration_checked"]
    assert not configured["accounts"]["remote_authorization_verified"]
    assert not configured["accounts"]["remote_balance_verified"]
    first_inventory = inventory(root)
    state = json.loads((root / "session.json").read_bytes())
    assert state["state"] == "AUTHORIZED" and state["current_index"] == 2
    assert state["approval"]["delivery_mode"] == "USER_FINAL_REVIEW"
    assert state["revision_fork"]["source_job_id"] == source_id
    assert state["revision_fork"]["module_ids"] == ["M03", "M04"]
    assert state["modules"][3]["user_adjustment"] == delta
    assert state["modules"][2]["user_adjustment"]["change_only"] != delta["change_only"]
    assert all(
        m["status"] in {"COMPLETE", "COMPLETE_WITH_RESIDUALS"}
        for m in state["modules"]
        if m["id"] not in {"M03", "M04"}
    )
    source_binding = json.loads(
        (detail_workspace(source_id) / "generation-binding.json").read_bytes()
    )
    child_binding = json.loads((root / "generation-binding.json").read_bytes())
    assert (
        child_binding["sha256"] == binding["sha256"]
        and child_binding["sha256"] != source_binding["sha256"]
    )
    assert (
        hashlib.sha256((root / "source-session.json").read_bytes()).hexdigest()
        == quote["source_native_session_sha256"]
    )
    assert (
        len(gateway.planner_calls) == before_calls[0] + 1
        and len(gateway.image_calls) == before_calls[1]
    )
    with factory() as db:
        plan = db.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == child_id))
        model = plan.payload["usage"]["planner"]
        assert len(model) == 1 and model[0]["phase"] == SCOPE_PROFILE
        assert model[0]["local_scope_binding_sha256"]
        first_usage = deepcopy(model)
    from app.domestic_detail import revision_accounts

    with monkeypatch.context() as patcher:

        def unavailable(gateway):
            raise AssertionError(
                "Paid journal replay must not perform a new account check"
            )

        patcher.setattr(revision_accounts, "configured_revision_accounts", unavailable)
        assert run()
    assert inventory(root) == first_inventory
    with factory() as db:
        plan = db.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == child_id))
        assert plan.payload["usage"]["planner"] == first_usage
        source_record = db.get(JobRecord, source_id)
        assert source_record.configuration == original_source_configuration
        source_plan = db.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == source_id)
        )
        assert source_plan.payload == original_source_payload
    assert inventory(detail_workspace(source_id)) == original_inventory
    assert (
        len(gateway.planner_calls) == before_calls[0] + 1
        and len(gateway.image_calls) == before_calls[1]
    )

    # Compile the first local image request and validate the actual accepted edit base.
    # No image provider call is made in this preparation-stage test.
    from app.domestic_detail.edit_canvas import resolve_native_edit_canvas

    runner = AuroraDetailHarnessRunner()
    request_path = root / "first-local-request.json"
    if layout_override:
        from app.config import default_skill_root
        from app.xinghe_agent import XingheImageProvider

        provider = XingheImageProvider(
            factory,
            gateway,
            storage=client.app.state.storage,
            skill_root=default_skill_root("aurora-domestic-ecommerce-detail-page"),
            skill_id="aurora-domestic-ecommerce-detail-page",
            max_concurrency=1,
        )
        configuration, sources = provider._authorize(request)
        sources = asyncio.run(provider._load_sources(request.workspace_id, sources))
        assert provider._generation_scope(request, sources) == binding
        _, native_request, ordered = asyncio.run(
            provider._prepare_native_detail_generation(
                request,
                configuration,
                sources,
                binding,
            )
        )
        patch = json.loads((root / "local-revision-scope.json").read_bytes())[
            "original_observation"
        ]["contract_overrides"][0]
        contracts = native_request["approved_module_contracts"]
        assert (
            contracts[1]["contract"]["text_system"]["anchor_zone"]
            == patch["replacement_value"]
        )
        assert contracts[0]["contract"] == state["modules"][2]["plan"]
        assert (
            state["modules"][3]["plan"]["text_system"]["anchor_zone"]
            == patch["original_value"]
        )
        assert (
            native_request["local_contract_bindings"][1]["effective_plan_sha256"]
            == contracts[1]["contract_sha256"]
        )
        assert any(source.source_role == "generated_edit_canvas" for source in ordered)
    else:
        runner.run(
            "generate-next",
            root,
            {"session": root / "session.json", "out": request_path},
        )
        native_request = json.loads(request_path.read_bytes())
    issued_state = json.loads((root / "session.json").read_bytes())
    edit = resolve_native_edit_canvas(
        runner, root, issued_state, native_request, binding
    )
    assert edit is not None
    canvas, edit_proof = edit
    assert canvas.sha256 == quote["source_pair_canvas"]["sha256"]
    assert edit_proof["source_role"] == "generated_edit_canvas"
    assert hashlib.sha256(canvas.payload).hexdigest() == canvas.sha256
    assert len(edit_proof["original_response_sha256"]) == 64
    assert inventory(detail_workspace(source_id)) == original_inventory
    assert len(gateway.image_calls) == before_calls[1]
    if pixel_review != "none":
        from tests.domestic_detail_revision_review_fixtures import (
            run_local_review_boundary,
        )

        run_local_review_boundary(
            client,
            monkeypatch,
            provider,
            gateway,
            request,
            binding,
            native_request,
            ordered,
            configuration,
            sources,
            pixel_review,
        )
        assert inventory(detail_workspace(source_id)) == original_inventory
        assert len(gateway.image_calls) == before_calls[1]


@pytest.mark.parametrize("sent", [False, True])
def test_scope_ledger_charges_zero_only_for_validated_not_sent_trace(client, sent):
    import asyncio

    from app.aurora_detail_runtime import detail_workspace
    from app.domestic_detail.planner_dispatch import dispatch_metadata, route_descriptor
    from app.domestic_detail.plans import canonical_hash
    from app.domestic_detail.revision_protocol import RevisionScopeError
    from app.domestic_detail.revision_runtime import account_scope_result
    from app.domestic_detail.revision_scope import run_scope_check
    from app.models import AuditEventRecord, JobPlanRecord, JobRecord, utc_now
    from app.provider import ProviderRequest
    from app.provider_quota_ledger import _planner_charges
    from app.services import new_id
    from app.xinghe_agent import FakeXingheGateway, PlannerCallResult, normalize_usage
    from tests.test_api import WORKSPACE_ID
    from tests.test_domestic_detail_revision_scope import context_fixture

    context, _ = context_fixture()
    factory = client.app.state.session_factory
    job_id = new_id("job")
    intent_sha = context["intent_sha256"]
    with factory() as db:
        job = JobRecord(
            id=job_id,
            workspace_id=WORKSPACE_ID,
            type="detail_pack",
            status="processing",
            expected_item_count=0,
            configuration={"local_revision": {"sha256": intent_sha}},
            estimated_cost_max_micros=1_430_000,
        )
        db.add(job)
        db.flush()
        payload = {"usage": {"planner": []}, "planner_diagnostics": []}
        plan_id = new_id("plan")
        db.add(
            JobPlanRecord(
                id=plan_id,
                workspace_id=WORKSPACE_ID,
                job_id=job_id,
                revision=1,
                payload=payload,
                payload_hash=canonical_hash(payload),
                confirmed_at=utc_now(),
                validation_status="valid",
            )
        )
        db.commit()
    request = ProviderRequest(
        workspace_id=WORKSPACE_ID,
        job_id=job_id,
        job_item_id="billing-only",
        attempt_id="original-scope-billing",
        operation="detail_pack",
        idempotency_key="original-scope-billing",
        configuration={},
    )
    route = route_descriptor(
        provider="offline-fixture",
        route="one",
        model="scope",
        endpoint="https://example.invalid",
        credential="fixture-only",
    )
    gateway = FakeXingheGateway()
    gateway.native_planner_route = lambda: route
    calls = []

    async def plan(call):
        calls.append(call)
        code = "EXPLICIT_OFFLINE_SCOPE_FAILURE"
        return PlannerCallResult(
            payload=None,
            provider_request_id=None,
            raw_usage=None,
            normalized_usage=normalize_usage(None),
            api_latency_seconds=0,
            actual_cost_micros=None,
            error_code=code,
            external_request=sent,
            provider_envelope={
                "native_planner_dispatch": dispatch_metadata(
                    route,
                    attempts=1 if sent else 0,
                    response_received=False,
                    status=None,
                    error_code=code,
                )
            },
        )

    gateway.plan = plan
    for _ in range(2):
        with pytest.raises(RevisionScopeError):
            asyncio.run(
                run_scope_check(
                    root=detail_workspace(job_id),
                    workspace_id=WORKSPACE_ID,
                    job_id=job_id,
                    context=context,
                    gateway=gateway,
                    before_dispatch=lambda: None,
                    account_result=lambda result, key: account_scope_result(
                        factory, request, intent_sha, result, key
                    ),
                )
            )
    assert len(calls) == 1
    with factory() as db:
        job = db.get(JobRecord, job_id)
        plan = db.get(JobPlanRecord, plan_id)
        assert len(plan.payload["usage"]["planner"]) == (1 if sent else 0)
        receipt = plan.payload["local_scope_billing_receipt"]
        assert receipt["provider_request_count"] == (1 if sent else 0)
        unit = 250_000
        reservation = AuditEventRecord(
            workspace_id=WORKSPACE_ID,
            action="explicit-fixture",
            resource_type="job",
            resource_id=job_id,
            request_id=job_id,
            details={"planner_reserve_micros": unit},
        )
        _, count, cost, _ = _planner_charges(db, job, reservation, 1_430_000, 7, 0, [])
        assert count == (1 if sent else 0) and cost == (unit if sent else 0)
        if not sent:
            assert receipt["charged_cost_micros"] == 0
            assert len(plan.payload["planner_diagnostics"]) == 1
            assert (
                plan.payload["planner_diagnostics"][0]["source"]
                == "native_scope_not_sent"
            )
