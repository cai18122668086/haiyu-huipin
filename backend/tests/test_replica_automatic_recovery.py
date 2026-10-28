"""Unattended recovery retains gates, checkpoints, counters and original tasks."""

import json
from collections import Counter

import pytest

from app.haiyu_transport import HaiyuTransport
from app.models import ReplicaDocumentRecord
from app.replica_automatic_recovery import claim_analysis_recovery
from app.xinghe_agent import XingheAgentError
from tests.test_haiyu_transport import JournalTransport, Transport, png, request
from tests.test_replica_analysis_recovery import split_gateway
from tests.test_replica_compact import SyntheticGateway
from tests.test_replica_partial_delivery import create_case


def test_boundary_failure_automatically_corrects_only_failed_columns_once(
    client, monkeypatch, tmp_path
):
    gateway = split_gateway()
    original = gateway.plan

    async def corrected_on_recovery(req):
        context = json.loads(req.product_information[0])["context"]
        mismatch = context.get("boundary_mismatch", {})
        if mismatch.get("correction_revision") not in {None, "initial"}:
            gateway.correct_repair = True
        return await original(req)

    gateway.plan = corrected_on_recovery
    _, route, did, jid = create_case(
        client, monkeypatch, tmp_path, fallback=False, gateway_override=gateway
    )
    assert jid is not None
    with client.app.state.session_factory() as db:
        values = db.get(ReplicaDocumentRecord, did).payload
    history = values["_automatic_analysis_recoveries"]
    assert len(history) == 1
    assert history[0]["error_code"] == "REPLICA_SEGMENTATION_INVALID"
    assert history[0]["planning_rounds_reset"] is False
    assert values["_analysis_lineage"]
    phases = Counter(
        json.loads(r.product_information[0])["stage"] for r in gateway.requests
    )
    assert phases["intake"] == 1
    assert len(values["plans"]) == 5
    assert not gateway.image_requests


def test_repeated_same_failure_stops_after_one_recovery_without_generating(
    client, monkeypatch, tmp_path
):
    gateway, route, did, jid = create_case(
        client, monkeypatch, tmp_path, gateway_override=split_gateway()
    )
    assert jid is None
    doc = client.get(route + "/documents/" + did).json()
    assert doc["status"] == "failed"
    assert doc["payload"]["error_code"] == "REPLICA_SEGMENTATION_INVALID"
    history = doc["payload"]["_automatic_analysis_recoveries"]
    assert len(history) == 1
    assert not gateway.image_requests
    before = len(gateway.requests)
    assert (
        claim_analysis_recovery(client.app, did, doc["payload"]["analysis_attempt_id"])
        is None
    )
    assert len(gateway.requests) == before


def test_recovery_never_claims_a_stale_attempt_or_changed_inputs(
    client, monkeypatch, tmp_path
):
    _, _, did, _ = create_case(
        client, monkeypatch, tmp_path, gateway_override=split_gateway()
    )
    with client.app.state.session_factory() as db:
        row = db.get(ReplicaDocumentRecord, did)
        values = dict(row.payload)
        values["_automatic_analysis_recoveries"] = []
        values["facts"] += " changed after failure"
        row.payload = values
        attempt = values["analysis_attempt_id"]
        db.commit()
    assert claim_analysis_recovery(client.app, did, "stale-attempt") is None
    assert claim_analysis_recovery(client.app, did, attempt) is None


def test_accepted_task_receipt_failure_automatically_polls_original_no_resubmit(
    tmp_path, monkeypatch
):
    class BrokenReceipt(JournalTransport):
        @staticmethod
        def _save_journal(path, record):
            raise OSError("local receipt path temporarily unavailable")

    monkeypatch.setattr("app.haiyu_transport.download_result", lambda *_: png())
    delegate = Transport([(202, {"id": "accepted-original", "status": "queued"})])
    status, payload = request(
        BrokenReceipt(delegate, journal_dir=tmp_path, poll_interval=0)
    )
    assert status == 200 and payload["haiyu_task_id"] == "accepted-original"
    assert payload["receipt_persistence_warning"]["task_id"] == "accepted-original"
    assert len(delegate.calls) == 1
    with pytest.raises(XingheAgentError, match="reconciliation"):
        request(HaiyuTransport(delegate, journal_dir=tmp_path))
    assert len(delegate.calls) == 1


def test_format_recovery_keeps_completed_plan_rounds_and_never_exceeds_three(
    client, monkeypatch, tmp_path
):
    from app.replica_analysis_recovery import document_file

    gateway = SyntheticGateway()
    original = gateway.plan
    malformed = []

    async def malformed_second_round(req):
        seen_review = any(
            json.loads(r.product_information[0])["stage"] == "review_plan"
            for r in gateway.requests
        )
        result = await original(req)
        envelope = json.loads(req.product_information[0])
        if envelope["stage"] == "plan" and seen_review and len(malformed) < 2:
            malformed.append(True)
            result.payload["modules"][0]["product_visibility"] = "invalid-enum"
        return result

    gateway.plan = malformed_second_round
    _, _, did, jid = create_case(
        client, monkeypatch, tmp_path, gateway_override=gateway
    )
    assert jid is not None and len(malformed) == 2
    with client.app.state.session_factory() as db:
        values = db.get(ReplicaDocumentRecord, did).payload
    history = values["_automatic_analysis_recoveries"]
    assert len(history) == 1 and history[0]["planning_rounds_before"] == 1
    source = values["_analysis_lineage"][-1]
    assert (
        document_file(source, "compact-checkpoints.json")["plan_cursor"]["rounds"] == 3
    )
    completion = document_file(values["native_snapshot"], "completion-plan.json")
    assert completion["completed_planning_rounds"] == 3
    assert completion["native_plan_retained"] is True
    phases = Counter(
        json.loads(r.product_information[0])["stage"] for r in gateway.requests
    )
    assert phases["intake"] == 1 and not gateway.image_requests
