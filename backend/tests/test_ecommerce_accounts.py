from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

import app.ecommerce_crawl_tasks as crawl_tasks
from app.models import CrawlTaskRecord


@pytest.fixture(autouse=True)
def admin_access(client) -> None:
    client.app.state.settings.announcement_admin_emails = ["admin@example.com"]


def _stored_task(client, task_id: str) -> CrawlTaskRecord:
    with client.app.state.session_factory() as session:
        task = session.scalar(
            select(CrawlTaskRecord).where(CrawlTaskRecord.id == task_id)
        )
        assert task is not None
        return task


def _create_account(client, **overrides: Any) -> dict[str, Any]:
    payload = {
        "workspace_id": "ws_test",
        "platform": "taobao",
        "account_key": "shop-owner@example.test",
        "display_name": "测试淘宝账号",
    }
    payload.update(overrides)
    response = client.post("/api/ecommerce/accounts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_ecommerce_account_is_workspace_owned_and_profile_isolated(client) -> None:
    account = _create_account(client)
    assert account["platform"] == "taobao"
    assert account["profile_id"].startswith("account-profile-")
    assert account["profile_busy"] is False
    assert "profile_path" not in account

    listed = client.get("/api/ecommerce/accounts", params={"workspace_id": "ws_test"})
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == account["id"]

    updated = client.patch(
        f"/api/ecommerce/accounts/{account['id']}",
        json={"status": "paused"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["status"] == "paused"
    assert updated.json()["data"]["paused_at"] is not None


def test_ecommerce_account_duplicate_and_platform_normalization(client) -> None:
    first = _create_account(client, platform="1688", account_key="seller-1")
    assert first["platform"] == "1688"
    duplicate = client.post(
        "/api/ecommerce/accounts",
        json={
            "workspace_id": "ws_test",
            "platform": "alibaba1688",
            "account_key": "seller-1",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "ECOMMERCE_ACCOUNT_EXISTS"


def test_crawl_task_uses_account_profile_and_rejects_unavailable_account(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    account = _create_account(client, account_key="task-account")
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
            "account_id": account["id"],
        },
    )
    assert response.status_code == 202
    task = response.json()["data"]
    assert task["account_id"] is None
    assert task["profile_id"] is None
    stored = _stored_task(client, task["id"])
    assert stored.account_id == account["id"]
    assert stored.profile_id == account["profile_id"]

    paused = client.patch(
        f"/api/ecommerce/accounts/{account['id']}",
        json={"status": "paused"},
    )
    assert paused.status_code == 200
    rejected = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=987654321098",
            "platform": "taobao",
            "account_id": account["id"],
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "ACCOUNT_NOT_AVAILABLE"


def test_regular_user_cannot_select_or_operate_ecommerce_accounts(
    client, monkeypatch
) -> None:
    account = _create_account(client, account_key="admin-only-account")
    client.app.state.settings.announcement_admin_emails = []

    selected = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
            "account_id": account["id"],
        },
    )
    assert selected.status_code == 403
    assert selected.json()["error"]["code"] == "ACCOUNT_SELECTION_ADMIN_REQUIRED"

    assert client.get("/api/ecommerce/accounts").status_code == 403
    assert client.post(
        "/api/ecommerce/accounts",
        json={
            "workspace_id": "ws_test",
            "platform": "taobao",
            "account_key": "ordinary-user-must-not-create",
        },
    ).status_code == 403
    assert client.get("/api/ecommerce/session/status").status_code == 403
    assert client.post(
        "/api/ecommerce/session/cancel", json={"account_id": account["id"]}
    ).status_code == 403


def test_crawl_task_account_platform_mismatch_is_rejected(client, monkeypatch) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    account = _create_account(client, platform="jd", account_key="jd-account")
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "auto",
            "account_id": account["id"],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_PLATFORM_MISMATCH"


def test_automatic_account_selection_rotates_ready_accounts(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    first = _create_account(client, account_key="auto-account-1")
    second = _create_account(client, account_key="auto-account-2")
    for account in (first, second):
        ready = client.patch(
            f"/api/ecommerce/accounts/{account['id']}",
            json={"login_state": "ready"},
        )
        assert ready.status_code == 200

    payload = {
        "workspace_id": "ws_test",
        "url": "https://item.taobao.com/item.htm?id=123456789012",
        "platform": "taobao",
    }
    first_task = client.post("/api/ecommerce/crawl-tasks", json=payload)
    second_task = client.post("/api/ecommerce/crawl-tasks", json=payload)
    assert first_task.status_code == 202
    assert second_task.status_code == 202
    first_data = first_task.json()["data"]
    second_data = second_task.json()["data"]
    assert first_data["account_id"] is None
    assert second_data["account_id"] is None
    assert _stored_task(client, first_data["id"]).account_id == first["id"]
    assert _stored_task(client, second_data["id"]).account_id == second["id"]


def test_automatic_account_selection_fails_closed_when_configured_pool_unavailable(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(crawl_tasks, "_schedule", lambda app, task_id: None)
    account = _create_account(client, account_key="auto-unavailable")
    # An account configured for the workspace but not yet logged in must not
    # silently fall back to the legacy shared platform session.
    response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_AVAILABLE"

    ready = client.patch(
        f"/api/ecommerce/accounts/{account['id']}",
        json={"login_state": "ready"},
    )
    assert ready.status_code == 200
    task = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=987654321098",
            "platform": "taobao",
        },
    )
    assert task.status_code == 202
    data = task.json()["data"]
    assert data["account_id"] is None
    assert _stored_task(client, data["id"]).account_id == account["id"]


def test_profile_lease_fencing_rejects_stale_worker(client) -> None:
    account = _create_account(client, account_key="lease-account")
    session_factory = client.app.state.session_factory
    first_token = crawl_tasks._acquire_profile_lease(
        session_factory,
        profile_id=account["profile_id"],
        platform="taobao",
        task_id="task-one",
        owner_id="worker-one",
    )
    assert first_token == 1
    with session_factory() as session:
        lease = session.query(crawl_tasks.CrawlProfileLeaseRecord).one()
        lease.expires_at = crawl_tasks.utc_now() - timedelta(seconds=1)
        session.commit()
    second_token = crawl_tasks._acquire_profile_lease(
        session_factory,
        profile_id=account["profile_id"],
        platform="taobao",
        task_id="task-two",
        owner_id="worker-two",
    )
    assert second_token == 2
    assert crawl_tasks._heartbeat_profile_lease(
        session_factory,
        profile_id=account["profile_id"],
        task_id="task-one",
        owner_id="worker-one",
        fencing_token=first_token,
    ) is False
    assert crawl_tasks._heartbeat_profile_lease(
        session_factory,
        profile_id=account["profile_id"],
        task_id="task-two",
        owner_id="worker-two",
        fencing_token=second_token,
    ) is True
    crawl_tasks._release_profile_lease(
        session_factory,
        profile_id=account["profile_id"],
        task_id="task-one",
        owner_id="worker-one",
        fencing_token=first_token,
    )
    with session_factory() as session:
        lease = session.query(crawl_tasks.CrawlProfileLeaseRecord).one()
        assert lease.task_id == "task-two"
        assert lease.fencing_token == 2



def test_account_session_start_syncs_login_state_and_uses_isolated_manager(
    client, monkeypatch
) -> None:
    from app.ecommerce_session import EcommerceSessionManager

    account = _create_account(client, account_key="session-account")
    manager = EcommerceSessionManager(
        client.app.state.settings.ecommerce_session_dir / "accounts" / account["id"],
        platform="taobao",
        session_profile=account["profile_id"],
    )
    client.app.state.ecommerce_account_sessions[account["id"]] = manager
    monkeypatch.setattr(
        manager,
        "start_login",
        lambda target_url=None, **_: {
            "state": "ready",
            "platform": "taobao",
            "sessionProfile": account["profile_id"],
        },
    )

    response = client.post(
        "/api/ecommerce/session/start",
        json={"platform": "taobao", "account_id": account["id"]},
    )

    assert response.status_code == 200
    assert response.json()["data"]["sessionProfile"] == account["profile_id"]
    stored = client.get(f"/api/ecommerce/accounts/{account['id']}").json()["data"]
    assert stored["login_state"] == "ready"
    assert stored["last_login_at"] is not None


def test_account_session_status_syncs_needs_login(client, monkeypatch) -> None:
    from app.ecommerce_session import EcommerceSessionManager

    account = _create_account(client, account_key="status-account")
    manager = EcommerceSessionManager(
        client.app.state.settings.ecommerce_session_dir / "accounts" / account["id"],
        platform="taobao",
        session_profile=account["profile_id"],
    )
    client.app.state.ecommerce_account_sessions[account["id"]] = manager
    monkeypatch.setattr(
        manager,
        "status",
        lambda: {
            "state": "needs_relogin",
            "platform": "taobao",
            "sessionProfile": account["profile_id"],
        },
    )

    response = client.get(
        "/api/ecommerce/session/status",
        params={"platform": "taobao", "account_id": account["id"]},
    )

    assert response.status_code == 200
    stored = client.get(f"/api/ecommerce/accounts/{account['id']}").json()["data"]
    assert stored["login_state"] == "needs_login"


def test_account_session_status_remains_queryable_when_paused(client) -> None:
    account = _create_account(client, account_key="paused-status-account")
    paused = client.patch(
        f"/api/ecommerce/accounts/{account['id']}",
        json={"status": "paused"},
    )
    assert paused.status_code == 200

    response = client.get(
        "/api/ecommerce/session/status",
        params={"platform": "taobao", "account_id": account["id"]},
    )

    assert response.status_code == 200
    assert response.json()["data"]["sessionProfile"] == account["profile_id"]


def test_account_session_rejects_paused_account(client) -> None:
    account = _create_account(client, account_key="paused-session-account")
    paused = client.patch(
        f"/api/ecommerce/accounts/{account['id']}",
        json={"status": "paused"},
    )
    assert paused.status_code == 200

    response = client.post(
        "/api/ecommerce/session/start",
        json={"platform": "taobao", "account_id": account["id"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ACCOUNT_NOT_AVAILABLE"


def test_account_session_rejects_platform_mismatch(client) -> None:
    account = _create_account(
        client, platform="jd", account_key="jd-session-account"
    )
    response = client.post(
        "/api/ecommerce/session/start",
        json={"platform": "taobao", "account_id": account["id"]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ACCOUNT_PLATFORM_MISMATCH"
