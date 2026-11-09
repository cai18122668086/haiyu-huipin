from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import services
from app.credit_memberships import add_months, aware, membership_state, refresh
from app.models import (
    CreditGrantRecord,
    CreditMembershipRecord,
    CreditPurchaseRecord,
    WorkspaceCreditAccountRecord,
)
from tests.test_credit_payments import (  # noqa: F401
    BASE,
    NOTIFY,
    WORKSPACE,
    balance,
    paid_fields,
)
from tests.test_credit_payments import (
    pilot_client as pilot_client,
)
from tests.test_customer_credit_billing import seed_job


def buy(client, product="pro_monthly", money="99.00", trade=None):
    response = client.post(
        BASE + "/purchases",
        headers={"Idempotency-Key": str(uuid4())},
        json={"package_id": product},
    )
    assert response.status_code == 201, response.text
    order = response.json()
    fields = paid_fields(order, trade=trade or "P" + uuid4().hex, money=money)
    callback = client.post(NOTIFY, data=fields)
    assert callback.status_code == 200, callback.text
    return order, fields


@pytest.mark.parametrize(
    "product,price,credits,months",
    [
        ("light_monthly", "49.00", 1000, 1),
        ("pro_monthly", "99.00", 2400, 1),
        ("team_monthly", "299.00", 8000, 1),
        ("light_annual", "468.00", 1000, 12),
        ("pro_annual", "948.00", 2400, 12),
        ("team_annual", "2868.00", 8000, 12),
        ("flagship_monthly", "799.00", 24000, 1),
        ("flagship_annual", "7668.00", 24000, 12),
    ],
)
def test_approved_membership_catalog_and_first_period_only(
    pilot_client, product, price, credits, months
):
    order, fields = buy(pilot_client, product, price)
    assert order["amount_fen"] == round(float(price) * 100)
    assert balance(pilot_client) == credits
    assert pilot_client.post(NOTIFY, data=fields).status_code == 200
    assert balance(pilot_client) == credits
    member = pilot_client.get(BASE + "/membership").json()
    assert member["monthly_credits"] == credits
    ledger = pilot_client.get(BASE + "/ledger").json()["items"]
    assert ledger[0]["released_credits"] == 0
    assert bool(member["next_grant_at"]) == (months == 12)


def test_calendar_months_use_china_timezone_and_restore_month_end_anchor():
    anchor = datetime(2024, 1, 31, 15, 30, tzinfo=UTC)  # Jan 31 23:30 China
    assert add_months(anchor, 1) == datetime(2024, 2, 29, 15, 30, tzinfo=UTC)
    assert add_months(anchor, 2) == datetime(2024, 3, 31, 15, 30, tzinfo=UTC)
    # Jan 31 UTC is already Feb 1 in China: cycle ends Mar 1 locally.
    assert add_months(datetime(2024, 1, 31, 20, tzinfo=UTC), 1) == datetime(
        2024, 2, 29, 20, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "product,price,credits",
    [
        ("pack_starter_160", "49.00", 800),
        ("pack_small", "99.00", 1800),
        ("pack_medium", "299.00", 6000),
        ("pack_large", "799.00", 18000),
    ],
)
def test_recharge_tiers_settle_once_with_twelve_month_validity(
    pilot_client, product, price, credits
):
    order, fields = buy(pilot_client, product, price)
    assert order["credits"] == balance(pilot_client) == credits
    assert order["amount_fen"] == round(float(price) * 100)
    assert pilot_client.post(NOTIFY, data=fields).status_code == 200
    assert balance(pilot_client) == credits
    with pilot_client.app.state.session_factory() as session:
        lot = session.scalar(select(CreditGrantRecord))
        paid = session.get(CreditPurchaseRecord, order["id"])
        assert (
            abs((aware(lot.expires_at) - add_months(paid.paid_at, 12)).total_seconds())
            < 2
        )


def test_repricing_keeps_pending_starter_price_credits_and_expiry(
    pilot_client, monkeypatch
):
    from dataclasses import replace

    from app.credit_products import PRODUCTS, STARTER_PACK_ID

    current = PRODUCTS[STARTER_PACK_ID]
    with monkeypatch.context() as old_catalog:
        old_catalog.setitem(
            PRODUCTS,
            STARTER_PACK_ID,
            replace(
                current,
                amount_fen=1990,
                credits=160,
                validity_months=0,
                validity_days=30,
            ),
        )
        response = pilot_client.post(
            BASE + "/purchases",
            headers={"Idempotency-Key": str(uuid4())},
            json={"package_id": STARTER_PACK_ID},
        )
        assert response.status_code == 201
        old_order = response.json()
    assert (current.amount_fen, current.credits, current.validity_months) == (
        4900,
        800,
        12,
    )
    assert (
        pilot_client.post(
            NOTIFY, data=paid_fields(old_order, money="19.90")
        ).status_code
        == 200
    )
    assert balance(pilot_client) == 160
    with pilot_client.app.state.session_factory() as session:
        lot = session.scalar(select(CreditGrantRecord))
        paid = session.get(CreditPurchaseRecord, old_order["id"])
        assert (paid.amount_fen, paid.credits) == (1990, 160)
        assert (
            abs(
                (
                    aware(lot.expires_at) - aware(paid.paid_at) - timedelta(days=30)
                ).total_seconds()
            )
            < 2
        )


