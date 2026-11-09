from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import services
from app.credit_memberships import aware
from app.errors import ApiError
from app.models import (
    CreditGrantRecord,
    CreditPurchaseRecord,
    JobItemRecord,
    JobRecord,
    PromotionCampaignRecord,
    ReplicaDocumentRecord,
    ReplicaWelcomeGrantRecord,
    UserRecord,
    WorkspaceCreditAccountRecord,
    utc_now,
)
from app.onboarding import ensure_personal_workspace, personal_workspace_id
from app.replica_welcome import CAMPAIGN, NEWCOMER_PACK, bind_session, ensure_grant
from tests.test_credit_payments import NOTIFY, paid_fields
from tests.test_credit_payments import pilot_client as pilot_client


@pytest.fixture
def welcome_client(pilot_client):
    client = pilot_client
    with client.app.state.session_factory() as session:
        user = session.get(UserRecord, "usr_test")
        user.phone_number = "+8613800012345"
        user.created_at = utc_now()
        session.add(
            PromotionCampaignRecord(
                id=CAMPAIGN, enabled=True, starts_at=utc_now() - timedelta(minutes=1)
            )
        )
        ensure_personal_workspace(session, user)
        session.flush()
        assert ensure_grant(session, user)
        session.commit()
    services.set_credit_pilot_workspaces(["ws_test", personal_workspace_id("usr_test")])
    yield client


def base():
    return f"/api/v1/workspaces/{personal_workspace_id('usr_test')}/credits"


