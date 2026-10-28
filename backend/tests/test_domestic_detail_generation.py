from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.aurora_detail_runtime import detail_workspace
from app.domestic_detail.generation import (
    DetailGenerationError,
    begin_invocation,
    bind_workspace,
    finish_invocation,
    verify_original_invocation,
)
from app.models import (
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    utc_now,
)
from app.provider import ProviderOutcome
from tests.test_api import WORKSPACE_ID, idem
from tests.test_domestic_detail_execution import _aurora_approval_payload, _gateway
from tests.test_domestic_detail_plans import planned_job


def scope():
    return {"sha256": "a" * 64, "pipeline": "test"}


def call(workspace, binding, *, token="signed-token", key="logical-key"):
    bind_workspace(workspace, binding, create=True)
    return begin_invocation(
        workspace,
        binding,
        {"token": token, "pair_id": "P01"},
        provider="test",
        model="test",
        pricing_version="test",
        idempotency_key=key,
        prompt="exact prompt",
        size="1440x3840",
        quality="high",
        sources=[],
    )


def test_same_native_token_cannot_dispatch_again_with_a_new_key(tmp_path):
    original = call(tmp_path, scope())
    with pytest.raises(DetailGenerationError, match="may already have incurred"):
        call(tmp_path, scope(), key="a-new-attempt-key")
    assert json.loads(original.read_text())["outcome"] == "outcome_unknown"


@pytest.mark.parametrize("outcome", [None, "outcome_unknown", "failed"])
def test_unknown_or_failed_receipts_cannot_authorize_canvas_recovery(tmp_path, outcome):
    receipt = call(tmp_path, scope())
    if outcome:
        finish_invocation(
            receipt,
            outcome=outcome,
            provider_request_id="req",
            artifact_sha256="b" * 64,
        )
    with pytest.raises(DetailGenerationError, match="exact original successful"):
        verify_original_invocation(
            [tmp_path], scope(), pair_id="P01", artifact_sha256="b" * 64
        )


def test_recovery_requires_exact_scope_request_and_canvas_bytes(tmp_path):
    import hashlib

    payload = b"exact original supplier response"
    sha = hashlib.sha256(payload).hexdigest()
    receipt = call(tmp_path, scope())
    finish_invocation(
        receipt,
        outcome="succeeded",
        provider_request_id="req",
        artifact_sha256=sha,
        artifact_payload=payload,
    )
    verify_original_invocation([tmp_path], scope(), pair_id="P01", artifact_sha256=sha)
    with pytest.raises(DetailGenerationError):
        verify_original_invocation(
            [tmp_path], scope(), pair_id="P01", artifact_sha256="c" * 64
        )
    with pytest.raises(DetailGenerationError, match="differ"):
        bind_workspace(tmp_path, {**scope(), "sha256": "d" * 64})
    changed = json.loads(receipt.read_text())
    changed["model"] = "different-model"
    receipt.write_text(json.dumps(changed))
    with pytest.raises(DetailGenerationError, match="receipt changed"):
        verify_original_invocation(
            [tmp_path], scope(), pair_id="P01", artifact_sha256=sha
        )


def approve_without_dispatch(client, monkeypatch):
    planned = planned_job(client)
    monkeypatch.setattr("app.routes_v1._schedule_outbox", lambda *args, **kwargs: None)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert response.status_code == 202, response.text
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    return planned


