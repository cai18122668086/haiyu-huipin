"""A new pair revision owns authority and reserves, never the accepted source."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from sqlalchemy import select

from app.aurora_detail_runtime import detail_workspace
from app.domestic_detail.delivery import DetailDeliveryError
from app.domestic_detail.plans import DetailPlanError, canonical_hash
from app.domestic_detail.revision_authorization import REVISION_APPROVAL_TEXT
from app.domestic_detail.revision_jobs import (
    create_revision_job,
    require_revision_reconciled,
)
from app.models import (
    AuditEventRecord,
    JobAssetRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    OutboxEventRecord,
    utc_now,
)
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_automatic_delivery import authorize_body
from tests.test_domestic_detail_delivery import (
    REVIEWS,
    explicit_synthetic_delivery,  # noqa: F401
    ready,
)
from tests.test_domestic_detail_execution import _gateway
from tests.test_domestic_detail_plans import planned_job
from tests.test_domestic_detail_revision_authorization import database_snapshot
from tests.test_native_detail_local_adjustment import adjustment, inventory


@pytest.mark.parametrize("automatic", [False, True])
def test_child_owns_new_authority_only_selected_pair_is_pending_and_source_is_exact(
    client, automatic
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
        job_id, fingerprint = planned["id"], response.json()["sha256"]
    else:
        source = ready(client)
        job_id, fingerprint = source["id"], REVIEWS[source["id"]]
        prefix = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}"
    body = {"final_review_sha256": fingerprint, "adjustment": adjustment("M04")}
    client.app.state.settings.provider_quota_enabled = True
    response = client.post(prefix + "/local-revision/quote", json=body)
    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote["maximum_scope_model_requests"] == 1
    assert (
        quote["maximum_model_requests"] == 5 and quote["maximum_provider_requests"] == 7
    )
    before_files = inventory(detail_workspace(job_id))
    before_db = database_snapshot(client)
    gateway = _gateway(client)
    calls = len(gateway.planner_calls), len(gateway.image_calls)
    args = dict(
        actor_id=quote["actor_user_id"],
        fingerprint=fingerprint,
        adjustment=body["adjustment"],
        quote_sha256=quote["sha256"],
        approval_text=REVISION_APPROVAL_TEXT,
    )
    factory = client.app.state.session_factory
    for wrong in [
        dict(approval_text="继续"),
        dict(quote_sha256="f" * 64),
        dict(actor_id="another-user"),
    ]:
        with factory() as db:
            record = db.get(JobRecord, job_id)
            with pytest.raises(DetailPlanError):
                asyncio.run(
                    create_revision_job(
                        db,
                        record,
                        client.app.state.storage,
                        client.app.state.settings,
                        gateway,
                        **{**args, **wrong},
                    )
                )
            db.rollback()
        assert database_snapshot(client) == before_db
    with factory() as db:
        record = db.get(JobRecord, job_id)
        source_configuration, source_plan = (
            deepcopy(record.configuration),
            db.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == job_id)),
        )
        source_payload = deepcopy(source_plan.payload)
        source_items = db.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == job_id)
            .order_by(JobItemRecord.ordinal)
        ).all()
        child = asyncio.run(
            create_revision_job(
                db,
                record,
                client.app.state.storage,
                client.app.state.settings,
                gateway,
                **args,
            )
        )
        db.flush()
        child_id = child.id
        marker = child.configuration["local_revision"]
        assert marker["job_id"] == child_id and marker["source_job_id"] == job_id
        assert (
            child.root_job_id == (record.root_job_id or job_id)
            and child.retry_of_job_id is None
        )
        assert (
            child.status == "queued"
            and child.expected_item_count == 8
            and child.succeeded_item_count == 6
        )
        assert child.estimated_cost_max_micros == quote["maximum_cost_micros"]
        quota = db.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.resource_id == child.id,
                AuditEventRecord.action == "provider.beta_job_reserved",
            )
        )
        assert quota is not None
        assert quota.details["provider_request_count"] == 7
        assert quota.details["planner_reserve_micros"] == quote["review_reserve_micros"]
        assert quota.details["image_budget_micros"] == 2 * quote["image_unit_micros"]
        assert marker["authority"]["quote"] == quote
        child_plan = db.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == child_id)
        )
        assert (
            child_plan.payload["local_revision_source"]["approval_receipt"]
            == source_payload["aurora_v4_3"]["approval_receipt"]
        )
        receipt = child_plan.payload["aurora_v4_3"]["approval_receipt"]
        assert "automatic_delivery_authorization" not in receipt
        assert receipt["local_revision_authority_sha256"] == marker["authority_sha256"]
        assert receipt["approval_text"] == REVISION_APPROVAL_TEXT
        assert (
            child_plan.payload["native_approval"] == source_payload["native_approval"]
        )
        assert (
            child_plan.payload["aurora_v4_3"]["contract_binding"]
            == source_payload["aurora_v4_3"]["contract_binding"]
        )
        from app.provider_quota_ledger import _planner_charges

        original_child_payload = deepcopy(child_plan.payload)
        child_plan.payload = {
            **original_child_payload,
            "usage": {"planner": [{"actual_cost_micros": None} for _ in range(5)]},
        }
        db.flush()
        charges, count, cost, actual = _planner_charges(
            db, child, quota, quote["maximum_cost_micros"], 7, 0, []
        )
        assert count == 5 and actual == 0
        assert cost == 5 * quote["review_reserve_micros"]
        assert cost + 2 * quote["image_unit_micros"] == child.estimated_cost_max_micros
        assert all(
            c["charged_cost_micros"] == quote["review_reserve_micros"] for c in charges
        )
        child_plan.payload = original_child_payload
        db.flush()
        assert child_plan.payload_hash == canonical_hash(child_plan.payload)
        assert "final_manifest" not in child_plan.payload
        items = db.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == child_id)
            .order_by(JobItemRecord.ordinal)
        ).all()
        assert [i.ordinal for i in items if i.status == "pending"] == [2, 3]
        assert all(i.status == "reused" for i in items if i.ordinal not in {2, 3})
        assert [i.source_job_item_id for i in items] == [i.id for i in source_items]
        links = db.scalars(
            select(JobAssetRecord).where(JobAssetRecord.job_id == child_id)
        ).all()
        assert (
            len(
                [
                    link
                    for link in links
                    if link.direction == "output"
                    and link.role == "detail_page"
                    and link.is_deliverable
                ]
            )
            == 6
        )
        assert all(
            link.job_item_id not in {items[2].id, items[3].id}
            for link in links
            if link.direction == "output"
        )
        assert not db.scalars(
            select(OutboxEventRecord).where(OutboxEventRecord.aggregate_id == child_id)
        ).all()
        assert (
            record.configuration == source_configuration
            and source_plan.payload == source_payload
        )
        with pytest.raises(DetailDeliveryError, match="already active"):
            require_revision_reconciled(db, record)
        # A durable unknown result remains a blocker even after a terminal failure.
        child.status = "partial"
        child.finished_at = utc_now()
        items[2].status = "failed"
        items[2].error_code = "PROVIDER_OUTCOME_UNKNOWN"
        db.flush()
        with pytest.raises(DetailDeliveryError, match="original revision attempt"):
            require_revision_reconciled(db, record)
        items[2].error_code = None
        db.flush()
        require_revision_reconciled(db, record)
        # Changing the stored human authority never opens a new paid cycle.
        original_marker = deepcopy(marker)
        child.configuration = {
            **child.configuration,
            "local_revision": {**marker, "actor_id": "tampered"},
        }
        db.flush()
        with pytest.raises(DetailDeliveryError, match="authority changed"):
            require_revision_reconciled(db, record)
        child.configuration = {**child.configuration, "local_revision": original_marker}
        db.commit()
    for suffix, payload in [("clone", None), ("retry", {"scope": "failed"})]:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{child_id}/" + suffix,
            headers=idem(),
            json=payload,
        )
        assert response.status_code == 409, response.text
        assert (
            response.json()["error"]["code"]
            == "NATIVE_DETAIL_REVISION_RECONCILIATION_REQUIRED"
        )
    after_db = database_snapshot(client)
    assert all(set(rows) <= set(after_db[table]) for table, rows in before_db.items())
    assert inventory(detail_workspace(job_id)) == before_files
    assert calls == (len(gateway.planner_calls), len(gateway.image_calls))
    assert not list(detail_workspace(child_id).iterdir())


@pytest.mark.parametrize("directory", ["planner-calls", "reviews"])
@pytest.mark.parametrize(
    "outcome", ["missing", "outcome_unknown", "response_invalid", "rejected"]
)
def test_original_model_response_must_be_reconciled_even_for_terminal_child(
    tmp_path, directory, outcome
):
    from app.domestic_detail.planner_dispatch import dispatch_metadata, route_descriptor
    from app.domestic_detail.review import ReviewStore
    from app.domestic_detail.revision_jobs import _require_reconciled_files

    route = route_descriptor(
        provider="explicit-test",
        route="one",
        model="scope-test",
        endpoint="https://example.invalid",
        credential="fixture-only",
    )
    store = ReviewStore(tmp_path / directory, {"dispatch_route": route})
    store.begin()
    if outcome != "missing":
        status = {"outcome_unknown": 500, "response_invalid": 200, "rejected": 400}[
            outcome
        ]
        result = {
            "payload": None,
            "error_code": "EXPLICIT_FIXTURE_FAILURE",
            "provider_envelope": {
                "native_planner_dispatch": dispatch_metadata(
                    route,
                    attempts=1,
                    response_received=True,
                    status=status,
                    error_code="EXPLICIT_FIXTURE_FAILURE",
                )
            },
        }
        store.finish(result)
    if outcome == "rejected":
        _require_reconciled_files(tmp_path)
    else:
        with pytest.raises(ValueError):
            _require_reconciled_files(tmp_path)


@pytest.mark.parametrize(
    "outcome", ["missing", "outcome_unknown", "artifact_missing", "failed"]
)
def test_original_image_receipt_must_be_reconciled_before_new_revision(
    tmp_path, outcome
):
    import json

    from app.domestic_detail.revision_jobs import _require_reconciled_files

    directory = tmp_path / "generation-calls"
    directory.mkdir()
    request = {"version": "fixture-only", "pair_id": "P01"}
    request = {
        **request,
        "request_sha256": canonical_hash(request),
        "outcome": "outcome_unknown",
    }
    (directory / "first.json").write_text(json.dumps(request), encoding="utf-8")
    if outcome != "missing":
        completion = {
            "request_sha256": request["request_sha256"],
            "outcome": "succeeded" if outcome == "artifact_missing" else outcome,
            "artifact_sha256": "f" * 64,
        }
        (directory / "first.completion.json").write_text(
            json.dumps(completion), encoding="utf-8"
        )
    if outcome == "failed":
        _require_reconciled_files(tmp_path)
    else:
        with pytest.raises((ValueError, OSError)):
            _require_reconciled_files(tmp_path)
