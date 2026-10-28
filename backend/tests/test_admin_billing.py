from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    AdminUserRecord,
    AuditEventRecord,
    CreditGrantRecord,
    UserRecord,
    WorkspaceCreditAccountRecord,
)
from tests.test_credit_payments import (
    OTHER,
    WORKSPACE,
    balance,
    paid_fields,
    purchase,
)
from tests.test_credit_payments import pilot_client as pilot_client  # noqa: F401

ADMIN = "/api/v1/admin/billing"


def test_finance_export_preserves_zero_refunds_and_cannot_execute_formulas(
    pilot_client,
):
    import csv
    import io

    from app.models import CreditPurchaseRecord

    enable_admin(pilot_client)
    order = purchase(pilot_client)
    with pilot_client.app.state.session_factory() as session:
        session.get(
            CreditPurchaseRecord, order["id"]
        ).package_name = "=dangerous_formula()"
        session.commit()
    result = pilot_client.get(ADMIN + "/orders-export")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    rows = list(csv.reader(io.StringIO(result.content.decode("utf-8-sig"))))
    assert rows[1][3] == "'=dangerous_formula()"
    assert rows[1][7] == "0"


def enable_admin(client, role="super_admin"):
    with client.app.state.session_factory() as session:
        session.add(
            AdminUserRecord(
                user_id="usr_test", role=role, status="active", permissions=[]
            )
        )
        session.add(
            UserRecord(
                id="usr_customer",
                auth_provider="test",
                auth_subject="customer",
                display_name="Synthetic Customer",
                phone_number="13800000001",
            )
        )
        session.commit()


def adjustment(client, delta, reason="测试用户积分处理", key=None):
    return client.post(
        ADMIN + f"/workspaces/{WORKSPACE}/adjustments",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={"delta": delta, "reason": reason},
    )


def test_non_admin_cannot_read_or_change_finance(pilot_client):
    assert pilot_client.get(ADMIN + "/users").status_code == 403
    assert pilot_client.get(ADMIN + "/orders").status_code == 403
    assert pilot_client.get(ADMIN + "/audit").status_code == 403
    assert adjustment(pilot_client, 100).status_code == 403