@pytest.mark.parametrize("failure", ["unknown", "exception"])
def test_uncertain_gateway_call_stops_without_reset_or_automatic_resend(
    client, monkeypatch, failure
):
    planned = approve_without_dispatch(client, monkeypatch)
    gateway = _gateway(client)
    original = gateway.edit

    async def uncertain(request):
        result = await original(request)
        if failure == "exception":
            raise TimeoutError("transport returned no response after send")
        return replace(
            result,
            outcome=ProviderOutcome.OUTCOME_UNKNOWN,
            artifact=None,
            actual_cost_micros=None,
            retryable=True,
        )

    monkeypatch.setattr(gateway, "edit", uncertain)
    runner = client.app.state.task_runner
    asyncio.run(runner.run(planned["id"]))
    asyncio.run(runner.run(planned["id"]))
    assert len(gateway.image_calls) == 1
    state = json.loads(
        (detail_workspace(planned["id"]) / "session.json").read_text(encoding="utf-8")
    )
    assert state["state"] == "AWAITING_GENERATED_MODULE"
    assert state["active_token"] is not None
    assert (
        state["approval"]["text"] == _aurora_approval_payload(planned)["approval_text"]
    )
    with client.app.state.session_factory() as db:
        attempts = db.scalars(
            select(JobAttemptRecord).where(JobAttemptRecord.job_id == planned["id"])
        ).all()
        assert len(attempts) == 1
        assert attempts[0].status == "outcome_unknown"
    retried = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/retry",
        headers=idem(),
        json={"scope": "failed"},
    )
    assert retried.status_code == 409, retried.text
    assert retried.json()["error"]["code"] == "NATIVE_DETAIL_RECONCILIATION_REQUIRED"
    assert len(gateway.image_calls) == 1


def test_expired_domestic_running_lease_is_not_requeued(client, monkeypatch):
    planned = approve_without_dispatch(client, monkeypatch)
    coordinator = client.app.state.task_runner.coordinator
    lease = coordinator.claim_next_item(planned["id"])
    assert lease is not None
    assert coordinator.mark_provider_started(lease)
    with client.app.state.session_factory() as db:
        attempt = db.get(JobAttemptRecord, lease.attempt_id)
        attempt.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    assert coordinator.claim_next_item(planned["id"]) is None
    with client.app.state.session_factory() as db:
        assert db.get(JobAttemptRecord, lease.attempt_id).status == "outcome_unknown"
        assert db.get(JobItemRecord, lease.item_id).status == "failed"
    assert _gateway(client).image_calls == []


@pytest.mark.parametrize(
    "change",
    [
        "approval",
        "legacy_version",
        "asset_role",
        "job_input",
        "approved_asset_manifest",
    ],
)
def test_generation_rechecks_current_authority_before_provider_call(
    client, monkeypatch, change
):
    planned = approve_without_dispatch(client, monkeypatch)
    with client.app.state.session_factory() as db:
        job = db.get(JobRecord, planned["id"])
        plan = db.get(JobPlanRecord, planned["plan"]["id"])
        if change == "legacy_version":
            job.configuration = {
                key: value
                for key, value in job.configuration.items()
                if key != "domestic_detail_pipeline"
            }
        elif change == "job_input":
            value = deepcopy(job.configuration)
            value["input"]["reference_asset_ids"] = ["a-different-reference"]
            job.configuration = value
        elif change == "approved_asset_manifest":
            value = deepcopy(plan.payload)
            value["source_asset_manifest"][0]["assets"][0]["filename"] = (
                "changed-after-approval.png"
            )
            plan.payload = value
        elif change == "approval":
            value = deepcopy(plan.payload)
            value["aurora_v4_3"]["approval_receipt"]["approval_document_sha256"] = (
                "0" * 64
            )
            plan.payload = value
        else:
            item = db.scalar(
                select(JobItemRecord)
                .where(JobItemRecord.job_id == job.id)
                .order_by(JobItemRecord.ordinal)
            )
            value = deepcopy(item.configuration)
            value["source_assets"][0]["role"] = "style_reference"
            item.configuration = value
        db.commit()
    asyncio.run(client.app.state.task_runner.run(planned["id"]))
    assert _gateway(client).image_calls == []


