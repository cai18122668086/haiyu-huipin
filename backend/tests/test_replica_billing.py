"""Reference caps are server-owned and shared by reservation and settlement."""

from uuid import uuid4

import pytest

from app import services
from app.credit_billing import successful_image_credits
from app.models import CreditReservationRecord, JobRecord, WorkspaceCreditAccountRecord
from app.replica_billing import KEY, VERSION, submitted_units
from tests.test_api import upload_asset
from tests.test_customer_credit_billing import WORKSPACE, seed_job


def billing_configuration(cap, *, fast=False):
    return {
        "settings": {
            "reference_skill": "aurora-ecommerce-reference-remix-fast" if fast else None
        },
        "replica_session_id": "rep_server_owned",
        KEY: {"version": VERSION, "submitted_units": cap},
    }


@pytest.mark.parametrize("fast,unit", [(False, 15), (True, 8)])
@pytest.mark.parametrize(
    "cap,screens,succeeded", [(4, 6, 6), (4, 6, 2), (4, 6, 0), (0, 1, 1)]
)
def test_capped_reservation_settlement_and_release(
    client, fast, unit, cap, screens, succeeded
):
    with client.app.state.session_factory() as session:
        job, items = seed_job(
            session,
            kind="reference_remix",
            count=screens,
            configuration=billing_configuration(cap, fast=fast),
        )
        reservation = services.reserve_image_credits(session, WORKSPACE, job)
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        original_balance = account.balance_credits
        assert reservation.reserved_credits == min(cap, screens) * unit
        for index, item in enumerate(items):
            item.status = "succeeded" if index < succeeded else "failed"
        session.flush()
        assert successful_image_credits(session, job) == min(cap, succeeded) * unit
        services.settle_credits(session, job, 0)
        assert reservation.settled_credits == min(cap, succeeded) * unit
        session.refresh(account)
        assert account.reserved_credits == 0
        assert account.balance_credits == original_balance - min(cap, succeeded) * unit


def test_public_job_cannot_forge_a_reference_billing_cap(client):
    asset = upload_asset(client)
    reference = upload_asset(client, filename="reference.png")
    configuration = {
        "image_count": 2,
        "billable_image_count": 0,
        KEY: {"version": VERSION, "submitted_units": 0},
        "replica_session_id": "rep_forged",
    }
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/jobs",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "type": "reference_remix",
            "input": {
                "product_asset_ids": [asset["id"]],
                "reference_asset_ids": [reference["id"]],
            },
            "configuration": configuration,
            KEY: configuration[KEY],
            "replica_session_id": "rep_forged",
        },
    )
    assert response.status_code == 202, response.text
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, response.json()["id"])
        assert KEY not in job.configuration
        assert "replica_session_id" not in job.configuration
        reservation = (
            session.query(CreditReservationRecord).filter_by(job_id=job.id).one()
        )
        assert reservation.reserved_credits == 30


def test_settings_cannot_enable_caps_or_cap_unrelated_tools(client):
    with client.app.state.session_factory() as session:
        for kind in ("reference_remix", "detail_pack"):
            job, _ = seed_job(
                session,
                kind=kind,
                count=6,
                configuration={
                    "settings": {"billable_image_count": 0, **billing_configuration(0)}
                },
            )
            assert (
                services.reserve_image_credits(session, WORKSPACE, job).reserved_credits
                == 90
            )
        job, _ = seed_job(
            session, kind="detail_pack", count=6, configuration=billing_configuration(0)
        )
        assert (
            services.reserve_image_credits(session, WORKSPACE, job).reserved_credits
            == 90
        )


@pytest.mark.parametrize("screens,expected", [(6, 4), (2, 2)])
def test_duplicate_reference_ids_do_not_increase_the_cap(screens, expected):
    references = [{"asset_id": str(i)} for i in range(4)]
    assert submitted_units({"references": references * 2}, screens) == expected
    assert submitted_units({}, screens) == screens


