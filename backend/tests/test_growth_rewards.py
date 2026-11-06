"""Real wallet, signed payment, calendar and refund flows with synthetic users."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import credit_memberships as lots
from app import credit_refunds as refunds
from app import growth_rewards as growth
from app import services
from app.models import (
    CreditGrantRecord,
    CreditPurchaseRecord,
    CreditReservationRecord,
    DailyCheckinRecord,
    GrowthDebtRecord,
    InvitationRecord,
    JobRecord,
    PromotionCampaignRecord,
    ReferralRewardRecord,
    UserRecord,
    WorkspaceCreditAccountRecord,
    utc_now,
)
from app.onboarding import ensure_personal_workspace, personal_workspace_id
from tests.test_credit_payments import NOTIFY, paid_fields
from tests.test_credit_payments import pilot_client as pilot_client

BUYER = "usr_test"
INVITER = "usr_growth_inviter"


def path(user_id=BUYER):
    return f"/api/v1/workspaces/{personal_workspace_id(user_id)}"


@pytest.fixture
def rewards_client(pilot_client):
    with pilot_client.app.state.session_factory() as session:
        user = session.get(UserRecord, BUYER)
        user.phone_number = "+8613800077777"
        user.created_at = utc_now()
        inviter = UserRecord(
            id=INVITER,
            auth_provider="test",
            auth_subject=INVITER,
            phone_number="+8613800066666",
            created_at=utc_now() - timedelta(days=1),
        )
        session.add(inviter)
        session.flush()
        for identifier in (growth.CHECKIN, growth.REFERRAL):
            session.add(
                PromotionCampaignRecord(
                    id=identifier,
                    enabled=True,
                    starts_at=utc_now() - timedelta(minutes=1),
                )
            )
        ensure_personal_workspace(session, user)
        ensure_personal_workspace(session, inviter)
        code = growth.state(session, inviter, personal_workspace_id(INVITER))[
            "invite_url"
        ].split("=")[-1]
        growth.bind_signup_invitation(session, user, code)
        session.commit()
    services.set_credit_pilot_workspaces(
        ["ws_test", personal_workspace_id(BUYER), personal_workspace_id(INVITER)]
    )
    yield pilot_client
    services.set_credit_pilot_workspaces(["ws_test"])


def reward_state(client, user_id=INVITER):
    response = client.get(path(user_id) + "/rewards", headers={"X-User-Id": user_id})
    assert response.status_code == 200, response.text
    return response.json()


def wallet(client, user_id=INVITER):
    return client.get(
        path(user_id) + "/credits", headers={"X-User-Id": user_id}
    ).json()["balance_credits"]


def paid(client, product="pack_medium", money="99.00", *, user_id=BUYER):
    response = client.post(
        path(user_id) + "/credits/purchases",
        headers={"Idempotency-Key": uuid4().hex, "X-User-Id": user_id},
        json={"package_id": product, "channel": "alipay"},
    )
    assert response.status_code == 201, response.text
    order = response.json()
    fields = paid_fields(order, money=money, trade="growth_" + order["id"])
    assert client.post(NOTIFY, data=fields).text == "success"
    return order, fields


def reserve(client, user_id, amount):
    with client.app.state.session_factory() as session:
        account = lots.refresh(session, personal_workspace_id(user_id))
        job = JobRecord(
            id="job_" + uuid4().hex,
            workspace_id=account.workspace_id,
            type="reference_remix",
            status="queued",
            expected_item_count=1,
            configuration={},
        )
        session.add(job)
        session.flush()
        row = CreditReservationRecord(
            id="res_" + uuid4().hex,
            workspace_id=account.workspace_id,
            job_id=job.id,
            reserved_credits=amount,
        )
        session.add(row)
        session.flush()
        account.reserved_credits += amount
        lots.allocate(session, account, row, lots.aware(utc_now()))
        session.commit()
        return row.id


def finish(client, reservation_id, charge):
    with client.app.state.session_factory() as session:
        reservation = session.get(CreditReservationRecord, reservation_id)
        account = lots.refresh(session, reservation.workspace_id)
        account.reserved_credits -= reservation.reserved_credits
        account.balance_credits -= charge
        reservation.status = "settled"
        reservation.settled_credits = charge
        lots.finish_allocation(session, account, reservation, charge)
        session.commit()


def refund_order(client, order, *, policy="unused", status="succeeded"):
    with client.app.state.session_factory() as session:
        quote = refunds.quote(session, order["id"], policy=policy)
        record = refunds.create(
            session,
            order["id"],
            BUYER,
            key=uuid4().hex,
            reason="Synthetic reward adjustment",
            policy=policy,
            quote_token=quote["quote_token"],
        )
        identifier = record.id
        refunds.settle(
            session,
            identifier,
            {"status": status, "provider_refund_id": "synthetic-growth-refund"},
        )
        return identifier


def test_daily_checkin_updates_wallet_once_and_has_real_expiry(rewards_client):
    client = rewards_client
    assert not reward_state(client, BUYER)["checked_in"]
    response = client.post(path() + "/rewards/check-in")
    assert response.status_code == 200, response.text
    assert response.json()["created"] and response.json()["credited_credits"] == 10
    assert client.post(path() + "/rewards/check-in").json()["credited_credits"] == 0
    assert wallet(client, BUYER) == 10
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DailyCheckinRecord)) == 1
        lot = session.scalar(
            select(CreditGrantRecord).where(CreditGrantRecord.kind == "daily_checkin")
        )
        assert 29 <= (lots.aware(lot.expires_at) - lots.aware(utc_now())).days <= 30


def test_beijing_midnight_not_browser_or_utc_day(rewards_client, monkeypatch):
    monkeypatch.setattr(
        growth, "utc_now", lambda: datetime(2026, 11, 6, 15, 59, 59, tzinfo=UTC)
    )
    assert (
        rewards_client.post(path() + "/rewards/check-in").json()["rewards"]["day"]
        == "2026-11-06"
    )
    monkeypatch.setattr(
        growth, "utc_now", lambda: datetime(2026, 11, 6, 16, 0, 1, tzinfo=UTC)
    )
    result = rewards_client.post(path() + "/rewards/check-in").json()
    assert result["created"] and result["rewards"]["day"] == "2026-11-07"
    assert wallet(rewards_client, BUYER) == 20


def test_workspace_scope_and_paused_campaign(rewards_client):
    assert (
        rewards_client.post("/api/v1/workspaces/ws_test/rewards/check-in").status_code
        == 403
    )
    assert rewards_client.get(path(INVITER) + "/rewards").status_code == 404
    with rewards_client.app.state.session_factory() as session:
        session.get(PromotionCampaignRecord, growth.CHECKIN).enabled = False
        session.commit()
    assert not reward_state(rewards_client, BUYER)["checkin_enabled"]
    assert rewards_client.post(path() + "/rewards/check-in").status_code == 403


def test_signed_payment_rewards_ten_percent_once_and_no_reward_on_checkin(
    rewards_client,
):
    order, fields = paid(rewards_client)
    assert wallet(rewards_client, INVITER) == 110
    assert rewards_client.post(NOTIFY, data=fields).text == "success"
    assert wallet(rewards_client, INVITER) == 110
    assert (
        rewards_client.post(path() + "/rewards/check-in").json()["credited_credits"]
        == 10
    )
    assert wallet(rewards_client, INVITER) == 110
    state = reward_state(rewards_client)
    assert state["invited_count"] == 1 and state["earned_credits"] == 110
    with rewards_client.app.state.session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(ReferralRewardRecord)) == 1
        )
        assert (
            session.get(CreditPurchaseRecord, order["id"]).product_snapshot[
                "referral_reward"
            ]["reward_bps"]
            == 1000
        )


def test_membership_reward_only_when_each_period_is_issued(rewards_client):
    order, _ = paid(rewards_client, "pro_annual", "948.00")
    assert wallet(rewards_client, INVITER) == 150
    with rewards_client.app.state.session_factory() as session:
        from app.models import CreditMembershipRecord

        member = session.scalar(
            select(CreditMembershipRecord).where(
                CreditMembershipRecord.purchase_id == order["id"]
            )
        )
        lots.refresh(
            session,
            personal_workspace_id(BUYER),
            now=lots.add_months(member.starts_at, 1) + timedelta(seconds=1),
        )
        session.commit()
    assert wallet(rewards_client, INVITER) == 300
    with rewards_client.app.state.session_factory() as session:
        lots.refresh(session, personal_workspace_id(BUYER))
        session.commit()
        assert (
            session.scalar(select(func.count()).select_from(ReferralRewardRecord)) == 2
        )


def test_scheduled_membership_no_early_reward(rewards_client):
    paid(rewards_client, "pro_monthly", "99.00")
    paid(rewards_client, "pro_monthly", "99.00")
    assert wallet(rewards_client, INVITER) == 150


def test_paid_snapshot_honored_after_campaign_paused(rewards_client):
    response = rewards_client.post(
        path() + "/credits/purchases",
        headers={"Idempotency-Key": uuid4().hex},
        json={"package_id": "pack_small"},
    )
    order = response.json()
    with rewards_client.app.state.session_factory() as session:
        session.get(PromotionCampaignRecord, growth.REFERRAL).enabled = False
        session.commit()
    assert (
        rewards_client.post(
            NOTIFY, data=paid_fields(order, money="29.90", trade="pause_" + order["id"])
        ).text
        == "success"
    )
    assert wallet(rewards_client, INVITER) == 30


def test_refund_reclaims_unused_reward_and_duplicate_settlement_is_safe(rewards_client):
    order, _ = paid(rewards_client)
    identifier = refund_order(rewards_client, order)
    assert wallet(rewards_client, INVITER) == 0
    assert reward_state(rewards_client)["earned_credits"] == 0
    with rewards_client.app.state.session_factory() as session:
        refunds.settle(
            session,
            identifier,
            {"status": "succeeded", "provider_refund_id": "synthetic-growth-refund"},
        )
    assert wallet(rewards_client, INVITER) == 0


def test_failed_refund_preserves_reward(rewards_client):
    order, _ = paid(rewards_client)
    refund_order(rewards_client, order, status="failed")
    assert wallet(rewards_client, INVITER) == 110


def test_spent_reward_adjusts_future_gifts_without_negative_or_paid_wallet(
    rewards_client, monkeypatch
):
    order, _ = paid(rewards_client, "pack_small", "29.90")
    finish(rewards_client, reserve(rewards_client, INVITER, 30), 30)
    refund_order(rewards_client, order)
    assert wallet(rewards_client, INVITER) == 0
    assert reward_state(rewards_client)["pending_adjustment_credits"] == 30
    response = rewards_client.post(
        path(INVITER) + "/rewards/check-in", headers={"X-User-Id": INVITER}
    )
    assert (
        response.json()["credited_credits"] == 0
        and response.json()["adjusted_credits"] == 10
    )
    assert wallet(rewards_client, INVITER) == 0
    with rewards_client.app.state.session_factory() as session:
        assert session.get(GrowthDebtRecord, INVITER).pending_credits == 20


def test_refund_does_not_cancel_active_task_and_reconciles_held_bonus_on_finish(
    rewards_client,
):
    order, _ = paid(rewards_client, "pack_small", "29.90")
    reservation = reserve(rewards_client, INVITER, 30)
    refund_order(rewards_client, order)
    assert wallet(rewards_client, INVITER) == 30
    finish(rewards_client, reservation, 8)
    assert wallet(rewards_client, INVITER) == 0
    assert reward_state(rewards_client)["pending_adjustment_credits"] == 8


def test_expired_unused_bonus_never_creates_adjustment(rewards_client):
    order, _ = paid(rewards_client, "pack_small", "29.90")
    with rewards_client.app.state.session_factory() as session:
        lot = session.scalar(
            select(CreditGrantRecord).where(CreditGrantRecord.kind == "referral_bonus")
        )
        lot.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    assert wallet(rewards_client, INVITER) == 0
    refund_order(rewards_client, order)
    assert reward_state(rewards_client)["pending_adjustment_credits"] == 0


def test_verified_registration_binds_invite_once_and_invalid_code_does_not_block(
    rewards_client,
):
    from app.phone_accounts import persist_otp_account
    from app.supertokens_auth import ExternalIdentity

    code = reward_state(rewards_client)["invite_url"].split("=")[-1]
    with rewards_client.app.state.session_factory() as session:
        identity = ExternalIdentity(
            "growth-new-verified", "growth-recipe", None, False, "+8613800055555", True
        )
        result = persist_otp_account(
            session, identity, "register", "Synthetic123!", code
        )
        user = session.scalar(
            select(UserRecord).where(UserRecord.auth_subject == result.subject)
        )
        assert session.get(InvitationRecord, user.id).inviter_user_id == INVITER
        identity2 = ExternalIdentity(
            "growth-invalid-code", "growth-recipe2", None, False, "+8613800044444", True
        )
        result2 = persist_otp_account(
            session, identity2, "register", "Synthetic123!", "invalid"
        )
        user2 = session.scalar(
            select(UserRecord).where(UserRecord.auth_subject == result2.subject)
        )
        assert session.get(InvitationRecord, user2.id) is None


def test_newcomer_pack_also_rewards_sixteen_credits(rewards_client):
    from app.replica_welcome import CAMPAIGN, ensure_grant

    with rewards_client.app.state.session_factory() as session:
        session.add(
            PromotionCampaignRecord(
                id=CAMPAIGN, enabled=True, starts_at=utc_now() - timedelta(minutes=1)
            )
        )
        assert ensure_grant(session, session.get(UserRecord, BUYER))
        session.commit()
    paid(rewards_client, "newcomer_160", "9.90")
    assert wallet(rewards_client, INVITER) == 16


def test_partial_unused_refund_only_adjusts_corresponding_reward(rewards_client):
    order, _ = paid(rewards_client)
    finish(rewards_client, reserve(rewards_client, BUYER, 550), 550)
    refund_order(rewards_client, order)
    assert wallet(rewards_client, INVITER) == 55
    assert reward_state(rewards_client)["pending_adjustment_credits"] == 0


def test_expired_bonus_only_adjusts_consumed_benefit(rewards_client):
    order, _ = paid(rewards_client, "pack_small", "29.90")
    finish(rewards_client, reserve(rewards_client, INVITER, 8), 8)
    with rewards_client.app.state.session_factory() as session:
        lot = session.scalar(
            select(CreditGrantRecord).where(CreditGrantRecord.kind == "referral_bonus")
        )
        lot.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    assert wallet(rewards_client, INVITER) == 0
    refund_order(rewards_client, order)
    assert reward_state(rewards_client)["pending_adjustment_credits"] == 8


def test_referral_reward_is_only_one_level(rewards_client):
    with rewards_client.app.state.session_factory() as session:
        grandparent = UserRecord(
            id="usr_grandparent",
            auth_provider="test",
            auth_subject="grandparent",
            phone_number="+8613800033333",
            created_at=utc_now() - timedelta(days=2),
        )
        session.add(grandparent)
        session.flush()
        ensure_personal_workspace(session, grandparent)
        session.get(PromotionCampaignRecord, growth.REFERRAL).starts_at = (
            utc_now() - timedelta(days=3)
        )
        code = growth.state(
            session, grandparent, personal_workspace_id(grandparent.id)
        )["invite_url"].split("=")[-1]
        growth.bind_signup_invitation(session, session.get(UserRecord, INVITER), code)
        session.commit()
    paid(rewards_client)
    assert wallet(rewards_client, INVITER) == 110
    with rewards_client.app.state.session_factory() as session:
        assert (
            session.get(
                WorkspaceCreditAccountRecord, personal_workspace_id("usr_grandparent")
            ).balance_credits
            == 0
        )


def test_refund_adjustment_never_takes_inviter_paid_credits(rewards_client):
    order, _ = paid(rewards_client, "pack_small", "29.90")
    finish(rewards_client, reserve(rewards_client, INVITER, 30), 30)
    paid(rewards_client, "pack_small", "29.90", user_id=INVITER)
    assert wallet(rewards_client, INVITER) == 300
    refund_order(rewards_client, order)
    assert wallet(rewards_client, INVITER) == 300
    rewards_client.post(
        path(INVITER) + "/rewards/check-in", headers={"X-User-Id": INVITER}
    )
    assert wallet(rewards_client, INVITER) == 300
    assert reward_state(rewards_client)["pending_adjustment_credits"] == 20


def test_membership_future_refund_keeps_reward_for_consumed_paid_period(rewards_client):
    order, _ = paid(rewards_client, "pro_annual", "948.00")
    finish(rewards_client, reserve(rewards_client, BUYER, 1500), 1500)
    refund_order(rewards_client, order)
    assert wallet(rewards_client, INVITER) == 150
    with rewards_client.app.state.session_factory() as session:
        from app.models import CreditMembershipRecord

        member = session.scalar(
            select(CreditMembershipRecord).where(
                CreditMembershipRecord.purchase_id == order["id"]
            )
        )
        lots.refresh(
            session,
            personal_workspace_id(BUYER),
            now=lots.add_months(member.starts_at, 2),
        )
        session.commit()
        assert (
            session.scalar(select(func.count()).select_from(ReferralRewardRecord)) == 1
        )


def test_full_goodwill_refund_reverses_reward_even_if_buyer_spent_credits(
    rewards_client,
):
    order, _ = paid(rewards_client, "pack_small", "29.90")
    finish(rewards_client, reserve(rewards_client, BUYER, 300), 300)
    refund_order(rewards_client, order, policy="goodwill")
    assert wallet(rewards_client, INVITER) == 0