def test_yearly_issues_monthly_expires_old_period_and_replays_once(pilot_client):
    buy(pilot_client, "pro_annual", "948.00")
    with pilot_client.app.state.session_factory() as session:
        member = session.scalar(select(CreditMembershipRecord))
        anchor = aware(member.starts_at)
        for index in range(1, 12):
            now = add_months(anchor, index) + timedelta(seconds=1)
            account = refresh(session, WORKSPACE, now=now)
            assert account.balance_credits == 2400
            refresh(session, WORKSPACE, now=now)
            assert account.balance_credits == 2400
        assert session.scalar(select(func.count()).select_from(CreditGrantRecord)) == 12
        refresh(session, WORKSPACE, now=add_months(anchor, 12))
        assert account.balance_credits == 0
        assert (
            membership_state(session, WORKSPACE, now=add_months(anchor, 12))["plan_id"]
            is None
        )


def test_renewal_paid_now_starts_after_purchased_period_without_early_grant(
    pilot_client,
):
    buy(pilot_client)
    buy(pilot_client, "light_annual", "468.00")
    assert balance(pilot_client) == 2400
    with pilot_client.app.state.session_factory() as session:
        members = list(
            session.scalars(
                select(CreditMembershipRecord).order_by(
                    CreditMembershipRecord.starts_at
                )
            )
        )
        assert aware(members[1].starts_at) == aware(members[0].ends_at)
        account = refresh(session, WORKSPACE, now=aware(members[1].starts_at))
        assert account.balance_credits == 1000


def test_reserved_expired_credits_stay_bound_to_original_task(pilot_client):
    buy(pilot_client, "light_monthly", "49.00")
    with pilot_client.app.state.session_factory() as session:
        job, items = seed_job(session, count=2)
        reservation = services.reserve_image_credits(session, WORKSPACE, job)
        lot = session.scalar(select(CreditGrantRecord))
        assert lot.held == reservation.reserved_credits == 30
        lot.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        refresh(session, WORKSPACE)
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        assert account.balance_credits == account.reserved_credits == 30
        items[0].status = "succeeded"
        items[1].status = "failed"
        session.flush()
        services.settle_credits(session, job, 0)
        session.commit()
        assert account.balance_credits == account.reserved_credits == 0
        assert lot.remaining == lot.held == 0
        assert job.actual_credits == 15


def test_soonest_expiry_consumed_before_pack_and_release_preserves_valid_pack(
    pilot_client,
):
    buy(pilot_client, "credits_1500", "99.00")
    buy(pilot_client, "light_monthly", "49.00")
    with pilot_client.app.state.session_factory() as session:
        job, _ = seed_job(session, count=1)
        reservation = services.reserve_image_credits(session, WORKSPACE, job)
        lots = list(session.scalars(select(CreditGrantRecord)))
        assert next(lot for lot in lots if lot.kind == "membership").held == 15
        assert next(lot for lot in lots if lot.kind == "purchase").held == 0
        services.release_credits(session, job)
        session.commit()
        assert sum(lot.remaining for lot in lots) == 2500
        assert sum(lot.held for lot in lots) == 0
        assert reservation.status == "released"


def test_pending_legacy_order_keeps_original_non_expiring_contract(pilot_client):
    response = pilot_client.post(
        BASE + "/purchases",
        headers={"Idempotency-Key": str(uuid4())},
        json={"package_id": "credits_1500"},
    )
    order = response.json()
    with pilot_client.app.state.session_factory() as session:
        session.get(CreditPurchaseRecord, order["id"]).product_snapshot = {
            "payment_identity": {"app_id": "test-app", "merchant_id": "test-seller"}
        }
        session.commit()
    assert pilot_client.post(NOTIFY, data=paid_fields(order)).status_code == 200
    with pilot_client.app.state.session_factory() as session:
        assert session.scalar(select(CreditGrantRecord)).expires_at is None


def test_catalog_upgrade_preserves_pending_order_and_annual_member(
    pilot_client, monkeypatch
):
    from dataclasses import replace

    from app.credit_products import PRODUCTS

    current = PRODUCTS["pro_annual"]
    with monkeypatch.context() as old_catalog:
        old_catalog.setitem(PRODUCTS, current.id, replace(current, credits=1500))
        response = pilot_client.post(
            BASE + "/purchases",
            headers={"Idempotency-Key": str(uuid4())},
            json={"package_id": current.id},
        )
        assert response.status_code == 201
        old_order = response.json()
    assert PRODUCTS[current.id].credits == 2400
    assert old_order["credits"] == 1500
    assert (
        pilot_client.post(
            NOTIFY, data=paid_fields(old_order, money="948.00")
        ).status_code
        == 200
    )
    assert balance(pilot_client) == 1500
    with pilot_client.app.state.session_factory() as session:
        member = session.scalar(select(CreditMembershipRecord))
        assert member.monthly_credits == 1500
        account = refresh(session, WORKSPACE, now=add_months(member.starts_at, 1))
        assert account.balance_credits == 1500
        assert member.monthly_credits == 1500


def test_history_pages_do_not_leak_orders_to_other_workspace(pilot_client):
    ids = {buy(pilot_client)[0]["id"] for _ in range(3)}
    response = pilot_client.get(BASE + "/purchases", params={"limit": 2})
    assert response.status_code == 200
    page = response.json()
    next_page = pilot_client.get(
        BASE + "/purchases", params={"limit": 2, "cursor": page["next_cursor"]}
    ).json()
    assert {order["id"] for order in page["items"] + next_page["items"]} == ids
    assert not next_page["next_cursor"]
    other = "/api/v1/workspaces/ws_not_in_pilot/credits/purchases"
    assert pilot_client.get(other).json()["items"] == []
    assert (
        pilot_client.get(other, params={"cursor": next(iter(ids))}).status_code == 422
    )
