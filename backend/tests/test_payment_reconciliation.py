"""Browser-independent WeChat settlement; every merchant and receipt is synthetic."""

from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app import credit_payment_reconciliation as queries
from app import haiyu_payments as payments
from app.models import (
    CreditLedgerEntryRecord,
    CreditMembershipRecord,
    CreditPurchaseRecord,
    WorkspaceCreditAccountRecord,
    utc_now,
)
from tests.test_admin_billing import ADMIN, enable_admin
from tests.test_credit_payments import BASE, WORKSPACE, purchase, wx_notice
from tests.test_credit_payments import pilot_client as pilot_client  # noqa: F401


def wx_order(client, monkeypatch, product="pack_medium"):
    settings = client.app.state.settings
    settings.credit_wechat_api_v3_key = SecretStr("")
    settings.credit_wechat_confirmation_mode = "query"
    settings.credit_payment_reconciliation_enabled = True
    monkeypatch.setattr(
        payments, "create_checkout", lambda *args: "weixin://wxpay/synthetic"
    )
    return purchase(client, channel="wechat", product=product)


def receipt(order, **changes):
    return {
        "appid": "test-wx-app",
        "mchid": "test-mch",
        "out_trade_no": order["id"],
        "transaction_id": "wx_" + order["id"],
        "trade_state": "SUCCESS",
        "amount": {"total": order["amount_fen"], "currency": "CNY"},
        **changes,
    }


def run(client):
    queries.maintenance(client.app.state.session_factory, client.app.state.settings)


def row(client, order):
    with client.app.state.session_factory() as session:
        return session.get(CreditPurchaseRecord, order["id"])


def retry_due(client, order):
    with client.app.state.session_factory() as session:
        record = session.get(CreditPurchaseRecord, order["id"])
        record.reconcile_next_at = utc_now() - timedelta(seconds=1)
        record.reconcile_last_at = utc_now() - timedelta(seconds=10)
        session.commit()


def test_query_mode_requires_signing_credentials_and_enabled_worker(pilot_client):
    settings = pilot_client.app.state.settings
    settings.credit_wechat_api_v3_key = SecretStr("x" * 41)
    assert "wechat" not in payments.channels(settings)
    settings.credit_wechat_confirmation_mode = "query"
    assert "wechat" not in payments.channels(settings)
    settings.credit_payment_reconciliation_enabled = True
    assert "wechat" in payments.channels(settings)
    settings.credit_wechat_public_keys = {}
    assert "wechat" not in payments.channels(settings)


def test_background_credits_offline_customer_and_annual_membership_once(
    pilot_client, monkeypatch
):
    order = wx_order(pilot_client, monkeypatch, "pro_annual")
    monkeypatch.setattr(payments, "query_payment", lambda *args: receipt(order))
    # No customer read/reconcile request is made after the checkout response.
    for _ in range(3):
        run(pilot_client)
    with pilot_client.app.state.session_factory() as session:
        record = session.get(CreditPurchaseRecord, order["id"])
        assert record.status == "paid" and record.reconcile_next_at is None
        assert record.reconcile_attempts == 1
        assert (
            session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits == 1500
        )
        assert (
            session.scalar(select(func.count()).select_from(CreditMembershipRecord))
            == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(CreditLedgerEntryRecord))
            == 1
        )
        monitor = queries.status(session, pilot_client.app.state.settings)
        assert monitor["healthy"] and monitor["pending_orders"] == 0


def test_network_failure_is_durable_and_success_after_restart_is_recovered(
    pilot_client, monkeypatch
):
    order = wx_order(pilot_client, monkeypatch)

    def unavailable(*args):
        raise TimeoutError()

    monkeypatch.setattr(payments, "query_payment", unavailable)
    run(pilot_client)
    record = row(pilot_client, order)
    assert (
        record.status == "pending"
        and record.reconcile_last_error == "PAYMENT_QUERY_UNAVAILABLE"
    )
    assert record.reconcile_next_at and record.reconcile_lease_until is None
    retry_due(pilot_client, order)
    # A fresh session/worker resumes from database state, without browser state.
    monkeypatch.setattr(payments, "query_payment", lambda *args: receipt(order))
    run(pilot_client)
    assert row(pilot_client, order).status == "paid"


def test_crashed_worker_lease_expires_and_recovers(pilot_client, monkeypatch):
    order = wx_order(pilot_client, monkeypatch)
    with pilot_client.app.state.session_factory() as session:
        claim = queries._claim(session, order["id"], False)
        assert claim is not None
    called = []
    monkeypatch.setattr(
        payments,
        "query_payment",
        lambda *args: called.append(args[2]) or receipt(order),
    )
    run(pilot_client)
    assert called == []
    with pilot_client.app.state.session_factory() as session:
        record = session.get(CreditPurchaseRecord, order["id"])
        record.reconcile_lease_until = utc_now() - timedelta(seconds=1)
        session.commit()
    run(pilot_client)
    assert called == [order["id"]] and row(pilot_client, order).status == "paid"


@pytest.mark.parametrize(
    "changes",
    [
        {"mchid": "another-merchant"},
        {"appid": "another-app"},
        {"out_trade_no": "pay_different_order"},
        {"amount": {"total": 1, "currency": "CNY"}},
        {"amount": {"total": 9900, "currency": "USD"}},
    ],
)
def test_mismatched_receipt_never_credits_and_is_visible_in_admin(
    pilot_client, monkeypatch, changes
):
    enable_admin(pilot_client)
    order = wx_order(pilot_client, monkeypatch)
    monkeypatch.setattr(
        payments, "query_payment", lambda *args: receipt(order, **changes)
    )
    run(pilot_client)
    with pilot_client.app.state.session_factory() as session:
        assert session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits == 0
    result = pilot_client.get(ADMIN + "/orders?needs_review=true").json()
    assert result["total"] == 1
    assert (
        result["items"][0]["reconciliation"]["last_error"] == "PAYMENT_ORDER_MISMATCH"
    )
    assert row(pilot_client, order).status == "pending"


