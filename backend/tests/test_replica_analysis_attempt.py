"""Retry namespaces must bypass failed-response caches without rerunning plans."""

import asyncio
import json
from collections import Counter
from dataclasses import asdict
from types import SimpleNamespace

from app.models import ReplicaDocumentRecord
from app.replica_analysis_attempt import AttemptScopedPlanner
from app.replica_analysis_recovery import document_file
from app.xinghe_agent import PlannerCallResult
from tests.test_replica_compact import SyntheticGateway
from tests.test_replica_partial_delivery import create_case
from tests.test_replica_planner_truncation import request


def truncated():
    return PlannerCallResult(
        payload=None,
        provider_request_id="known-truncated-analysis",
        raw_usage=None,
        normalized_usage={
            "reported": True,
            "input_tokens": 100,
            "output_tokens": 32768,
            "error_code": "PLANNER_OUTPUT_TRUNCATED",
            "finish_reason": "length",
            "output_token_limit": 32768,
            "http_status": 200,
        },
        api_latency_seconds=0,
        actual_cost_micros=None,
        error_code="PLANNER_OUTPUT_TRUNCATED",
        http_status=200,
    )


def test_attempt_changes_only_transport_key_and_replay_within_attempt_stays_stable():
    sent = []

    async def plan(req):
        sent.append(req)
        return "result"

    gateway = SimpleNamespace(plan=plan, external_network_request_count=7)
    req = request()
    original = asdict(req)
    first = AttemptScopedPlanner(gateway, "attempt-a")
    second = AttemptScopedPlanner(first, "attempt-b")
    for wrapper in (first, first, second):
        assert asyncio.run(wrapper.plan(req)) == "result"
        assert wrapper.external_network_request_count == 7
    assert sent[0].idempotency_key == sent[1].idempotency_key
    assert sent[0].idempotency_key != sent[2].idempotency_key
    assert sent[2].idempotency_key.count(":analysis-attempt:") == 1
    for value in sent:
        fields = asdict(value)
        fields["idempotency_key"] = original["idempotency_key"]
        assert fields == original
    assert asdict(req) == original


def test_truncated_review_resumes_with_fresh_key_without_resetting_rounds(
    client, monkeypatch, tmp_path
):
    gateway = SyntheticGateway()
    original = gateway.plan
    revision_started = False
    failed_key = None
    resumed_keys = []

    async def cached_truncation(req):
        nonlocal revision_started, failed_key
        stage = json.loads(req.product_information[0])["stage"]
        if stage == "plan" and any(
            json.loads(r.product_information[0])["stage"] == "review_plan"
            for r in gateway.requests
        ):
            revision_started = True
        if failed_key == req.idempotency_key:
            return truncated()  # A real relay would replay this cached failure.
        if stage == "review_plan" and revision_started and failed_key is None:
            failed_key = req.idempotency_key
            return truncated()
        if failed_key and stage == "review_plan":
            resumed_keys.append(req.idempotency_key)
        return await original(req)

    gateway.plan = cached_truncation
    _, _, did, jid = create_case(
        client, monkeypatch, tmp_path, gateway_override=gateway
    )
    assert jid is not None and failed_key and resumed_keys
    with client.app.state.session_factory() as db:
        values = db.get(ReplicaDocumentRecord, did).payload
    history = values["_automatic_analysis_recoveries"]
    assert len(history) == 1
    event = history[0]
    assert event["error_code"] == "REPLICA_PLANNER_OUTPUT_TRUNCATED"
    assert (
        event["planning_rounds_before"] == 1 and event["planning_rounds_reset"] is False
    )
    assert event["from_attempt"] in failed_key
    assert event["to_attempt"] in resumed_keys[0]
    complete = document_file(values["native_snapshot"], "completion-plan.json")
    assert complete["completed_planning_rounds"] == 3
    assert complete["native_plan_retained"] is True
    phases = Counter(
        json.loads(r.product_information[0])["stage"] for r in gateway.requests
    )
    assert phases["intake"] == 1 and not gateway.image_requests


def test_persistent_truncation_stops_after_one_recovery_with_actual_error_retained(
    client, monkeypatch, tmp_path
):
    gateway = SyntheticGateway()
    original = gateway.plan
    keys = []

    async def truncate(req):
        if json.loads(req.product_information[0])["stage"] == "product":
            keys.append(req.idempotency_key)
            return truncated()
        return await original(req)

    gateway.plan = truncate
    _, route, did, jid = create_case(
        client, monkeypatch, tmp_path, gateway_override=gateway
    )
    assert jid is None and len(keys) == len(set(keys)) == 2
    doc = client.get(route + "/documents/" + did).json()
    assert doc["status"] == "failed"
    assert doc["payload"]["error_code"] == "REPLICA_PLANNER_OUTPUT_TRUNCATED"
    assert len(doc["payload"]["_automatic_analysis_recoveries"]) == 1
    assert not gateway.image_requests


def test_empty_json_reply_is_named_and_recovered_once_without_repeating_intake(
    client, monkeypatch, tmp_path
):
    from dataclasses import replace

    gateway = SyntheticGateway()
    original = gateway.plan
    failed_key = None
    recovered_key = None

    async def empty_then_complete(req):
        nonlocal failed_key, recovered_key
        if json.loads(req.product_information[0])["stage"] == "product":
            if failed_key is None:
                failed_key = req.idempotency_key
            if req.idempotency_key == failed_key:
                return replace(
                    truncated(),
                    error_code="PLANNER_SCHEMA_INVALID",
                    normalized_usage={
                        "error_code": "PLANNER_SCHEMA_INVALID",
                        "output_characters": 0,
                        "provider_request_id": "empty-response",
                    },
                )
            recovered_key = req.idempotency_key
        return await original(req)

    gateway.plan = empty_then_complete
    _, _, did, jid = create_case(
        client, monkeypatch, tmp_path, fallback=False, gateway_override=gateway
    )
    assert jid is not None and recovered_key != failed_key
    with client.app.state.session_factory() as db:
        values = db.get(ReplicaDocumentRecord, did).payload
    history = values["_automatic_analysis_recoveries"]
    assert len(history) == 1
    assert history[0]["error_code"] == "REPLICA_MODEL_RESPONSE_INVALID"
    assert history[0]["error_details"]["provider_request_id"] == "empty-response"
    phases = Counter(
        json.loads(r.product_information[0])["stage"] for r in gateway.requests
    )
    assert phases["intake"] == 1 and not gateway.image_requests
