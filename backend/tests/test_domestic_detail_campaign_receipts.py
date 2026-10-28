"""Unreadable original dispatch evidence must never permit another paid review."""

from dataclasses import asdict

import pytest

from app.aurora_detail_runtime import detail_workspace
from app.domestic_detail.campaign_jobs import require_campaign_review_reconciled
from app.domestic_detail.delivery import DetailDeliveryError
from app.domestic_detail.planner_dispatch import result_with_dispatch, route_descriptor
from app.domestic_detail.review import ReviewStore
from app.models import JobRecord, utc_now
from app.services import new_id
from app.xinghe_agent import PlannerCallResult
from tests.test_api import WORKSPACE_ID


@pytest.mark.parametrize(
    "changed", [None, "request", "response", "raw", "response_missing"]
)
def test_original_receipts_must_be_reconciled_before_new_review_intent(client, changed):
    assert client.get("/api/v1/workspaces").status_code == 200
    source = JobRecord(
        id=new_id("job"),
        workspace_id=WORKSPACE_ID,
        type="detail_pack",
        status="completed",
        succeeded_item_count=1,
        progress=100,
        finished_at=utc_now(),
        configuration={},
    )
    prior = JobRecord(
        id=new_id("job"),
        workspace_id=WORKSPACE_ID,
        type="domestic_detail_review",
        status="completed",
        succeeded_item_count=1,
        progress=100,
        finished_at=utc_now(),
        configuration={"source_job_id": source.id, "intent_sha256": "a" * 64},
    )
    with client.app.state.session_factory() as db:
        db.add_all([source, prior])
        db.commit()
    route = route_descriptor(
        provider="synthetic",
        route="synthetic",
        model="synthetic",
        endpoint="synthetic",
        credential="synthetic",
        kind="synthetic",
    )
    store = ReviewStore(
        detail_workspace(prior.id) / "reviews",
        {
            "phase": "aurora_detail_campaign_audit",
            "render_token": prior.id,
            "native_request_sha256": "a" * 64,
            "candidate_sha256": "b" * 64,
            "request_context_sha256": "c" * 64,
            "dispatch_route": route,
        },
    )
    store.begin()
    result = PlannerCallResult(
        payload={"synthetic": True},
        audit_raw_content='{"synthetic":true}',
        provider_request_id="synthetic-original-receipt",
        raw_usage={},
        normalized_usage={},
        api_latency_seconds=0.01,
        actual_cost_micros=0,
    )
    store.finish(result_with_dispatch(asdict(result), route))
    paths = {
        "request": store.request_path,
        "response": store.response_path,
        "raw": store.stem.with_suffix(".reply.txt"),
    }
    if changed == "response_missing":
        store.response_path.unlink()
    elif changed:
        paths[changed].write_bytes(b"changed-original-receipt")
    with client.app.state.session_factory() as db:
        original = db.get(JobRecord, source.id)
        if changed is None:
            require_campaign_review_reconciled(db, original)
        else:
            with pytest.raises(DetailDeliveryError) as rejected:
                require_campaign_review_reconciled(db, original)
            assert rejected.value.code == "NATIVE_DETAIL_CAMPAIGN_OUTCOME_UNKNOWN"