@pytest.mark.parametrize(
    "single_dispatch,expected", [(True, "outcome_unknown"), (False, "failed")]
)
def test_detail_gateway_classifies_server_error_conservatively_without_changing_default(
    single_dispatch, expected
):
    from app.xinghe_agent import ImageEditRequest, TengliOpenAICompatibleGateway

    class ServerErrorTransport:
        calls = 0

        async def post_multipart(self, **kwargs):
            self.calls += 1
            return 502, {
                "id": "original-call",
                "error": {"message": "upstream unavailable"},
            }

    transport = ServerErrorTransport()
    gateway = TengliOpenAICompatibleGateway(
        api_key="offline-test-key",
        base_url="https://offline.invalid",
        planner_model="test",
        image_model="test",
        transport=transport,
    )
    request = ImageEditRequest(
        workspace_id="test",
        job_id="job",
        job_item_id="item",
        attempt_id="attempt",
        page_number=1,
        group_key="primary",
        sources=(),
        prompt="test",
        quality="high",
        idempotency_key="key",
        size="1440x3840",
        single_dispatch=single_dispatch,
    )
    result = asyncio.run(gateway.edit(request))
    assert result.outcome.value == expected
    assert result.retryable is (not single_dispatch)
    assert transport.calls == 1


def test_repair_uses_a_distinct_wire_key_and_same_request_keeps_its_key():
    from app.domestic_detail.generation import wire_idempotency_key

    assert wire_idempotency_key("logical", "token-1") == wire_idempotency_key(
        "logical", "token-1"
    )
    assert wire_idempotency_key("logical", "token-1") != wire_idempotency_key(
        "logical", "token-2"
    )


@pytest.mark.parametrize("changed", ["identity", "input", "compiled_plan", "approval"])
def test_compiled_identity_and_approval_cannot_change_after_dispatch_seal(
    tmp_path, changed
):
    import hashlib

    from app.domestic_detail.generation import bind_native_contract
    from app.domestic_detail.plans import canonical_hash

    plan = {"modules": [{"id": "M01"}]}
    binding = {**scope(), "normalized_plan_sha256": canonical_hash(plan)}
    bind_workspace(tmp_path, binding, create=True)
    for name in ("input.json", "identity.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    document = tmp_path / "approval.md"
    document.write_bytes(b"exact compiler approval")
    session = {
        "version": "4.3.1",
        "plan": plan,
        "modules": [{"plan": plan["modules"][0]}],
        "approval": {"text": "user words"},
        "approval_document": {
            "path": str(document),
            "sha256": hashlib.sha256(document.read_bytes()).hexdigest(),
        },
    }
    (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")
    bind_native_contract(tmp_path, binding, create=True)
    if changed in {"input", "identity"}:
        (tmp_path / (changed + ".json")).write_text(
            '{"changed":true}', encoding="utf-8"
        )
    elif changed == "approval":
        document.write_bytes(b"different compiler approval")
    else:
        session["plan"] = {"modules": [{"id": "M02"}]}
        (tmp_path / "session.json").write_text(json.dumps(session), encoding="utf-8")
    with pytest.raises(DetailGenerationError):
        bind_native_contract(tmp_path, binding)


def test_candidate_bytes_are_durable_before_success_completion_receipt(tmp_path):
    import hashlib

    payload = b"original provider candidate bytes"
    receipt = call(tmp_path, scope())
    finish_invocation(
        receipt,
        outcome="succeeded",
        provider_request_id="original-request",
        artifact_sha256=hashlib.sha256(payload).hexdigest(),
        artifact_payload=payload,
    )
    assert receipt.with_suffix(".artifact").read_bytes() == payload
    assert receipt.with_suffix(".completion.json").is_file()


def test_draft_edits_preserve_the_server_owned_pipeline_version(client):
    from app.domestic_detail.generation import PIPELINE_VERSION
    from tests.test_api import upload_asset
    from tests.test_domestic_detail_execution import _create_detail_job

    asset = upload_asset(client, filename="draft-pipeline.png")
    created = _create_detail_job(
        client, asset["id"], market_region="domestic", auto_start_after_plan=False
    )
    with client.app.state.session_factory() as db:
        job = db.get(JobRecord, created["id"])
        job.status = "draft"
        configuration = deepcopy(job.configuration)
        configuration["domestic_detail_pipeline"] = "client-chosen-version"
        db.commit()
    result = client.patch(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created['id']}",
        json={"configuration": configuration},
    )
    assert result.status_code == 200, result.text
    with client.app.state.session_factory() as db:
        assert (
            db.get(JobRecord, created["id"]).configuration["domestic_detail_pipeline"]
            == PIPELINE_VERSION
        )