def test_unpaid_and_paying_remain_retryable_closed_order_stops_polling(
    pilot_client, monkeypatch
):
    order = wx_order(pilot_client, monkeypatch)
    for state in ["NOTPAY", "USERPAYING"]:
        monkeypatch.setattr(
            payments,
            "query_payment",
            lambda *args, state=state: receipt(order, trade_state=state),
        )
        retry_due(pilot_client, order)
        run(pilot_client)
        assert row(pilot_client, order).status == "pending"
    monkeypatch.setattr(
        payments, "query_payment", lambda *args: receipt(order, trade_state="CLOSED")
    )
    retry_due(pilot_client, order)
    run(pilot_client)
    assert row(pilot_client, order).status == "closed"
    assert row(pilot_client, order).reconcile_next_at is None
    monkeypatch.setattr(
        payments, "query_payment", lambda *args: pytest.fail("Closed order queried")
    )
    run(pilot_client)


def test_closed_receipt_with_no_amount_never_grants_and_stops_polling(
    pilot_client, monkeypatch
):
    order = wx_order(pilot_client, monkeypatch)
    data = receipt(order, trade_state="CLOSED")
    data.pop("amount")
    monkeypatch.setattr(payments, "query_payment", lambda *args: data)
    run(pilot_client)
    assert row(pilot_client, order).status == "closed"
    with pilot_client.app.state.session_factory() as session:
        assert session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits == 0


def test_signed_notification_hint_never_credits_without_query(
    pilot_client, monkeypatch
):
    order = wx_order(pilot_client, monkeypatch)
    body, headers = wx_notice(order)
    response = pilot_client.post(
        "/api/v1/payments/wechat/notify", content=body, headers=headers
    )
    assert response.status_code == 204
    with pilot_client.app.state.session_factory() as session:
        assert session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits == 0
    headers["Wechatpay-Signature"] = "forged"
    assert (
        pilot_client.post(
            "/api/v1/payments/wechat/notify", content=body, headers=headers
        ).status_code
        == 400
    )
    monkeypatch.setattr(payments, "query_payment", lambda *args: receipt(order))
    run(pilot_client)
    assert row(pilot_client, order).status == "paid"


def test_changed_merchant_prevents_external_query(pilot_client, monkeypatch):
    order = wx_order(pilot_client, monkeypatch)
    pilot_client.app.state.settings.credit_wechat_mch_id = "changed-merchant"
    monkeypatch.setattr(
        payments, "query_payment", lambda *args: pytest.fail("Wrong merchant queried")
    )
    run(pilot_client)
    assert (
        row(pilot_client, order).reconcile_last_error == "PAYMENT_MERCHANT_UNAVAILABLE"
    )


def test_slow_request_holds_no_database_transaction(pilot_client, monkeypatch):
    order = wx_order(pilot_client, monkeypatch)
    with pilot_client.app.state.session_factory() as session:

        def query(*args):
            assert not session.in_transaction()
            return receipt(order)

        monkeypatch.setattr(payments, "query_payment", query)
        queries.process(session, pilot_client.app.state.settings, order["id"])
    assert row(pilot_client, order).status == "paid"


def test_manual_customer_reconciliation_obeys_lease_and_cooldown(
    pilot_client, monkeypatch
):
    order = wx_order(pilot_client, monkeypatch)
    calls = []
    monkeypatch.setattr(
        payments,
        "query_payment",
        lambda *args: calls.append(args[2]) or receipt(order, trade_state="NOTPAY"),
    )
    for _ in range(3):
        assert (
            pilot_client.post(BASE + f"/purchases/{order['id']}/reconcile").status_code
            == 200
        )
    assert calls == [order["id"]]


def test_late_verified_payment_can_recover_previously_closed_local_order(
    pilot_client, monkeypatch
):
    from app.credit_payment_routes import _native_transaction

    order = wx_order(pilot_client, monkeypatch)
    with pilot_client.app.state.session_factory() as session:
        record = session.get(CreditPurchaseRecord, order["id"])
        record.status = "closed"
        session.commit()
        _native_transaction(session, "wechat", receipt(order))
    assert row(pilot_client, order).status == "paid"


def test_channel_errors_and_many_days_old_pending_order_are_not_abandoned(
    pilot_client, monkeypatch
):
    order = wx_order(pilot_client, monkeypatch)
    with pilot_client.app.state.session_factory() as session:
        record = session.get(CreditPurchaseRecord, order["id"])
        record.created_at = utc_now() - timedelta(days=8)
        session.commit()

    def missing(*args):
        raise payments.PaymentProviderError("ORDER_NOT_EXIST", True)

    monkeypatch.setattr(payments, "query_payment", missing)
    run(pilot_client)
    assert row(pilot_client, order).status == "pending"
    assert row(pilot_client, order).reconcile_next_at is not None
    retry_due(pilot_client, order)
    monkeypatch.setattr(payments, "query_payment", lambda *args: receipt(order))
    run(pilot_client)
    assert row(pilot_client, order).status == "paid"
