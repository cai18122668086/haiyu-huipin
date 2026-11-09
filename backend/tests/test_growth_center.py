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
    ReplicaDocumentRecord,
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
    [("pack_medium", "99.00", 110), ("pro_monthly", "99.00", 240)],
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


def test_reminder_starts_when_delivered_results_are_viewed_and_waits_thirty_seconds(
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
        seconds=30
    )
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=29))
    assert not offer(center_client, "/impression", "post").json()["should_show"]
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=31))
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


def test_late_visit_does_not_remind_twice_on_same_beijing_day(
    center_client, monkeypatch
):
    from datetime import UTC

    now = datetime(2026, 11, 6, 0, 0, tzinfo=UTC)
    with center_client.app.state.session_factory() as session:
        session.get(PromotionCampaignRecord, center.REMINDER).starts_at = (
            now - timedelta(days=2)
        )
        session.get(PromotionCampaignRecord, CAMPAIGN).starts_at = now - timedelta(
            days=2
        )
        session.add(
            NewcomerOfferRecord(
                user_id=BUYER,
                session_id="synthetic-delivered",
                first_viewed_at=now - timedelta(hours=23),
                last_shown_window=-1,
            )
        )
        session.commit()
    monkeypatch.setattr(center, "utc_now", lambda: now)
    assert offer(center_client, "/impression", "post").json()["should_show"]
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(hours=2))
    assert not offer(center_client, "/impression", "post").json()["should_show"]
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(days=1))
    assert offer(center_client, "/impression", "post").json()["should_show"]


def test_unequal_credit_packs_have_no_crossed_out_price_and_regular_refund_unchanged(
    center_client,
):
    from sqlalchemy import select

    from app import credit_products
    from app.models import CreditGrantRecord
    from tests.test_growth_rewards import wallet

    data = offer(center_client).json()
    regular = credit_products.PRODUCTS[credit_products.STARTER_PACK_ID]
    newcomer = credit_products.PRODUCTS["newcomer_160"]
    assert (
        data["regular_amount_fen"] == data["amount_fen"] == newcomer.amount_fen == 990
    )
    assert data["savings_fen"] == 0
    assert (regular.amount_fen, regular.credits, regular.validity_days) == (
        1990,
        160,
        30,
    )
    assert (newcomer.credits, newcomer.validity_days) == (240, 30)
    first, _ = paid(center_client, regular.id, "19.90")
    paid(center_client, regular.id, "19.90")
    assert wallet(center_client, BUYER) == 320
    assert wallet(center_client, INVITER) == 32
    with center_client.app.state.session_factory() as session:
        lot = session.scalar(
            select(CreditGrantRecord).where(CreditGrantRecord.source_key == first["id"])
        )
        assert (
            abs(
                (aware(lot.expires_at) - aware(lot.created_at)).total_seconds()
                - 30 * 86400
            )
            < 5
        )
    refund_order(center_client, first)
    assert wallet(center_client, BUYER) == 160
    assert wallet(center_client, INVITER) == 16
    assert not offer(center_client).json()["eligible"]


@pytest.mark.parametrize("event", ["downloaded", "continue"])
def test_result_actions_accelerate_without_resetting_deadline_or_daily_limit(
    center_client, monkeypatch, event
):
    session_id = delivered_trial(center_client)
    now = utc_now()
    monkeypatch.setattr(center, "utc_now", lambda: now)
    viewed = offer(center_client, "/viewed", "post", {"session_id": session_id}).json()
    with center_client.app.state.session_factory() as session:
        job_id = session.get(ReplicaDocumentRecord, session_id).payload["job_ids"][0]
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=10))
    action = offer(center_client, "/viewed", "post", {"job_id": job_id, "event": event})
    assert action.status_code == 200
    accelerated = action.json()
    assert aware(datetime.fromisoformat(accelerated["ready_at"])) == aware(
        now
    ) + timedelta(seconds=15)
    assert accelerated["window_ends_at"] == viewed["window_ends_at"]
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=14))
    assert not offer(center_client, "/impression", "post").json()["should_show"]
    repeated = offer(
        center_client, "/viewed", "post", {"job_id": job_id, "event": event}
    ).json()
    assert repeated["ready_at"] == accelerated["ready_at"]
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=15))
    assert offer(center_client, "/impression", "post").json()["should_show"]
    after = offer(center_client).json()
    assert after["eligible"] and after["reminded_today"] and not after["should_show"]
    # Closing/reopening is client-side; later downloads cannot claim again today.
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=45))
    offer(center_client, "/viewed", "post", {"job_id": job_id, "event": "downloaded"})
    assert not offer(center_client, "/impression", "post").json()["should_show"]


