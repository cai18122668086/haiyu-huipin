"""Explicit native automatic delivery and zero-call recovery."""

import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from app.aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
from app.domestic_detail.delivery_authorization import (
    AUTO_AUTHORIZATION_TEXT,
    AUTO_MODE,
    MANUAL_MODE,
    authorization_quote,
    confirm_authorization,
    native_authorization_text,
    native_delivery_mode,
    receipt_authorization,
)
from app.domestic_detail.plans import DetailPlanError, canonical_hash
from app.models import UsageEventRecord
from app.xinghe_agent import XingheImageProvider
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_atomic_review import visible_failure
from tests.test_domestic_detail_delivery import (
    explicit_synthetic_delivery,  # noqa: F401
)
from tests.test_domestic_detail_execution import _aurora_approval_payload, _gateway
from tests.test_domestic_detail_plans import planned_job


def contract():
    return {
        "native_approval": {
            "source_job_id": "job_contract",
            "document_sha256": "a" * 64,
        },
        "source_input_sha256": "b" * 64,
        "aurora_v4_3": {
            "approval_document_sha256": "c" * 64,
            "contract_binding": {"sha256": "d" * 64},
        },
    }


def quote_arguments(payload):
    return dict(
        job_id="job_contract",
        workspace_id="ws_contract",
        actor_id="usr_contract",
        plan_id="plan_contract",
        revision=3,
        payload=payload,
    )


def test_default_and_ordinary_continue_never_authorize_automatic_delivery():
    payload = contract()
    payload["aurora_v4_3"]["approval_receipt"] = {"approval_text": "继续，按方案生成"}
    assert native_delivery_mode(payload) == MANUAL_MODE
    assert (
        confirm_authorization(
            mode=None, text=None, fingerprint=None, **quote_arguments(payload)
        )
        is None
    )
    with pytest.raises(DetailPlanError):
        confirm_authorization(
            mode=AUTO_MODE,
            text="继续",
            fingerprint="a" * 64,
            **quote_arguments(payload),
        )
    with pytest.raises(DetailPlanError):
        confirm_authorization(
            mode=MANUAL_MODE,
            text=AUTO_AUTHORIZATION_TEXT,
            fingerprint="a" * 64,
            **quote_arguments(payload),
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "none",
        "actor",
        "native_document",
        "revision",
        "copied_child",
        "workspace",
        "runtime",
    ],
)
def test_auto_authority_is_bound_to_exact_human_source_and_plan(tamper, monkeypatch):
    payload = contract()
    quote = authorization_quote(**quote_arguments(payload))
    assert (
        confirm_authorization(
            mode=AUTO_MODE,
            text=AUTO_AUTHORIZATION_TEXT,
            fingerprint=quote["sha256"],
            **quote_arguments(payload),
        )
        == quote
    )
    receipt = {
        "approval_text": "original human approval",
        "actor_user_id": "usr_contract",
        "plan_revision": 3,
        "automatic_delivery_authorization": quote,
    }
    payload["aurora_v4_3"]["approval_receipt"] = receipt
    scope = dict(
        job_id="job_contract",
        workspace_id="ws_contract",
        plan_id="plan_contract",
        revision=3,
    )
    if tamper == "actor":
        receipt["actor_user_id"] = "usr_other"
    if tamper == "native_document":
        payload["native_approval"]["document_sha256"] = "e" * 64
    if tamper == "revision":
        receipt["plan_revision"] = 4
    if tamper == "copied_child":
        scope["job_id"] = "job_child"
    if tamper == "workspace":
        scope["workspace_id"] = "ws_other"
    if tamper == "runtime":
        monkeypatch.setattr(
            "app.domestic_detail.generation.runtime_source_hash", lambda: "f" * 64
        )
    if tamper != "none":
        with pytest.raises(DetailPlanError):
            receipt_authorization(payload, **scope)
    else:
        assert receipt_authorization(payload, **scope) == quote
        assert native_delivery_mode(payload) == AUTO_MODE
        assert (
            native_authorization_text(payload)
            == "original human approval\n" + AUTO_AUTHORIZATION_TEXT
        )


