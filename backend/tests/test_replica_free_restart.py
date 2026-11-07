"""A user can replace a failed, zero-delivery trial without spending credits."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditEventRecord,
    CreditReservationRecord,
    JobRecord,
    ReplicaDocumentRecord,
    ReplicaWelcomeGrantRecord,
    WorkspaceCreditAccountRecord,
)
from app.onboarding import personal_workspace_id
from tests.test_api import image_payload
from tests.test_credit_payments import pilot_client as pilot_client
from tests.test_credit_payments_postgres import postgres_credits as postgres_credits
from tests.test_replica_fast import Gateway, execute, proposal
from tests.test_replica_welcome import welcome_client as welcome_client


def failed_trial(client, count=1):
    workspace = personal_workspace_id("usr_test")
    base = f"/api/v1/workspaces/{workspace}"
    gateway = Gateway(proposal(count=count), failure="unknown")
    client.app.state.replica_gateway = gateway
    client.app.state.settings.reference_remix_provider_enabled = True
    assets = []
    for color in [(255, 0, 0)] + [(0, i * 20, 255) for i in range(count)]:
        reply = client.post(
            base + "/assets",
            headers={"Idempotency-Key": str(uuid4())},
            files={"file": ("source.png", image_payload(color=color), "image/png")},
        )
        assert reply.status_code == 201, reply.text
        assets.append(reply.json()["id"])
    reply = client.post(
        base + "/replica/sessions",
        json={
            "request_id": str(uuid4()),
            "replica_speed": "fast",
            "replication_method": "exact",
            "use_welcome_trial": True,
            "auto_start_after_plan": True,
            "product_asset_ids": assets[:1],
            "references": [{"asset_id": asset} for asset in assets[1:]],
        },
    )
    assert reply.status_code == 202, reply.text
    document_id = reply.json()["id"]
    doc = client.get(base + "/replica/documents/" + document_id).json()
    job_id = doc["payload"]["job_ids"][0]
    assert execute(client, gateway, job_id) == "failed"
    return base, document_id, job_id, gateway


def restart(client, base, doc, job, **body):
    return client.post(
        base + f"/replica/sessions/{doc}/jobs/{job}/restart-free",
        json={"request_id": str(uuid4()), "confirm_new_generation": True, **body},
    )


@pytest.mark.parametrize("count", [1, 10])
def test_failed_trial_keeps_entitlement_and_explicit_restart_is_free(
    welcome_client, monkeypatch, count
):
    client = welcome_client
    base, doc, original, gateway = failed_trial(client, count)
    state = client.get(base + "/promotions/replica-welcome").json()
    assert state["status"] == "recoverable"
    assert state["remaining_tasks"] == 1 and state["recovery_available"] is True
    assert state["session_id"] == doc
    assert len(gateway.images) == count

    def never_recover(*args, **kwargs):
        raise AssertionError(
            "The customer chose a new generation, not old-image recovery"
        )

    monkeypatch.setattr("app.replica_preserved_outputs.recover_outputs", never_recover)
    normal = client.post(
        base + f"/replica/sessions/{doc}/jobs/{original}/retry",
        json={"request_id": str(uuid4())},
    )
    assert normal.status_code == 409
    assert (
        restart(client, base, doc, original, confirm_new_generation=False).status_code
        == 422
    )
    reply = restart(client, base, doc, original)
    assert reply.status_code == 202, reply.text
    replacement = reply.json()["payload"]["job_ids"][0]
    assert replacement != original
    repeated = restart(client, base, doc, original)
    assert repeated.json()["payload"]["job_ids"] == [replacement]
    assert (
        len(gateway.images) == count
    )  # The read/state and constructor do not dispatch inline.
    gateway.failure = None
    assert execute(client, gateway, replacement) == "completed"
    assert len(gateway.images) == 2 * count and len(gateway.plans) == 1

    with client.app.state.session_factory() as session:
        old = session.get(JobRecord, original)
        new = session.get(JobRecord, replacement)
        assert old.status == "failed" and old.error_code == "PROVIDER_OUTCOME_UNKNOWN"
        assert new.root_job_id == original and new.actual_credits == 0
        hold = session.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == replacement
            )
        )
        assert hold.reserved_credits == hold.settled_credits == 0
        assert hold.pricing_snapshot["welcome_trial"] == "replica-welcome-v1"
        account = session.get(WorkspaceCreditAccountRecord, old.workspace_id)
        assert account.balance_credits == account.reserved_credits == 0
        grant = session.get(ReplicaWelcomeGrantRecord, "usr_test")
        assert grant.source_job_id == original and grant.session_id == doc
        receipt = session.get(ReplicaDocumentRecord, doc).payload["_fast_invocations"][
            "F001"
        ]
        assert receipt["state"] == "returned"
        assert receipt["previous_invocation"]["job_id"] == original
        assert (
            session.scalar(
                select(func.count())
                .select_from(JobRecord)
                .where(JobRecord.root_job_id == original)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(
                    AuditEventRecord.action
                    == "promotion.replica_welcome_restart_requested"
                )
            )
            == 1
        )
    state = client.get(base + "/promotions/replica-welcome").json()
    assert state["status"] == "used" and state["remaining_tasks"] == 0


def test_normal_retry_cannot_spoof_the_server_restart_authorization(welcome_client):
    base, doc, original, gateway = failed_trial(welcome_client)
    reply = welcome_client.post(
        base + f"/jobs/{original}/retry?free_replica_restart_job_id={original}",
        headers={"Idempotency-Key": str(uuid4())},
        json={"scope": "failed"},
    )
    assert reply.status_code == 409
    assert len(gateway.images) == 1
    with welcome_client.app.state.session_factory() as session:
        row = session.get(ReplicaDocumentRecord, doc)
        row.payload = {**row.payload, "use_welcome_trial": False}
        session.commit()
    assert restart(welcome_client, base, doc, original).status_code == 409


def test_free_restart_preserves_the_existing_bounded_recovery_limit(welcome_client):
    client = welcome_client
    base, doc, current, gateway = failed_trial(client)
    for _ in range(3):
        reply = restart(client, base, doc, current)
        assert reply.status_code == 202, reply.text
        current = reply.json()["payload"]["job_ids"][0]
        assert execute(client, gateway, current) == "failed"
    assert restart(client, base, doc, current).status_code == 409
    assert len(gateway.images) == 4 and len(gateway.plans) == 1


@pytest.fixture
def pg_welcome_client(settings, postgres_credits, request):
    _, engine = postgres_credits
    settings.database_url = engine.url.render_as_string(hide_password=False)
    return request.getfixturevalue("welcome_client")


def test_parallel_free_restart_creates_one_zero_credit_job(pg_welcome_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client = pg_welcome_client
    base, doc, original, gateway = failed_trial(client)
    barrier = Barrier(4)

    def click():
        barrier.wait(timeout=10)
        reply = restart(client, base, doc, original)
        assert reply.status_code == 202, reply.text
        return reply.json()["payload"]["job_ids"][0]

    with ThreadPoolExecutor(max_workers=4) as pool:
        replacements = list(pool.map(lambda _: click(), range(4)))
    assert len(set(replacements)) == 1
    assert len(gateway.images) == 1
    with client.app.state.session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(JobRecord)
                .where(JobRecord.root_job_id == original)
            )
            == 1
        )
        hold = session.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == replacements[0]
            )
        )
        assert hold.reserved_credits == hold.settled_credits == 0
