from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.ecommerce_session import EcommerceSessionManager
from app.models import AuditEventRecord, EcommerceLoginSessionRecord, utc_now


@pytest.fixture(autouse=True)
def admin_access(client) -> None:
    client.app.state.settings.announcement_admin_emails = ["admin@example.com"]


def _create_account(client, **overrides: Any) -> dict[str, Any]:
    payload = {
        "workspace_id": "ws_test",
        "platform": "taobao",
        "account_key": "login-session-account",
        "display_name": "登录会话测试账号",
    }
    payload.update(overrides)
    response = client.post("/api/ecommerce/accounts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _manager(client, account: dict[str, Any]) -> EcommerceSessionManager:
    manager = EcommerceSessionManager(
        client.app.state.settings.ecommerce_session_dir
        / "accounts"
        / account["id"],
        platform="taobao",
        session_profile=account["profile_id"],
    )
    client.app.state.ecommerce_account_sessions[account["id"]] = manager
    return manager


def _pending_manager(client, account: dict[str, Any]) -> EcommerceSessionManager:
    manager = _manager(client, account)
    monkey_status = {
        "state": "login_pending",
        "message": "请完成官方登录。",
        "platform": "taobao",
        "sessionProfile": account["profile_id"],
        "previewAvailable": False,
    }
    manager.status = lambda: dict(monkey_status)  # type: ignore[method-assign]
    manager.start_login = lambda *args, **kwargs: dict(monkey_status)  # type: ignore[method-assign]
    return manager


def test_login_start_is_disabled_by_default(client) -> None:
    client.app.state.settings.ecommerce_login_enabled = False
    account = _create_account(client, account_key="disabled-login")

    response = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ECOMMERCE_LOGIN_DISABLED"


def test_headless_login_status_message_describes_qr_preview(tmp_path: Path) -> None:
    manager = EcommerceSessionManager(tmp_path / "profile", platform="taobao")
    manager._login_headless = True

    assert "二维码" in manager._message("pending")
    assert "弹出" not in manager._message("pending")
    assert "预览已关闭" in manager._message("closed")


def test_production_login_requires_account_and_headless_transport(client) -> None:
    settings = client.app.state.settings
    settings.environment = "production"
    settings.ecommerce_login_enabled = True

    missing_account = client.post(
        "/api/ecommerce/session/start", json={"platform": "taobao"}
    )
    assert missing_account.status_code == 400
    assert missing_account.json()["detail"]["code"] == "ACCOUNT_REQUIRED"

    account = _create_account(client, account_key="production-transport")
    settings.ecommerce_login_transport = "local_visible"
    wrong_transport = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )
    assert wrong_transport.status_code == 503
    assert wrong_transport.json()["detail"]["code"] == "LOGIN_TRANSPORT_UNAVAILABLE"


def test_login_session_is_durable_and_rejects_duplicate_active_flow(
    client,
) -> None:
    account = _create_account(client, account_key="durable-login")
    manager = _pending_manager(client, account)

    started = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["data"]["loginSession"]["status"] == "active"

    duplicate = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "LOGIN_SESSION_BUSY"

    manager.status = lambda: {  # type: ignore[method-assign]
        "state": "ready",
        "platform": "taobao",
        "sessionProfile": account["profile_id"],
        "previewAvailable": False,
    }
    status = client.get(
        "/api/ecommerce/session/status",
        params={"account_id": account["id"], "platform": "taobao"},
    )
    assert status.status_code == 200
    assert status.json()["data"]["loginSession"]["status"] == "completed"
    assert status.json()["data"]["state"] == "ready"

    stored_account = client.get(f"/api/ecommerce/accounts/{account['id']}").json()[
        "data"
    ]
    assert stored_account["login_state"] == "ready"
    with client.app.state.session_factory() as session:
        record = session.scalar(
            select(EcommerceLoginSessionRecord).where(
                EcommerceLoginSessionRecord.account_id == account["id"]
            )
        )
        assert record is not None
        assert record.status == "completed"


def test_account_login_revalidates_a_persisted_ready_profile(client) -> None:
    """An explicit account login must not be short-circuited by stale ready state."""

    account = _create_account(client, account_key="force-relogin")
    manager = _manager(client, account)
    statuses = iter(
        [
            {
                "state": "ready",
                "platform": "taobao",
                "sessionProfile": account["profile_id"],
                "previewAvailable": False,
            },
            {
                "state": "login_pending",
                "platform": "taobao",
                "sessionProfile": account["profile_id"],
                "previewAvailable": False,
            },
        ]
    )
    manager.status = lambda: next(statuses)  # type: ignore[method-assign]
    captured: dict[str, object] = {}

    def start_login(*args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "state": "login_pending",
            "platform": "taobao",
            "sessionProfile": account["profile_id"],
            "previewAvailable": False,
        }

    manager.start_login = start_login  # type: ignore[method-assign]
    response = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )

    assert response.status_code == 200
    assert captured["force"] is True
    assert response.json()["data"]["state"] == "login_pending"
    assert response.json()["data"]["loginSession"]["status"] == "active"