def authorize_body(client, planned):
    prefix = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}"
    response = client.get(prefix + "/plan/automatic-delivery-authorization")
    assert response.status_code == 200, response.text
    quote = response.json()
    body = {
        **_aurora_approval_payload(planned),
        "domestic_detail_delivery_mode": AUTO_MODE,
        "automatic_delivery_authorization_text": quote["authorization_text"],
        "automatic_delivery_authorization_sha256": quote["sha256"],
    }
    return prefix, quote, body


def test_api_rejects_missing_or_stale_auto_consent_before_generation(client):
    planned = planned_job(client)
    prefix, quote, body = authorize_body(client, planned)
    gateway = _gateway(client)
    counts = (len(gateway.planner_calls), len(gateway.image_calls))
    for changes in (
        {"automatic_delivery_authorization_text": None},
        {"automatic_delivery_authorization_text": "继续"},
        {"automatic_delivery_authorization_sha256": "e" * 64},
        {"domestic_detail_delivery_mode": MANUAL_MODE},
    ):
        response = client.post(
            prefix + "/plan/confirm", headers=idem(), json={**body, **changes}
        )
        assert response.status_code == 409, response.text
    assert counts == (len(gateway.planner_calls), len(gateway.image_calls))
    key = idem()
    accepted = client.post(prefix + "/plan/confirm", headers=key, json=body)
    assert accepted.status_code == 202, accepted.text
    assert (
        accepted.json()["plan"]["payload"]["aurora_v4_3"]["approval_receipt"][
            "automatic_delivery_authorization"
        ]
        == quote
    )
    assert counts == (len(gateway.planner_calls), len(gateway.image_calls))


def observed_commands(monkeypatch):
    original = AuroraDetailHarnessRunner.run
    commands = []

    def run(self, command, workspace, arguments):
        commands.append((command, dict(arguments)))
        return original(self, command, workspace, arguments)

    monkeypatch.setattr(AuroraDetailHarnessRunner, "run", run)
    return commands


@pytest.mark.parametrize("with_residual", [False, True])
def test_explicit_auto_delivery_finishes_native_manifest_and_preserves_residuals(
    client, monkeypatch, with_residual
):
    if with_residual:

        async def failure(self, request, native_request, sources, artifact):
            value = self._native_detail_judgment(native_request, artifact)
            if native_request["pair_id"] == "P01":
                visible_failure(value["module_judgments"][0], "geometry")
                value.update(status="MATERIAL_FAIL", material_failures=[])
            return value

        monkeypatch.setattr(
            XingheImageProvider, "_native_detail_judgment_with_review", failure
        )
    planned = planned_job(client)
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    prefix, quote, body = authorize_body(client, planned)
    commands = observed_commands(monkeypatch)
    response = client.post(prefix + "/plan/confirm", headers=idem(), json=body)
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    result = client.get(prefix).json()
    assert result["status"] == "completed", result
    assert len([c for c, a in commands if c == "auto-finalize"]) == 1
    assert not [c for c, a in commands if c == "finalize"]
    authorize = [a for c, a in commands if c == "authorize"]
    assert (
        len(authorize) == 1
        and authorize[0]["delivery-mode"] == AUTO_MODE
        and AUTO_AUTHORIZATION_TEXT in authorize[0]["approval-text"]
    )
    gateway = _gateway(client)
    assert len(gateway.image_calls) == (5 if with_residual else 4)
    counts = (len(gateway.planner_calls), len(gateway.image_calls))
    root = detail_workspace(planned["id"])
    state = (root / "session.json").read_bytes()
    native = json.loads(state)
    assert (
        native["state"] == "FINALIZED"
        and native["final"]["approval"]["mode"] == AUTO_MODE
    )
    packet = client.post(prefix + "/detail-final-review")
    assert packet.status_code == 200, packet.text
    record = packet.json()
    assert (
        record["delivery_mode"] == AUTO_MODE
        and record["automatic_delivery_authorization"] == quote
    )
    assert "不表示用户已经逐屏审阅最终成图" in record["document_markdown"]
    assert record["campaign_audit_status"] == "NOT_REQUESTED"
    if with_residual:
        assert record["quality_state"] == "WITH_RESIDUALS" and record["residual_issues"]
    archive = client.get(prefix + "/deliverables.zip")
    assert archive.status_code == 200, archive.text
    with ZipFile(BytesIO(archive.content)) as z:
        assert json.loads(z.read("final-review.json")) == record
        assert (
            json.loads(z.read("native-final-manifest.json"))["approval"]["mode"]
            == AUTO_MODE
        )
    key = idem()
    recovery = client.post(
        prefix + "/automatic-delivery/recover",
        headers=key,
        json={"authorization_sha256": quote["sha256"]},
    )
    assert recovery.status_code == 202 and recovery.json()["status"] == "completed", (
        recovery.text
    )
    dispatch_pending(client)
    assert (root / "session.json").read_bytes() == state and counts == (
        len(gateway.planner_calls),
        len(gateway.image_calls),
    )
    assert len([c for c, a in commands if c == "auto-finalize"]) == 1