@pytest.mark.parametrize("method", ["exact", "style"])
def test_standard_split_auto_starts_with_the_submitted_budget(
    client, monkeypatch, method
):
    from app.replica_native import NativeReplicaRun
    from tests.test_replica import upload
    from tests.test_replica_auto_start import setup

    route, body = setup(client, monkeypatch, method)
    body["references"] += [
        {"asset_id": upload(client, color)} for color in ("green", "yellow", "purple")
    ]
    body["credit_budget"] = 60

    async def prepare(self, payload, records):
        return [
            {
                "reference": payload["references"][i % 4],
                "analysis": {},
                "prompt": "synthetic native prompt",
                "unit_id": f"M{i:02}",
            }
            for i in range(6)
        ]

    monkeypatch.setattr(NativeReplicaRun, "prepare", prepare)
    response = client.post(route + "/sessions", json=body)
    assert response.status_code == 202, response.text
    doc = client.get(route + "/documents/" + response.json()["id"]).json()
    assert doc["status"] == "generating", doc
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, doc["payload"]["job_ids"][0])
        assert job.expected_item_count == 6
        assert job.configuration[KEY]["submitted_units"] == 4
        assert job.estimated_credits == 60


def test_fast_split_auto_starts_and_settles_within_submitted_budget(client):
    from tests.test_replica_fast import Gateway, create, execute, proposal

    gateway = Gateway(proposal(screens=2))
    doc = create(client, gateway, auto=True, budget=8)
    assert doc["status"] == "generating", doc
    job_id = doc["payload"]["job_ids"][0]
    assert execute(client, gateway, job_id) == "completed"
    with client.app.state.session_factory() as session:
        reservation = (
            session.query(CreditReservationRecord).filter_by(job_id=job_id).one()
        )
        assert reservation.reserved_credits == reservation.settled_credits == 8
    assert len(gateway.images) == 2


@pytest.mark.parametrize("full,expected", [(False, 30), (True, 60)])
def test_retries_bill_only_runnable_items_and_keep_the_reference_cap(
    client, full, expected
):
    from app.domain import RetryScope
    from app.routes_v1 import _copy_job_for_retry, _copy_job_relations_for_retry

    with client.app.state.session_factory() as session:
        source, items = seed_job(
            session,
            kind="reference_remix",
            count=6,
            configuration=billing_configuration(4),
        )
        for index, item in enumerate(items):
            item.status = "succeeded" if index < 4 else "failed"
        selected = [item.id for item in (items if full else items[4:])]
        scope = RetryScope.FULL if full else RetryScope.FAILED
        retry = _copy_job_for_retry(session, source, scope, selected)
        session.add(retry)
        session.flush()
        _copy_job_relations_for_retry(session, source, retry, selected)
        reservation = services.reserve_image_credits(session, WORKSPACE, retry)
        assert reservation.reserved_credits == expected
        assert len(reservation.pricing_snapshot["item_ids"]) == len(selected)
        assert reservation.pricing_snapshot["billable_units"] == min(4, len(selected))


def test_a_genuinely_lower_budget_still_requires_explicit_confirmation(
    client, monkeypatch
):
    from tests.test_replica_auto_start import setup

    route, body = setup(client, monkeypatch)
    body["credit_budget"] = 14
    response = client.post(route + "/sessions", json=body)
    assert response.status_code == 202, response.text
    doc = client.get(route + "/documents/" + response.json()["id"]).json()
    assert doc["status"] == "awaiting_confirmation", doc
    assert doc["payload"]["credit_budget_exceeded"] is True
    assert doc["payload"]["credit_quote"]["estimated_credits"] == 15
    assert not doc["payload"].get("job_ids")
    confirmed = client.post(
        route + "/sessions/" + doc["id"] + "/confirm",
        json={"plan_hash": doc["payload"]["plan_hash"]},
    )
    assert confirmed.status_code == 202, confirmed.text
    job_id = confirmed.json()["payload"]["job_ids"][0]
    with client.app.state.session_factory() as session:
        assert session.get(JobRecord, job_id).estimated_credits == 15