def test_history_job_identifier_starts_first_view_and_accepts_real_partial_delivery(
    center_client,
):
    session_id = delivered_trial(center_client)
    with center_client.app.state.session_factory() as session:
        job_id = session.get(ReplicaDocumentRecord, session_id).payload["job_ids"][0]
        job = session.get(JobRecord, job_id)
        job.status = "partial"
        job.expected_item_count = 2
        job.failed_item_count = 1
        session.commit()
    viewed = offer(center_client, "/viewed", "post", {"job_id": job_id})
    assert viewed.status_code == 200 and viewed.json()["trial_viewed"]
    assert not viewed.json()["reminded_today"]


def test_result_event_rejects_unrelated_jobs_missing_sources_and_invalid_actions(
    center_client,
):
    session_id = delivered_trial(center_client)
    for body, status in [
        ({}, 422),
        ({"job_id": "job_someone_else"}, 409),
        ({"session_id": "another_session"}, 409),
        (
            {
                "session_id": session_id,
                "job_id": "job_someone_else",
                "event": "downloaded",
            },
            409,
        ),
        ({"session_id": session_id, "event": "paid"}, 422),
    ]:
        assert offer(center_client, "/viewed", "post", body).status_code == status
    assert not offer(center_client).json()["trial_viewed"]


def test_download_must_have_real_deliverable_and_does_not_delay_an_earlier_reminder(
    center_client, monkeypatch
):
    session_id = delivered_trial(center_client, output=False)
    assert (
        offer(
            center_client,
            "/viewed",
            "post",
            {"session_id": session_id, "event": "downloaded"},
        ).status_code
        == 409
    )
    session_id = delivered_trial(center_client)
    now = utc_now()
    monkeypatch.setattr(center, "utc_now", lambda: now)
    viewed = offer(center_client, "/viewed", "post", {"session_id": session_id}).json()
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=29))
    action = offer(
        center_client,
        "/viewed",
        "post",
        {"session_id": session_id, "event": "downloaded"},
    ).json()
    assert action["ready_at"] == viewed["ready_at"]


def test_manual_open_before_timer_suppresses_automatic_reminders_but_can_reopen(
    center_client, monkeypatch
):
    assert not offer(center_client, "/impression", "post", {"manual": True}).json()[
        "should_show"
    ]
    session_id = delivered_trial(center_client)
    now = utc_now()
    monkeypatch.setattr(center, "utc_now", lambda: now)
    viewed = offer(center_client, "/viewed", "post", {"session_id": session_id}).json()
    assert offer(center_client, "/impression", "post", {"manual": True}).json()[
        "should_show"
    ]
    assert offer(center_client).json()["reminded_today"]
    monkeypatch.setattr(center, "utc_now", lambda: now + timedelta(seconds=60))
    assert not offer(center_client, "/impression", "post").json()["should_show"]
    assert offer(center_client, "/impression", "post", {"manual": True}).json()[
        "should_show"
    ]
    assert offer(center_client).json()["window_ends_at"] == viewed["window_ends_at"]
    paid(center_client)
    assert not offer(center_client, "/impression", "post", {"manual": True}).json()[
        "should_show"
    ]
