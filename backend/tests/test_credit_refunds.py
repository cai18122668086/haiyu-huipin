"""Synthetic original-route refund, entitlement and authorization contracts."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app import credit_refunds as refunds
from app import haiyu_payments as payments
from app.models import CreditGrantRecord, CreditMembershipRecord, CreditRefundRecord
from tests.test_admin_billing import ADMIN, adjustment, enable_admin
from tests.test_credit_payments import (
    BASE,
    NOTIFY,
    balance,
    paid_fields,
    purchase,
)
from tests.test_credit_payments import pilot_client as pilot_client  # noqa: F401


def paid(client, product="credits_1500", money="99.00"):
    order = purchase(client, product=product)
    assert (
        client.post(
            NOTIFY, data=paid_fields(order, money=money, trade="trade_" + order["id"])
        ).text
        == "success"
    )
    return order


def quoted(client, order, policy="unused"):
    response = client.post(
        ADMIN + f"/orders/{order['id']}/refund-quote", params={"policy": policy}
    )
    assert response.status_code == 200, response.text
    return response.json()


def request_refund(client, order, quote, key=None, **changes):
    return client.post(
        ADMIN + f"/orders/{order['id']}/refunds",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={
            "reason": "测试原路退款处理",
            "quote_token": quote["quote_token"],
            "policy": quote["policy"],
            **changes,
        },
    )


def success(monkeypatch):
    monkeypatch.setattr(
        payments,
        "create_refund",
        lambda *args: {"status": "succeeded", "provider_refund_id": "synthetic-refund"},
    )


def test_super_admin_only_and_history_owner(pilot_client):
    order = paid(pilot_client)
    assert (
        pilot_client.post(ADMIN + f"/orders/{order['id']}/refund-quote").status_code
        == 403
    )
    enable_admin(pilot_client, "admin")
    assert (
        pilot_client.post(ADMIN + f"/orders/{order['id']}/refund-quote").status_code
        == 403
    )
    assert pilot_client.get(BASE + f"/purchases/{order['id']}/refunds").json() == {
        "items": []
    }
    assert (
        pilot_client.get(
            "/api/v1/workspaces/ws_not_in_pilot/credits/purchases/"
            + order["id"]
            + "/refunds"
        ).status_code
        == 404
    )


def test_full_refund_permanent_replay_and_late_payment_callback(
    pilot_client, monkeypatch
):
    enable_admin(pilot_client)
    order = paid(pilot_client)
    adjustment(pilot_client, 40)
    quote = quoted(pilot_client, order)
    assert quote["max_refundable_amount_fen"] == 9900
    success(monkeypatch)
    key = str(uuid4())
    response = request_refund(pilot_client, order, quote, key)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["status"] == "succeeded"
    assert record["credits_revoked"] == 1500
    assert balance(pilot_client) == 40
    assert request_refund(pilot_client, order, quote, key).json()["id"] == record["id"]
    assert (
        request_refund(
            pilot_client, order, quote, key, reason="修改已用请求的原因"
        ).status_code
        == 409
    )
    assert (
        pilot_client.post(
            NOTIFY, data=paid_fields(order, trade="trade_" + order["id"])
        ).text
        == "success"
    )
    assert balance(pilot_client) == 40
    result = pilot_client.get(BASE + f"/purchases/{order['id']}").json()
    assert result["status"] == "refunded" and result["refunded_amount_fen"] == 9900
    assert (
        pilot_client.get(BASE + f"/purchases/{order['id']}/refunds").json()["items"][0][
            "status"
        ]
        == "succeeded"
    )


def test_timeout_query_recovery_reuses_id_and_withholds_rights(
    pilot_client, monkeypatch
):
    enable_admin(pilot_client)
    order = paid(pilot_client)

    def timeout(*args):
        raise TimeoutError()

    monkeypatch.setattr(payments, "create_refund", timeout)
    result = request_refund(pilot_client, order, quoted(pilot_client, order)).json()
    assert result["status"] == "processing" and balance(pilot_client) == 0
    assert (
        pilot_client.post(ADMIN + f"/orders/{order['id']}/refund-quote").status_code
        == 409
    )
    seen = []

    def query(*args):
        seen.append(args[3])
        return {"status": "succeeded", "provider_refund_id": "recover"}

    monkeypatch.setattr(payments, "query_refund", query)
    result = pilot_client.post(ADMIN + f"/refunds/{result['id']}/reconcile").json()
    assert result["status"] == "succeeded" and seen == [result["id"]]
    assert balance(pilot_client) == 0


@pytest.mark.parametrize(
    "definitive,expected", [(True, "failed"), (False, "processing")]
)
def test_only_definitive_rejection_restores_rights(
    pilot_client, monkeypatch, definitive, expected
):
    enable_admin(pilot_client)
    order = paid(pilot_client, "pro_annual", "948.00")
    quote = quoted(pilot_client, order)
    assert quote["future_credits_to_cancel"] == 16500

    def reject(*args):
        raise payments.PaymentProviderError("PARAM_ERROR", definitive)

    monkeypatch.setattr(payments, "create_refund", reject)
    result = request_refund(pilot_client, order, quote).json()
    assert result["status"] == expected
    assert balance(pilot_client) == (1500 if definitive else 0)
    with pilot_client.app.state.session_factory() as session:
        member = session.scalar(select(CreditMembershipRecord))
        assert (member.cancelled_at is None) == definitive


def test_consumed_credit_cap_stale_quote_and_frozen_credit(pilot_client, monkeypatch):
    enable_admin(pilot_client)
    order = paid(pilot_client)
    old = quoted(pilot_client, order)
    adjustment(pilot_client, -500)
    assert request_refund(pilot_client, order, old).status_code == 409
    current = quoted(pilot_client, order)
    assert current["max_refundable_amount_fen"] == 6600
    assert (
        request_refund(pilot_client, order, current, amount_fen=6601).status_code == 422
    )
    with pilot_client.app.state.session_factory() as session:
        lot = session.scalar(
            select(CreditGrantRecord).where(CreditGrantRecord.source_key == order["id"])
        )
        lot.held = 10
        session.commit()
    frozen = quoted(pilot_client, order)
    assert frozen["frozen_credits"] == 10
    assert request_refund(pilot_client, order, frozen).status_code == 409


def test_goodwill_can_refund_used_portion_and_no_overrefund(pilot_client, monkeypatch):
    enable_admin(pilot_client)
    order = paid(pilot_client)
    adjustment(pilot_client, -500)
    success(monkeypatch)
    result = request_refund(pilot_client, order, quoted(pilot_client, order)).json()
    assert result["amount_fen"] == 6600 and balance(pilot_client) == 0
    assert quoted(pilot_client, order)["max_refundable_amount_fen"] == 0
    goodwill = quoted(pilot_client, order, "goodwill")
    assert goodwill["max_refundable_amount_fen"] == 3300
    assert request_refund(pilot_client, order, goodwill).json()["status"] == "succeeded"
    assert quoted(pilot_client, order, "goodwill")["max_refundable_amount_fen"] == 0


def test_failed_refund_does_not_restore_expired_lots(pilot_client, monkeypatch):
    from datetime import timedelta

    from app.models import utc_now

    enable_admin(pilot_client)
    order = paid(pilot_client, "pack_medium")
    monkeypatch.setattr(
        payments, "create_refund", lambda *args: {"status": "processing"}
    )
    result = request_refund(pilot_client, order, quoted(pilot_client, order)).json()
    with pilot_client.app.state.session_factory() as session:
        lot = session.scalar(
            select(CreditGrantRecord).where(CreditGrantRecord.source_key == order["id"])
        )
        lot.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
        refunds.settle(session, result["id"], {"status": "failed"})
    assert balance(pilot_client) == 0


def test_pending_order_cannot_refund_and_new_intent_keeps_original_merchant(
    pilot_client, monkeypatch
):
    enable_admin(pilot_client)
    order = purchase(pilot_client)
    assert (
        pilot_client.post(ADMIN + f"/orders/{order['id']}/refund-quote").status_code
        == 409
    )
    pilot_client.post(NOTIFY, data=paid_fields(order))
    pilot_client.app.state.settings.credit_alipay_seller_id = "another-merchant"

    def never(*args):
        pytest.fail("Merchant mismatch must prevent network submission")

    monkeypatch.setattr(payments, "create_refund", never)
    result = request_refund(pilot_client, order, quoted(pilot_client, order)).json()
    assert result["status"] == "requested"
    with pilot_client.app.state.session_factory() as session:
        assert session.get(CreditRefundRecord, result["id"]).attempts == 0


def test_renewal_reserves_refund_pending_period(pilot_client, monkeypatch):
    from app.credit_memberships import aware

    enable_admin(pilot_client)
    first = paid(pilot_client, "pro_annual", "948.00")
    monkeypatch.setattr(
        payments, "create_refund", lambda *args: {"status": "processing"}
    )
    refund = request_refund(pilot_client, first, quoted(pilot_client, first)).json()
    second = paid(pilot_client, "pro_monthly", "99.00")
    with pilot_client.app.state.session_factory() as session:
        old = session.scalar(
            select(CreditMembershipRecord).where(
                CreditMembershipRecord.purchase_id == first["id"]
            )
        )
        new = session.scalar(
            select(CreditMembershipRecord).where(
                CreditMembershipRecord.purchase_id == second["id"]
            )
        )
        assert aware(new.starts_at) == aware(old.ends_at)
        refunds.settle(session, refund["id"], {"status": "failed"})
    assert balance(pilot_client) == 1500
