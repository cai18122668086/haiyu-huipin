from __future__ import annotations

from app.domain import AdminRole, AdminStatus
from app.models import AdminUserRecord, AuditEventRecord, UserRecord


def configure_admin(client):
    client.app.state.settings.announcement_admin_emails = ["admin@example.com"]
    with client.app.state.session_factory() as session:
        user = session.get(UserRecord, "usr_test")
        assert user is not None
        user.email_normalized = "admin@example.com"
        session.add(
            UserRecord(
                id="usr_second",
                auth_provider="development",
                auth_subject="usr_second",
                email_normalized="second@example.com",
                display_name="第二位同事",
                status="active",
            )
        )
        session.commit()


def test_bootstrap_admin_can_list_and_add_managed_admin(client):
    configure_admin(client)

    listed = client.get("/api/v1/admin/administrators")
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["items"][0]["role"] == "super_admin"
    assert listed.json()["data"]["items"][0]["source"] == "bootstrap"

    created = client.post(
        "/api/v1/admin/administrators",
        json={"email": "SECOND@example.com"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["role"] == "admin"

    with client.app.state.session_factory() as session:
        records = session.query(AdminUserRecord).order_by(AdminUserRecord.user_id).all()
        assert [record.user_id for record in records] == ["usr_second", "usr_test"]
        assert all(record.status == AdminStatus.ACTIVE.value for record in records)
        assert all(
            record.role == AdminRole.SUPER_ADMIN.value
            for record in records
            if record.user_id == "usr_test"
        )
        actions = session.query(AuditEventRecord.action).all()
        assert "admin.created" in [action for (action,) in actions]
        assert "admin.bootstrap_materialized" in [action for (action,) in actions]


def test_super_admin_can_disable_and_reenable_admin(client):
    configure_admin(client)
    assert (
        client.post(
            "/api/v1/admin/administrators", json={"email": "second@example.com"}
        ).status_code
        == 201
    )

    disabled = client.patch(
        "/api/v1/admin/administrators/usr_second",
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"]["status"] == "disabled"

    enabled = client.patch(
        "/api/v1/admin/administrators/usr_second",
        json={"status": "active"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["data"]["status"] == "active"


def test_last_super_admin_cannot_be_demoted_or_disabled(client):
    configure_admin(client)
    assert (
        client.post(
            "/api/v1/admin/administrators", json={"email": "second@example.com"}
        ).status_code
        == 201
    )
    assert (
        client.patch(
            "/api/v1/admin/administrators/usr_second",
            json={"role": "super_admin"},
        ).status_code
        == 200
    )

    assert (
        client.patch(
            "/api/v1/admin/administrators/usr_second",
            json={"status": "disabled"},
        ).status_code
        == 200
    )
    for changes in ({"status": "disabled"}, {"role": "admin"}):
        response = client.patch("/api/v1/admin/administrators/usr_test", json=changes)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "LAST_SUPER_ADMIN"


def test_non_super_admin_cannot_manage_administrators(client):
    configure_admin(client)
    assert (
        client.post(
            "/api/v1/admin/administrators", json={"email": "second@example.com"}
        ).status_code
        == 201
    )
    client.app.state.settings.development_user_id = "usr_second"
    denied = client.post(
        "/api/v1/admin/administrators", json={"email": "missing@example.com"}
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_MANAGE_REQUIRED"
