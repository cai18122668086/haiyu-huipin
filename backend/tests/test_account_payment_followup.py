from uuid import uuid4

from app import haiyu_payments as payments
from tests.test_credit_payments import BASE, OTHER, balance, paid_fields, purchase
from tests.test_credit_payments import pilot_client as pilot_client  # noqa: F401


def test_catalog_preserves_original_packs(pilot_client):
    catalog = pilot_client.get(BASE + "/packages?include_memberships=true").json()
    packs = [p for p in catalog["packages"] if p["kind"] == "pack"]
    assert [
        (p["id"], p["amount_fen"], p["credits"], p["validity_months"]) for p in packs
    ] == [
        ("pack_small", 2990, 300, 12),
        ("pack_medium", 9900, 1100, 12),
        ("pack_large", 29900, 3600, 12),
    ]
    assert catalog["channels"] == ["alipay", "wechat"]


def test_authenticated_query_recovers_missed_notice_once(pilot_client, monkeypatch):
    order = purchase(pilot_client)
    monkeypatch.setattr(payments, "query_payment", lambda *a: paid_fields(order))
    for _ in range(2):
        response = pilot_client.post(BASE + "/purchases/" + order["id"] + "/reconcile")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "paid"
    assert balance(pilot_client) == 1500


def test_pending_and_wrong_query_do_not_credit(pilot_client, monkeypatch):
    order = purchase(pilot_client)
    monkeypatch.setattr(
        payments,
        "query_payment",
        lambda *a: paid_fields(order, trade_status="WAIT_BUYER_PAY"),
    )
    assert (
        pilot_client.post(BASE + "/purchases/" + order["id"] + "/reconcile").json()[
            "status"
        ]
        == "pending"
    )
    monkeypatch.setattr(
        payments, "query_payment", lambda *a: paid_fields(order, money="0.01")
    )
    assert (
        pilot_client.post(BASE + "/purchases/" + order["id"] + "/reconcile").status_code
        == 400
    )
    assert balance(pilot_client) == 0


def test_other_workspace_cannot_reconcile_or_read(pilot_client):
    order = purchase(pilot_client)
    path = f"/api/v1/workspaces/{OTHER}/credits/purchases/{order['id']}"
    assert pilot_client.get(path).status_code == 404
    assert pilot_client.post(path + "/reconcile").status_code == 404


def test_checkout_retry_uses_persisted_order(pilot_client, monkeypatch):
    original = payments.create_checkout

    def fail(*a):
        from app.errors import ApiError

        raise ApiError(502, "TEST_FAILURE", "temporary")

    monkeypatch.setattr(payments, "create_checkout", fail)
    key = str(uuid4())
    assert (
        pilot_client.post(
            BASE + "/purchases",
            headers={"Idempotency-Key": key},
            json={"package_id": "credits_1500"},
        ).status_code
        == 502
    )
    monkeypatch.setattr(payments, "create_checkout", original)
    assert purchase(pilot_client, key)["checkout_url"]
    assert len(pilot_client.get(BASE + "/purchases").json()["items"]) == 1