def test_expired_login_session_is_marked_expired(client) -> None:
    account = _create_account(client, account_key="expired-login")
    manager = _manager(client, account)
    manager.status = lambda: {  # type: ignore[method-assign]
        "state": "login_pending",
        "platform": "taobao",
        "sessionProfile": account["profile_id"],
        "previewAvailable": False,
    }
    now = utc_now()
    with client.app.state.session_factory() as session:
        session.add(
            EcommerceLoginSessionRecord(
                id="login_expired_test",
                account_id=account["id"],
                workspace_id="ws_test",
                started_by="usr_test",
                status="active",
                transport="local_visible",
                started_at=now - timedelta(minutes=20),
                expires_at=now - timedelta(minutes=1),
                last_seen_at=now - timedelta(minutes=1),
            )
        )
        session.commit()

    response = client.get(
        "/api/ecommerce/session/status",
        params={"account_id": account["id"], "platform": "taobao"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["loginSession"]["status"] == "expired"
    assert (
        response.json()["data"]["loginSession"]["errorCode"]
        == "LOGIN_SESSION_EXPIRED"
    )


def test_login_preview_is_protected_and_not_ready_is_safe_error(client) -> None:
    client.app.state.settings.ecommerce_login_transport = "headless_qr"
    account = _create_account(client, account_key="preview-login")
    manager = _pending_manager(client, account)
    first = client.get(
        "/api/ecommerce/session/preview", params={"account_id": account["id"]}
    )
    assert first.status_code == 409
    assert first.json()["detail"]["code"] == "LOGIN_SESSION_NOT_ACTIVE"

    manager.status = lambda: {  # type: ignore[method-assign]
        "state": "login_pending",
        "platform": "taobao",
        "sessionProfile": account["profile_id"],
        "previewAvailable": True,
    }
    started = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )
    assert started.status_code == 200
    not_ready = client.get(
        "/api/ecommerce/session/preview", params={"account_id": account["id"]}
    )
    assert not_ready.status_code == 404
    assert not_ready.json()["detail"]["code"] == "LOGIN_PREVIEW_NOT_READY"

    manager.preview_path.parent.mkdir(parents=True, exist_ok=True)
    manager.preview_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    preview = client.get(
        "/api/ecommerce/session/preview", params={"account_id": account["id"]}
    )
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    assert preview.content.startswith(b"\x89PNG")
    assert "profile_path" not in preview.text.lower()


def test_login_input_is_relayed_only_to_active_session(client) -> None:
    account = _create_account(client, account_key="interactive-login")
    manager = _pending_manager(client, account)
    queued: list[dict[str, Any]] = []
    manager.enqueue_input = lambda action: queued.append(action)  # type: ignore[method-assign]
    started = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )
    assert started.status_code == 200

    response = client.post(
        "/api/ecommerce/session/input",
        json={
            "account_id": account["id"],
            "type": "click",
            "x": 420,
            "y": 260,
        },
    )
    assert response.status_code == 200
    assert queued == [{"type": "click", "x": 420.0, "y": 260.0, "button": "left"}]


def test_headless_login_stream_accepts_websocket_cookie_dependency(client) -> None:
    """WebSocket auth must not invoke APIKeyCookie with a Request-only type."""

    client.app.state.settings.ecommerce_login_transport = "headless_qr"
    account = _create_account(client, account_key="stream-cookie-compat")
    manager = _manager(client, account)
    state = {
        "state": "login_pending",
        "message": "请完成官方登录。",
        "platform": "taobao",
        "sessionProfile": account["profile_id"],
        "previewAvailable": True,
    }
    manager.status = lambda: dict(state)  # type: ignore[method-assign]
    manager.start_login = lambda *args, **kwargs: dict(state)  # type: ignore[method-assign]
    started = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )
    assert started.status_code == 200

    with client.websocket_connect(
        f"/api/ecommerce/session/stream?account_id={account['id']}"
    ) as websocket:
        # ``websocket_connect`` only returns after a successful HTTP 101
        # upgrade; the connection may close quickly when the helper exits.
        assert websocket is not None


def test_login_cancel_updates_session_and_audit(client) -> None:
    account = _create_account(client, account_key="cancel-login")
    manager = _pending_manager(client, account)
    manager.cancel_login = lambda: {  # type: ignore[method-assign]
        "state": "signed_out",
        "platform": "taobao",
        "sessionProfile": account["profile_id"],
        "previewAvailable": False,
    }
    started = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )
    assert started.status_code == 200

    cancelled = client.post(
        "/api/ecommerce/session/cancel", json={"account_id": account["id"]}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["loginSession"]["status"] == "cancelled"
    assert cancelled.json()["data"]["state"] == "signed_out"

    with client.app.state.session_factory() as session:
        actions = session.scalars(
            select(AuditEventRecord.action).where(
                AuditEventRecord.resource_id == account["id"]
            )
        ).all()
        assert "ecommerce.login_started" in actions
        assert "ecommerce.login_cancelled" in actions


def test_login_responses_do_not_expose_sensitive_browser_details(client) -> None:
    account = _create_account(client, account_key="safe-login-response")
    _pending_manager(client, account)
    response = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": account["id"], "platform": "taobao"},
    )
    assert response.status_code == 200
    body = response.text.lower()
    assert "cookie" not in body
    assert "profile_path" not in body
    assert "password" not in body
    assert (
        str(Path(client.app.state.settings.ecommerce_session_dir))
        not in response.text
    )


def test_login_account_boundary_rejects_unknown_account(client) -> None:
    response = client.get(
        "/api/ecommerce/session/status",
        params={"account_id": "eac_not_owned", "platform": "taobao"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ECOMMERCE_ACCOUNT_NOT_FOUND"
