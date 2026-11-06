"""Invitation privacy/reporting and server-timed reminders on delivered trials."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app import growth_center as center
from app.credit_memberships import aware
from app.models import (
    AssetRecord,
    JobAssetRecord,
    JobRecord,
    NewcomerOfferRecord,
    PromotionCampaignRecord,
    ReplicaWelcomeGrantRecord,
    UserRecord,
    utc_now,
)
from app.replica_welcome import CAMPAIGN, ensure_grant
from tests.test_credit_payments import pilot_client as pilot_client
from tests.test_growth_rewards import BUYER, INVITER, paid, path, refund_order
from tests.test_growth_rewards import rewards_client as rewards_client
from tests.test_replica_welcome import document


@pytest.fixture
def center_client(rewards_client):
    with rewards_client.app.state.session_factory() as session:
        for identifier in (CAMPAIGN, center.REMINDER):
            session.add(
                PromotionCampaignRecord(
                    id=identifier,
                    enabled=True,
                    starts_at=utc_now() - timedelta(minutes=1),
                )
            )
        session.flush()
        user = session.get(UserRecord, BUYER)
        assert ensure_grant(session, user)
        session.commit()
    return rewards_client


def get_center(client, user=INVITER, query=""):
    response = client.get(
        path(user) + "/invitations" + query, headers={"X-User-Id": user}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def offer(client, suffix="", method="get", body=None):
    return client.request(
        method,
        path() + "/promotions/newcomer-offer" + suffix,
        json=body,
        headers={"X-User-Id": BUYER},
    )


def delivered_trial(client, *, complete=True, output=True):
    with client.app.state.session_factory() as session:
        row = document(session)
        job = JobRecord(
            id="job_" + uuid4().hex,
            workspace_id=row.workspace_id,
            type="reference_remix",
            status="completed" if complete else "queued",
            expected_item_count=1,
            succeeded_item_count=1 if complete else 0,
            finished_at=utc_now() if complete else None,
            configuration={"replica_session_id": row.id},
        )
        session.add(job)
        session.flush()
        row.payload = {**row.payload, "job_ids": [job.id], "submission_complete": True}
        session.get(ReplicaWelcomeGrantRecord, BUYER).session_id = row.id
        if output:
            asset = AssetRecord(
                id="asset_" + uuid4().hex,
                workspace_id=row.workspace_id,
                original_filename="synthetic.png",
                content_type="image/png",
                byte_size=8,
                sha256="0" * 64,
                storage_provider="local",
                storage_bucket="test",
                object_key=uuid4().hex,
                lifecycle_status="ready",
            )
            session.add(asset)
            session.flush()
            session.add(
                JobAssetRecord(
                    workspace_id=row.workspace_id,
                    job_id=job.id,
                    asset_id=asset.id,
                    direction="output",
                    role="generated",
                    is_deliverable=True,
                )
            )
        session.commit()
        return row.id


def test_invitation_center_masks_friends_and_scopes_to_inviter(center_client):
    data = get_center(center_client)
    assert data["invited_count"] == data["unpaid_count"] == 1
    assert data["paid_count"] == data["earned_credits"] == 0
    assert data["items"][0]["friend"] == "138****7777"
    assert "+86" not in str(data) and BUYER not in str(data)
    assert data["invite_url"].endswith("/auth/register?invite=" + data["invite_code"])
    assert get_center(center_client, BUYER)["items"] == []
    assert get_center(center_client, query="?offset=1&limit=1")["items"] == []
    assert get_center(center_client, query="?status=paid")["total"] == 0


@pytest.mark.parametrize(
    "product,money,reward",
    [("pack_medium", "99.00", 110), ("pro_monthly", "99.00", 150)],
)
def test_invitation_center_tracks_real_pack_and_membership_payment(
    center_client, product, money, reward
):
    paid(center_client, product, money)
    data = get_center(center_client, query="?status=paid")
    assert data["paid_count"] == data["total"] == 1
    assert data["unpaid_count"] == 0
    assert data["items"][0]["payment_status"] == "paid"
    assert data["items"][0]["last_paid_at"]
    assert data["earned_credits"] == data["items"][0]["earned_credits"] == reward


def test_invitation_center_refund_updates_status_and_net_rewards(center_client):
    order, _ = paid(center_client)
    refund_order(center_client, order)
    data = get_center(center_client, query="?status=refunded")
    assert data["refunded_count"] == data["total"] == 1
    assert data["paid_count"] == data["earned_credits"] == 0
    assert data["items"][0]["payment_status"] == "refunded"
    assert data["items"][0]["earned_credits"] == 0


@pytest.mark.parametrize("complete,output", [(False, True), (True, False)])
def test_reminder_rejects_pending_or_missing_trial_outputs(
    center_client, complete, output
):
    session_id = delivered_trial(center_client, complete=complete, output=output)
    response = offer(center_client, "/viewed", "post", {"session_id": session_id})
    assert response.status_code == 409
    assert not offer(center_client).json()["trial_viewed"]


def test_reminder_starts_when_delivered_results_are_viewed_and_waits_two_minutes(
    center_client, monkeypatch
):
    session_id = delivered_trial(center_client)
    now = utc_now()
    monkeypatch.setattr(center, "utc_now", lambda: now)
    before = offer(center_client).json()
    assert before["eligible"] and not before["trial_viewed"]
    viewed = offer(center_client, "/viewed", "post", {"session_id": session_id}).json()
    assert viewed["trial_viewed"] and not viewed["should_show"]
    assert aware(datetime.fromisoformat(viewed["ready_at"])) == aware(now) + timedelta(
        seconds=120
    )
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=119))
    assert not offer(center_client, "/impression", "post").json()["should_show"]
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=121))
    assert offer(center_client, "/impression", "post").json()["should_show"]
    assert not offer(center_client, "/impression", "post").json()["should_show"]
    duplicate = offer(
        center_client, "/viewed", "post", {"session_id": session_id}
    ).json()
    assert duplicate["ready_at"] == viewed["ready_at"]
    assert duplicate["window_ends_at"] == viewed["window_ends_at"]


def test_daily_offer_window_survives_refresh_and_stops_after_any_paid_purchase(
    center_client, monkeypatch
):
    now = utc_now()
    with center_client.app.state.session_factory() as session:
        session.add(
            NewcomerOfferRecord(
                user_id=BUYER,
                session_id="synthetic-delivered",
                first_viewed_at=now - timedelta(minutes=3),
                last_shown_window=-1,
            )
        )
        session.commit()
    monkeypatch.setattr(center, "utc_now", lambda: now)
    first = offer(center_client, "/impression", "post").json()
    assert first["should_show"]
    assert offer(center_client).json()["window_ends_at"] == first["window_ends_at"]
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(days=1))
    second = offer(center_client, "/impression", "post").json()
    assert second["should_show"] and second["window_ends_at"] != first["window_ends_at"]
    assert not offer(center_client, "/impression", "post").json()["should_show"]
    paid(center_client, "pro_monthly", "99.00")
    assert not offer(center_client).json()["eligible"]
    assert not offer(center_client, "/impression", "post").json()["should_show"]


def test_reminder_disabled_and_personal_workspace_required(center_client):
    with center_client.app.state.session_factory() as session:
        session.get(PromotionCampaignRecord, center.REMINDER).enabled = False
        session.commit()
    assert not offer(center_client).json()["eligible"]
    assert (
        center_client.get("/api/v1/workspaces/ws_test/invitations").status_code == 403
    )
    assert (
        center_client.get(
            path(INVITER) + "/invitations?limit=101", headers={"X-User-Id": INVITER}
        ).status_code
        == 422
    )