def test_manual_memberships_never_fake_paid_orders_and_cancel_idempotently(
    pilot_client,
):
    from app.models import CreditMembershipRecord, CreditPurchaseRecord

    enable_admin(pilot_client)
    path = ADMIN + f"/workspaces/{WORKSPACE}/memberships"
    key = str(uuid4())
    payload = {"product_id": "pro_annual", "reason": "客服补偿年度会员"}
    response = pilot_client.post(path, headers={"Idempotency-Key": key}, json=payload)
    assert response.status_code == 201, response.text
    member = response.json()
    assert (
        pilot_client.post(path, headers={"Idempotency-Key": key}, json=payload).json()
        == member
    )
    assert balance(pilot_client) == 1500
    with pilot_client.app.state.session_factory() as session:
        assert session.scalar(select(CreditPurchaseRecord)) is None
        assert session.get(CreditMembershipRecord, member["id"]).purchase_id is None
    assert pilot_client.get(path).json()["items"][0]["purchase_id"] is None
    cancel_key = str(uuid4())
    for _ in range(2):
        result = pilot_client.post(
            path + f"/{member['id']}/cancel",
            headers={"Idempotency-Key": cancel_key},
            json={"reason": "已核对补偿期限取消"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["credits_revoked"] == 1500
    assert balance(pilot_client) == 0
    overview = pilot_client.get(ADMIN + "/overview").json()
    assert overview["paid_orders"] == 0 and overview["gross_amount_fen"] == 0
    assert overview["net_amount_fen"] == 0


def test_ordinary_admin_cannot_grant_membership(pilot_client):
    enable_admin(pilot_client, "admin")
    path = ADMIN + f"/workspaces/{WORKSPACE}/memberships"
    assert pilot_client.get(path).status_code == 200
    assert (
        pilot_client.post(
            path, json={"product_id": "pro_monthly", "reason": "测试会员权限隔离"}
        ).status_code
        == 403
    )


def test_ordinary_admin_can_read_but_not_adjust(pilot_client):
    enable_admin(pilot_client, "admin")
    assert pilot_client.get(ADMIN + "/users").status_code == 200
    assert not pilot_client.get(ADMIN + "/access").json()["can_adjust"]
    assert adjustment(pilot_client, 100).status_code == 403
    assert (
        pilot_client.patch(
            ADMIN + "/users/usr_customer/status",
            json={"status": "suspended", "reason": "测试账户停用申请"},
        ).status_code
        == 403
    )


def test_adjustment_idempotency_audit_and_lot_invariants(pilot_client):
    enable_admin(pilot_client)
    key = str(uuid4())
    first = adjustment(pilot_client, 200, key=key)
    assert first.status_code == 200, first.text
    assert adjustment(pilot_client, 200, key=key).json() == first.json()
    assert adjustment(pilot_client, 201, key=key).status_code == 409
    assert balance(pilot_client) == 200
    assert adjustment(pilot_client, -30).status_code == 200
    assert balance(pilot_client) == 170
    with pilot_client.app.state.session_factory() as session:
        assert (
            sum(lot.remaining for lot in session.scalars(select(CreditGrantRecord)))
            == 170
        )
        events = list(
            session.scalars(
                select(AuditEventRecord).where(
                    AuditEventRecord.action == "credits.admin_adjusted"
                )
            )
        )
        assert len(events) == 2
        assert all(
            row.actor_user_id == "usr_test" and row.details["reason"] for row in events
        )
    assert adjustment(pilot_client, -171).status_code == 409
    assert balance(pilot_client) == 170


@pytest.mark.parametrize(
    "delta,reason",
    [
        (0, "原因测试测试"),
        (1.5, "原因测试测试"),
        (True, "原因测试测试"),
        (1000001, "原因测试测试"),
        (10, "     "),
    ],
)
def test_invalid_adjustments_rejected(pilot_client, delta, reason):
    enable_admin(pilot_client)
    assert adjustment(pilot_client, delta, reason).status_code == 422
    assert balance(pilot_client) == 0


def test_credit_deduction_cannot_take_reserved_credits(pilot_client):
    enable_admin(pilot_client)
    assert adjustment(pilot_client, 200).status_code == 200
    with pilot_client.app.state.session_factory() as session:
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        account.reserved_credits = 190
        session.scalar(select(CreditGrantRecord)).held = 190
        session.commit()
    assert adjustment(pilot_client, -11).status_code == 409
    assert adjustment(pilot_client, -10).status_code == 200
    assert balance(pilot_client) == 190


def test_masked_user_lookup_and_status_audit(pilot_client):
    enable_admin(pilot_client)
    response = pilot_client.get(ADMIN + "/users", params={"search": "13800000001"})
    assert response.status_code == 200
    assert response.json()["items"][0]["phone_hint"] == "138****0001"
    assert "13800000001" not in response.text
    for status in ["suspended", "active"]:
        response = pilot_client.patch(
            ADMIN + "/users/usr_customer/status",
            json={"status": status, "reason": "测试用户状态处理"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == status
    assert (
        pilot_client.patch(
            ADMIN + "/users/usr_test/status",
            json={"status": "suspended", "reason": "不能禁用自身账号"},
        ).status_code
        == 409
    )
    assert len(pilot_client.get(ADMIN + "/audit").json()["items"]) == 2


def test_admin_query_receipt_fulfills_once_without_manual_paid_override(
    pilot_client, monkeypatch
):
    from app import haiyu_payments as payments

    enable_admin(pilot_client)
    order = purchase(pilot_client)
    monkeypatch.setattr(payments, "query_payment", lambda *args: paid_fields(order))
    for _ in range(2):
        result = pilot_client.post(ADMIN + "/orders/" + order["id"] + "/reconcile")
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "paid"
    assert balance(pilot_client) == 1500
    assert (
        pilot_client.get(ADMIN + "/orders").json()["items"][0]["checkout_url"] is None
    )
    detail = pilot_client.get(ADMIN + f"/workspaces/{WORKSPACE}").json()
    assert detail["balance_credits"] == 1500 and detail["ledger"]


def test_legacy_non_pilot_balance_display_does_not_write_lots(pilot_client):
    with pilot_client.app.state.session_factory() as session:
        session.get(WorkspaceCreditAccountRecord, OTHER).balance_credits = 150
        session.commit()
    assert (
        pilot_client.get(f"/api/v1/workspaces/{OTHER}/credits/membership").json()[
            "pack_credits"
        ]
        == 150
    )
    with pilot_client.app.state.session_factory() as session:
        assert not session.get(WorkspaceCreditAccountRecord, OTHER).lots_initialized