def usage_snapshot(client, job_id):
    with client.app.state.session_factory() as db:
        return [
            (u.id, u.actual_cost_micros, canonical_hash(u.raw_usage))
            for u in db.scalars(
                select(UsageEventRecord)
                .where(UsageEventRecord.job_id == job_id)
                .order_by(UsageEventRecord.id)
            ).all()
        ]


def test_original_auto_delivery_recovers_before_and_after_native_without_rebilling(
    client, monkeypatch
):
    from app.domestic_detail import automatic_delivery

    planned = planned_job(client)
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    prefix, quote, body = authorize_body(client, planned)
    commands = observed_commands(monkeypatch)
    verify = automatic_delivery._verified_context
    project = automatic_delivery.public_delivery_manifest
    failed_before = False
    failed_after = False

    async def interrupted_verify(*args, **kwargs):
        nonlocal failed_before
        if not failed_before:
            failed_before = True
            raise OSError("synthetic before original native finalize")
        return await verify(*args, **kwargs)

    def interrupted_project(*args, **kwargs):
        nonlocal failed_after
        value = project(*args, **kwargs)
        if not failed_after:
            failed_after = True
            raise OSError("synthetic after original native result before SQL")
        return value

    monkeypatch.setattr(automatic_delivery, "_verified_context", interrupted_verify)
    monkeypatch.setattr(
        automatic_delivery, "public_delivery_manifest", interrupted_project
    )
    response = client.post(prefix + "/plan/confirm", headers=idem(), json=body)
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    first = client.get(prefix).json()
    assert (
        first["status"] == "awaiting_final_review"
        and first["stage"] == "automatic_delivery_reconciliation_required"
    ), first
    assert not [c for c, a in commands if c == "auto-finalize"]
    gateway = _gateway(client)
    counts = (len(gateway.planner_calls), len(gateway.image_calls))
    usage = usage_snapshot(client, planned["id"])
    body = {"authorization_sha256": quote["sha256"]}
    key = idem()
    assert (
        client.post(
            prefix + "/automatic-delivery/recover", headers=key, json=body
        ).status_code
        == 202
    )
    assert (
        client.post(
            prefix + "/automatic-delivery/recover", headers=key, json=body
        ).status_code
        == 202
    )
    dispatch_pending(client)
    second = client.get(prefix).json()
    assert second["status"] == "awaiting_final_review" and failed_after, second
    root = detail_workspace(planned["id"])
    original_state = (root / "session.json").read_bytes()
    assert json.loads(original_state)["state"] == "FINALIZED"
    directory = next((root / "delivery").iterdir())
    original = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert (
        client.post(
            prefix + "/automatic-delivery/recover", headers=idem(), json=body
        ).status_code
        == 202
    )
    dispatch_pending(client)
    final = client.get(prefix).json()
    assert final["status"] == "completed", final
    assert (root / "session.json").read_bytes() == original_state
    assert {
        p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()
    } == original
    assert len([c for c, a in commands if c == "auto-finalize"]) == 1
    assert usage_snapshot(client, planned["id"]) == usage and counts == (
        len(gateway.planner_calls),
        len(gateway.image_calls),
    )