def test_verified_signup_automatically_gets_one_grant(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.supertokens_auth import ExternalIdentity
    from tests.test_auth_external import FakeExternalAuthBackend, external_settings

    identity = ExternalIdentity(
        primary_user_id="welcome-synthetic-signup",
        recipe_user_id="welcome-synthetic-recipe",
        email=None,
        email_verified=False,
        phone_number="+8613800099999",
        phone_verified=True,
    )
    with TestClient(
        create_app(external_settings(tmp_path), FakeExternalAuthBackend(identity))
    ) as client:
        with client.app.state.session_factory() as session:
            session.add(
                PromotionCampaignRecord(
                    id=CAMPAIGN,
                    enabled=True,
                    starts_at=utc_now() - timedelta(minutes=1),
                )
            )
            session.commit()
        for _ in range(3):
            assert client.get("/api/v1/me").status_code == 200
        with client.app.state.session_factory() as session:
            assert (
                session.scalar(
                    select(func.count()).select_from(ReplicaWelcomeGrantRecord)
                )
                == 1
            )
            grant = session.scalar(select(ReplicaWelcomeGrantRecord))
            assert (
                session.get(
                    WorkspaceCreditAccountRecord, grant.workspace_id
                ).balance_credits
                == 0
            )


def document(session, *, speed="fast", count=8, trial=True):
    row = ReplicaDocumentRecord(
        id="rep_" + uuid4().hex,
        workspace_id=personal_workspace_id("usr_test"),
        created_by="usr_test",
        kind="session",
        fingerprint=uuid4().hex.ljust(64, "a"),
        status="analysing",
        payload={
            "use_welcome_trial": trial,
            "replica_speed": speed,
            "references": [{"asset_id": "ref" + str(i)} for i in range(count)],
        },
    )
    session.add(row)
    session.flush()
    return row


def task(session, row, *, parent=None):
    job = JobRecord(
        id="job_" + uuid4().hex,
        workspace_id=row.workspace_id,
        type="reference_remix",
        status="queued",
        expected_item_count=12,
        configuration={
            "replica_session_id": row.id,
            "replica_billing": {
                "version": "submitted-references-v1",
                "submitted_units": 8,
            },
            "settings": {"reference_skill": "aurora-ecommerce-reference-remix-fast"},
        },
        root_job_id=parent.id if parent else None,
        retry_of_job_id=parent.id if parent else None,
    )
    session.add(job)
    session.flush()
    session.add_all(
        [
            JobItemRecord(
                id="item_" + uuid4().hex,
                workspace_id=row.workspace_id,
                job_id=job.id,
                item_key=str(i),
                ordinal=i,
                status="pending",
            )
            for i in range(12)
        ]
    )
    session.flush()
    return job


def test_signup_grant_is_unique_and_not_spendable(welcome_client):
    with welcome_client.app.state.session_factory() as session:
        user = session.get(UserRecord, "usr_test")
        first = ensure_grant(session, user)
        assert ensure_grant(session, user).user_id == first.user_id
        assert (
            session.scalar(select(func.count()).select_from(ReplicaWelcomeGrantRecord))
            == 1
        )
        assert (
            session.get(
                WorkspaceCreditAccountRecord, first.workspace_id
            ).balance_credits
            == 0
        )
        assert (
            timedelta(days=6)
            < aware(first.expires_at) - aware(utc_now())
            <= timedelta(days=7)
        )


@pytest.mark.parametrize("speed,count", [("standard", 8), ("fast", 11)])
def test_trial_rejects_other_mode_and_excess_references(welcome_client, speed, count):
    with welcome_client.app.state.session_factory() as session:
        row = document(session, speed=speed, count=count)
        with pytest.raises(ApiError, match="快速复刻"):
            bind_session(session, row)


def test_trial_cannot_bind_two_products_or_ignore_expiry(welcome_client):
    with welcome_client.app.state.session_factory() as session:
        one = document(session)
        bind_session(session, one)
        bind_session(session, one)
        with pytest.raises(ApiError):
            bind_session(session, document(session))
        session.rollback()
        grant = session.get(ReplicaWelcomeGrantRecord, "usr_test")
        grant.expires_at = utc_now() - timedelta(seconds=1)
        with pytest.raises(ApiError, match="到期"):
            bind_session(session, document(session))


def test_trial_generation_and_failed_retry_never_debit_normal_balance(welcome_client):
    with welcome_client.app.state.session_factory() as session:
        row = document(session)
        bind_session(session, row)
        job = task(session, row)
        reserve = services.reserve_image_credits(session, row.workspace_id, job)
        assert reserve.reserved_credits == 0
        assert reserve.pricing_snapshot["credits_per_image"] == 0
        services.settle_credits(session, job, 999)
        assert job.actual_credits == 0
        job.status, job.finished_at, job.failed_item_count = "failed", utc_now(), 12
        session.flush()
        retry = task(session, row, parent=job)
        assert (
            services.reserve_image_credits(
                session, row.workspace_id, retry
            ).reserved_credits
            == 0
        )
        assert (
            session.get(WorkspaceCreditAccountRecord, row.workspace_id).balance_credits
            == 0
        )
        duplicate = task(session, row)
        with pytest.raises(ApiError, match="已使用"):
            services.reserve_image_credits(session, row.workspace_id, duplicate)


def test_normal_fast_job_does_not_get_free_pricing(welcome_client):
    with welcome_client.app.state.session_factory() as session:
        row = document(session, trial=False)
        with pytest.raises(ApiError, match="额度不足"):
            services.reserve_image_credits(
                session, row.workspace_id, task(session, row)
            )


def test_old_signup_and_unverified_phone_receive_no_grant(welcome_client):
    with welcome_client.app.state.session_factory() as session:
        for i, phone, created in [
            (1, "+8613800055555", utc_now() - timedelta(days=1)),
            (2, None, utc_now()),
        ]:
            user = UserRecord(
                id=f"usr_old_{i}",
                auth_provider="development",
                auth_subject=f"old{i}",
                phone_number=phone,
                created_at=created,
            )
            session.add(user)
            session.flush()
            ensure_personal_workspace(session, user)
            assert ensure_grant(session, user) is None


def test_welcome_state_is_scoped_to_personal_workspace(welcome_client):
    path = (
        f"/api/v1/workspaces/{personal_workspace_id('usr_test')}"
        "/promotions/replica-welcome"
    )
    assert welcome_client.get(path).json()["status"] == "available"
    assert (
        welcome_client.get(
            "/api/v1/workspaces/ws_test/promotions/replica-welcome"
        ).json()["status"]
        == "unavailable"
    )
    assert welcome_client.get("/api/v1/public/promotions/replica-welcome").json() == {
        "enabled": True
    }


def test_newcomer_checkout_is_reused_paid_once_and_expires_in_30_days(welcome_client):
    first = None
    for _ in range(2):
        response = welcome_client.post(
            base() + "/purchases",
            headers={"Idempotency-Key": str(uuid4())},
            json={"package_id": NEWCOMER_PACK, "channel": "alipay"},
        )
        assert response.status_code == 201, response.text
        order = response.json()
        assert order["amount_fen"] == 990 and order["credits"] == 240
        if first:
            assert order["id"] == first["id"]
        first = order
    fields = paid_fields(order, money="9.90", trade="newcomer-paid-001")
    for _ in range(2):
        assert welcome_client.post(NOTIFY, data=fields).status_code == 200
    assert welcome_client.get(base()).json()["balance_credits"] == 240
    with welcome_client.app.state.session_factory() as session:
        lot = session.scalar(
            select(CreditGrantRecord).where(CreditGrantRecord.source_key == order["id"])
        )
        paid = session.get(CreditPurchaseRecord, order["id"])
        assert aware(lot.expires_at) - aware(paid.paid_at) == pytest.approx(
            timedelta(days=30), abs=timedelta(seconds=2)
        )
    response = welcome_client.post(
        base() + "/purchases",
        headers={"Idempotency-Key": str(uuid4())},
        json={"package_id": NEWCOMER_PACK, "channel": "alipay"},
    )
    assert response.status_code == 403
    assert NEWCOMER_PACK not in [
        p["id"] for p in welcome_client.get(base() + "/packages").json()["packages"]
    ]


def test_paused_campaign_hides_newcomer_product(welcome_client):
    with welcome_client.app.state.session_factory() as session:
        session.get(PromotionCampaignRecord, CAMPAIGN).enabled = False
        session.commit()
    assert welcome_client.get("/api/v1/public/promotions/replica-welcome").json() == {
        "enabled": False
    }
    assert NEWCOMER_PACK not in [
        p["id"] for p in welcome_client.get(base() + "/packages").json()["packages"]
    ]


def test_free_fast_suite_real_routes_partial_retry_and_download(welcome_client):
    from tests.test_api import image_payload
    from tests.test_replica_fast import Gateway, execute, proposal

    client = welcome_client
    workspace = personal_workspace_id("usr_test")
    route = f"/api/v1/workspaces/{workspace}/replica"
    client.app.state.settings.reference_remix_provider_enabled = True
    gateway = Gateway(proposal(count=2), failure="F002")
    client.app.state.replica_gateway = gateway
    assets = []
    for color in [(255, 0, 0), (0, 0, 255), (0, 255, 0)]:
        response = client.post(
            f"/api/v1/workspaces/{workspace}/assets",
            headers={"Idempotency-Key": str(uuid4())},
            files={"file": ("product.png", image_payload(color=color), "image/png")},
        )
        assert response.status_code == 201, response.text
        assets.append(response.json()["id"])
    payload = {
        "request_id": str(uuid4()),
        "replica_speed": "fast",
        "replication_method": "exact",
        "product_asset_ids": assets[:1],
        "references": [{"asset_id": asset} for asset in assets[1:]],
        "facts": "用户授权红色商品",
        "use_welcome_trial": True,
        "auto_start_after_plan": True,
    }
    response = client.post(route + "/sessions", json=payload)
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    doc = client.get(route + "/documents/" + document_id).json()
    assert doc["payload"]["job_ids"], doc
    job_id = doc["payload"]["job_ids"][0]
    assert execute(client, gateway, job_id) == "partial"
    gateway.failure = None
    retry = client.post(
        route + f"/sessions/{document_id}/jobs/{job_id}/retry",
        json={"request_id": str(uuid4())},
    )
    assert retry.status_code == 202, retry.text
    next_job = retry.json()["payload"]["job_ids"][0]
    assert execute(client, gateway, next_job) == "completed"
    assert client.get(base()).json()["balance_credits"] == 0
    job = client.get(f"/api/v1/workspaces/{workspace}/jobs/{next_job}").json()
    outputs = [
        asset
        for asset in job["assets"]
        if asset["direction"] == "output" and asset["is_deliverable"]
    ]
    assert len(outputs) == 2
    access = client.post(
        f"/api/v1/workspaces/{workspace}/assets/{outputs[0]['asset_id']}/access-url",
        json={"variant": "original", "disposition": "attachment"},
    )
    assert access.status_code == 200, access.text
    assert access.json()["url"]
    assert (
        client.get(f"/api/v1/workspaces/{workspace}/promotions/replica-welcome").json()[
            "status"
        ]
        == "used"
    )
    duplicate = client.post(
        route + "/sessions", json={**payload, "request_id": str(uuid4())}
    )
    assert duplicate.status_code == 409
