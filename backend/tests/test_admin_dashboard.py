from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain import EcommerceLoginState
from app.models import (
    AssetRecord,
    AuditEventRecord,
    CrawlEventRecord,
    CrawlProfileLeaseRecord,
    CrawlTaskRecord,
    EcommerceAccountRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    UserRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
)


def test_admin_overview_is_not_available_without_admin_allowlist(client) -> None:
    access = client.get("/api/v1/admin/overview/access")
    assert access.status_code == 200
    assert access.json() == {"can_manage": False}

    overview = client.get("/api/v1/admin/overview")
    assert overview.status_code == 403
    assert overview.json()["error"]["code"] == "ADMIN_REQUIRED"


def test_admin_overview_aggregates_safe_usage_and_session_data(
    client, settings, monkeypatch
) -> None:
    settings.announcement_admin_emails = ["admin@example.com"]
    monkeypatch.setattr(
        "app.ecommerce_crawl_tasks._schedule", lambda app, task_id: None
    )

    session_factory = client.app.state.session_factory
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add(
            UserRecord(
                id="usr_suspended",
                auth_provider="development",
                auth_subject="suspended-subject",
                email_normalized="suspended@example.com",
                display_name="暂停用户",
                status="suspended",
            )
        )
        session.add(
            WorkspaceRecord(
                id="ws_secondary",
                name="第二工作区",
                status="active",
                created_by_user_id="usr_test",
            )
        )
        session.add_all(
            [
                JobRecord(
                    id="job_processing",
                    workspace_id="ws_test",
                    status="processing",
                    expected_item_count=1,
                ),
                JobRecord(
                    id="job_failed",
                    workspace_id="ws_test",
                    status="failed",
                    expected_item_count=1,
                    finished_at=now,
                ),
            ]
        )
        session.commit()

    account_response = client.post(
        "/api/ecommerce/accounts",
        json={
            "workspace_id": "ws_test",
            "platform": "taobao",
            "account_key": "owner@example.com",
            "display_name": "淘宝主账号",
        },
    )
    assert account_response.status_code == 201
    account = account_response.json()["data"]
    ready = client.patch(
        f"/api/ecommerce/accounts/{account['id']}",
        json={"login_state": EcommerceLoginState.READY.value},
    )
    assert ready.status_code == 200

    task_response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=123456789012",
            "platform": "taobao",
            "account_id": account["id"],
        },
    )
    assert task_response.status_code == 202
    task_id = task_response.json()["data"]["id"]
    with session_factory() as session:
        session.add(
            CrawlProfileLeaseRecord(
                id="lease_admin_overview",
                profile_id=account["profile_id"],
                platform="taobao",
                task_id=task_id,
                owner_id="crawl-worker-test",
                fencing_token=1,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
        session.commit()

    overview = client.get("/api/v1/admin/overview")
    assert overview.status_code == 200
    payload = overview.json()
    assert payload["success"] is True
    data = payload["data"]
    assert data["users"] == {
        "total": 2,
        "active": 1,
        "disabled": 1,
        "recent_signups": 2,
    }
    assert data["workspaces"] == {"total": 2, "active": 2}
    assert data["usage"]["jobs_total"] == 2
    assert data["usage"]["jobs_running"] == 1
    assert data["usage"]["jobs_failed"] == 1
    assert data["usage"]["crawl_tasks_total"] == 1
    assert data["usage"]["crawl_tasks_running"] == 1

    taobao = data["crawler_sessions"]["taobao"]
    assert taobao["account_count"] == 1
    assert taobao["ready_count"] == 1
    assert taobao["busy_count"] == 1
    assert taobao["needs_relogin_count"] == 0
    account_view = taobao["accounts"][0]
    assert account_view["label"] == "淘宝主账号"
    assert account_view["profile_busy"] is True
    assert account_view["active_lease_task_id"] == task_id
    assert "profile_path" not in payload["data"]
    assert "source_url" not in overview.text
    assert "owner@example.com" not in overview.text


def test_admin_user_and_workspace_usage_lists_are_paginated_and_masked(
    client, settings
) -> None:
    settings.announcement_admin_emails = ["admin@example.com"]
    session_factory = client.app.state.session_factory
    now = datetime.now(UTC)
    with session_factory() as session:
        session.add(
            UserRecord(
                id="usr_usage",
                auth_provider="development",
                auth_subject="usage-subject",
                email_normalized="usage@example.com",
                display_name="使用用户",
                status="active",
                last_login_at=now - timedelta(hours=2),
            )
        )
        session.add(
            WorkspaceRecord(
                id="ws_usage",
                name="使用情况工作区",
                status="active",
                created_by_user_id="usr_usage",
            )
        )
        session.add(
            WorkspaceMemberRecord(
                workspace_id="ws_usage",
                user_id="usr_usage",
                role="member",
                status="active",
            )
        )
        session.add(
            JobRecord(
                id="job_usage_processing",
                workspace_id="ws_usage",
                status="processing",
                expected_item_count=1,
            )
        )
        session.add(
            CrawlTaskRecord(
                id="crawl_usage_failed",
                workspace_id="ws_usage",
                created_by="usr_usage",
                platform="taobao",
                source_url="https://item.taobao.com/item.htm?id=usage",
                canonical_url="https://item.taobao.com/item.htm?id=usage",
                request_hash="a" * 64,
                status="failed",
                error_code="LOGIN_REQUIRED",
                error_message_safe="需要重新登录",
                finished_at=now,
            )
        )
        session.commit()

    users = client.get("/api/v1/admin/users?limit=1&offset=1&search=usage@example.com")
    assert users.status_code == 200
    user_data = users.json()["data"]
    assert user_data["pagination"] == {
        "total": 1,
        "limit": 1,
        "offset": 1,
        "has_more": False,
    }
    assert user_data["items"] == []

    users = client.get("/api/v1/admin/users?search=usage@example.com")
    assert users.status_code == 200
    user = users.json()["data"]["items"][0]
    assert user["display_name"] == "使用用户"
    assert user["email"] == "u***@example.com"
    assert user["workspace_count"] == 1
    assert user["active_workspace_count"] == 1
    assert user["workspace_jobs_total"] == 1
    assert user["workspace_jobs_running"] == 1
    assert user["crawl_tasks_total"] == 1
    assert user["crawl_tasks_failed"] == 1
    assert "usage@example.com" not in users.text

    workspaces = client.get("/api/v1/admin/workspaces?search=使用情况")
    assert workspaces.status_code == 200
    workspace = workspaces.json()["data"]["items"][0]
    assert workspace["id"] == "ws_usage"
    assert workspace["member_count"] == 1
    assert workspace["active_member_count"] == 1
    assert workspace["jobs_running"] == 1
    assert workspace["crawl_tasks_failed"] == 1
    assert workspace["account_count"] == 0


def test_admin_usage_lists_require_admin(client) -> None:
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.get("/api/v1/admin/workspaces").status_code == 403


def test_admin_logs_filter_and_redaction_cover_generation_and_crawl(
    client, settings
) -> None:
    settings.announcement_admin_emails = ["admin@example.com"]
    now = datetime.now(UTC)
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        session.add(
            JobRecord(
                id="job_admin_log_failed",
                workspace_id="ws_test",
                type="domestic_main_image",
                status="failed",
                stage="provider",
                progress=42,
                expected_item_count=1,
                failed_item_count=1,
                error_code="PROVIDER_INTERNAL_SECRET",
                error_message="provider stack and https://secret.example.invalid/token",
                configuration={"private_prompt": "do not expose"},
                finished_at=now,
            )
        )
        session.add(
            JobItemRecord(
                id="item_admin_log_failed",
                workspace_id="ws_test",
                job_id="job_admin_log_failed",
                item_key="item-1",
                ordinal=0,
                attempt_count=3,
            )
        )
        session.add(
            EcommerceAccountRecord(
                id="eac_admin_log_search",
                workspace_id="ws_test",
                created_by="usr_test",
                platform="taobao",
                account_key="owner-search@example.com",
                display_name="淘宝搜索账号",
                profile_id="profile_admin_log_search",
                profile_path="/private/profile/admin-log-search",
                status="active",
                login_state="ready",
            )
        )
        session.add(
            CrawlTaskRecord(
                id="crw_admin_log_failed",
                workspace_id="ws_test",
                created_by="usr_test",
                platform="taobao",
                source_url="https://item.taobao.com/item.htm?id=private",
                canonical_url="https://item.taobao.com/item.htm?id=private",
                request_hash="a" * 64,
                status="failed",
                stage="parse",
                progress=65,
                error_code="PARSE_FAILED",
                error_message_safe="解析详情失败",
                request_payload={"url": "https://private.example.invalid"},
                account_id="eac_admin_log_search",
                attempt_count=1,
                max_attempts=2,
                finished_at=now,
            )
        )
        session.add(
            CrawlEventRecord(
                id="cev_admin_log_failed",
                task_id="crw_admin_log_failed",
                event_type="status_changed",
                from_status="running",
                to_status="failed",
                message="解析失败，原始地址 https://private.example.invalid 不应返回",
                details={"raw_html": "secret"},
                created_at=now,
            )
        )
        session.add(
            AuditEventRecord(
                workspace_id="ws_test",
                action="job.dispatch_requested",
                resource_type="job",
                resource_id="job_admin_log_failed",
                details={
                    "source_url": "https://private.example.invalid",
                    "secret": "hidden",
                },
                occurred_at=now,
            )
        )
        session.commit()

    response = client.get(
        "/api/v1/admin/logs",
        params={
            "user_id": "usr_test",
            "workspace_id": "ws_test",
            "status": "failed",
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["pagination"]["total"] == 2
    items = payload["data"]["items"]
    assert {item["kind"] for item in items} == {"generation", "crawl"}
    crawl = next(item for item in items if item["kind"] == "crawl")
    assert crawl["platform"] == "taobao"
    assert crawl["error_code"] == "PARSE_FAILED"
    assert crawl["error_message"] == "平台页面解析失败。"
    generation = next(item for item in items if item["kind"] == "generation")
    assert generation["error_code"] == "PROVIDER_INTERNAL_SECRET"
    assert generation["error_message"] == "任务执行失败，请根据错误码定位。"
    assert generation["attempt_count"] == 3
    for forbidden in (
        "source_url",
        "request_payload",
        "private.example",
        "private_prompt",
        "raw_html",
    ):
        assert forbidden not in response.text

    page_one = client.get(
        "/api/v1/admin/logs",
        params={"limit": 1, "offset": 0},
    )
    assert page_one.status_code == 200
    assert page_one.json()["data"]["pagination"] == {
        "total": 2,
        "limit": 1,
        "offset": 0,
        "has_more": True,
    }
    page_two = client.get(
        "/api/v1/admin/logs",
        params={"limit": 1, "offset": 1},
    )
    assert page_two.status_code == 200
    assert page_two.json()["data"]["pagination"]["has_more"] is False
    assert (
        page_one.json()["data"]["items"][0]["id"]
        != page_two.json()["data"]["items"][0]["id"]
    )

    for params, expected_kind, expected_id in (
        ({"job_id": "job_admin_log_failed"}, "generation", "job_admin_log_failed"),
        ({"crawl_task_id": "crw_admin_log_failed"}, "crawl", "crw_admin_log_failed"),
        ({"account_id": "eac_admin_log_search"}, "crawl", "crw_admin_log_failed"),
        ({"query": "淘宝搜索账号"}, "crawl", "crw_admin_log_failed"),
        ({"query": ".COM"}, "crawl", "crw_admin_log_failed"),
    ):
        search_response = client.get("/api/v1/admin/logs", params=params)
        assert search_response.status_code == 200, params
        search_data = search_response.json()["data"]
        assert search_data["pagination"]["total"] == 1, params
        assert search_data["items"][0]["kind"] == expected_kind, params
        assert search_data["items"][0]["id"] == expected_id, params
        assert "owner-search@example.com" not in search_response.text

    platform_response = client.get(
        "/api/v1/admin/logs",
        params={"platform": "taobao", "kind": "crawl"},
    )
    assert platform_response.status_code == 200
    assert platform_response.json()["data"]["pagination"]["total"] == 1

    detail = client.get("/api/v1/admin/logs/crawl/crw_admin_log_failed")
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["event_count"] == 1
    assert detail_data["events"][0]["message"] == "任务状态发生变化。"
    assert "source_url" not in detail.text
    assert "raw_html" not in detail.text

    generation_detail = client.get("/api/v1/admin/generation-jobs/job_admin_log_failed")
    assert generation_detail.status_code == 200
    assert generation_detail.json()["data"]["events"]
    assert "private_prompt" not in generation_detail.text


def test_admin_generation_detail_includes_items_attempts_and_protected_assets(
    client, settings
) -> None:
    settings.announcement_admin_emails = ["admin@example.com"]
    now = datetime.now(UTC)
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        session.add(
            JobRecord(
                id="job_admin_generation_detail",
                workspace_id="ws_test",
                type="domestic_main_image",
                status="failed",
                stage="provider",
                progress=100,
                expected_item_count=1,
                failed_item_count=1,
                error_code="PROVIDER_INTERNAL_SECRET",
                error_message="private provider stack and prompt must stay hidden",
                configuration={"prompt": "private prompt"},
                finished_at=now,
            )
        )
        session.add(
            JobItemRecord(
                id="item_admin_generation_detail",
                workspace_id="ws_test",
                job_id="job_admin_generation_detail",
                item_key="PAGE-01",
                ordinal=0,
                status="failed",
                stage="provider",
                quality="failed",
                result_source="provider",
                attempt_count=1,
                error_code="PROVIDER_INTERNAL_SECRET",
                error_message="private item error",
                started_at=now,
                finished_at=now,
            )
        )
        session.add(
            JobAttemptRecord(
                id="attempt_admin_generation_detail",
                workspace_id="ws_test",
                job_id="job_admin_generation_detail",
                job_item_id="item_admin_generation_detail",
                attempt_no=1,
                status="failed",
                lease_token="test-token",
                leased_by="test-worker",
                lease_expires_at=now,
                provider="tengli",
                operation="generate",
                provider_idempotency_key="idem-admin-generation-detail",
                error_code="PROVIDER_INTERNAL_SECRET",
                error_message="private provider error",
                started_at=now,
                finished_at=now,
            )
        )
        session.add_all(
            [
                AssetRecord(
                    id="asset_admin_generation_input",
                    workspace_id="ws_test",
                    media_kind="image",
                    origin_kind="upload",
                    lifecycle_status="ready",
                    original_filename="input.png",
                    content_type="image/png",
                    byte_size=10,
                    width=100,
                    height=100,
                    sha256="a" * 64,
                    storage_provider="local",
                    storage_bucket="local",
                    object_key="ws_test/private-input.png",
                ),
                AssetRecord(
                    id="asset_admin_generation_output",
                    workspace_id="ws_test",
                    media_kind="image",
                    origin_kind="generated",
                    lifecycle_status="ready",
                    original_filename="output.png",
                    content_type="image/png",
                    byte_size=20,
                    width=1200,
                    height=1200,
                    sha256="b" * 64,
                    storage_provider="local",
                    storage_bucket="local",
                    object_key="ws_test/private-output.png",
                ),
                AssetRecord(
                    id="asset_admin_generation_pending",
                    workspace_id="ws_test",
                    media_kind="image",
                    origin_kind="generated",
                    lifecycle_status="pending",
                    original_filename="pending.png",
                    content_type="image/png",
                    byte_size=30,
                    width=500,
                    height=500,
                    sha256="c" * 64,
                    storage_provider="local",
                    storage_bucket="local",
                    object_key="ws_test/private-pending.png",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                JobAssetRecord(
                    workspace_id="ws_test",
                    job_id="job_admin_generation_detail",
                    job_item_id="item_admin_generation_detail",
                    asset_id="asset_admin_generation_input",
                    direction="input",
                    role="product",
                    ordinal=0,
                    is_deliverable=False,
                    validation={"valid": True},
                ),
                JobAssetRecord(
                    workspace_id="ws_test",
                    job_id="job_admin_generation_detail",
                    job_item_id="item_admin_generation_detail",
                    asset_id="asset_admin_generation_output",
                    direction="output",
                    role="domestic_main_image",
                    ordinal=0,
                    is_deliverable=True,
                    validation={"valid": True, "quality": "passed"},
                ),
                JobAssetRecord(
                    workspace_id="ws_test",
                    job_id="job_admin_generation_detail",
                    job_item_id="item_admin_generation_detail",
                    asset_id="asset_admin_generation_pending",
                    direction="intermediate",
                    role="provider_preview",
                    ordinal=0,
                    is_deliverable=False,
                    validation={},
                ),
            ]
        )
        session.commit()

    response = client.get("/api/v1/admin/logs/generation/job_admin_generation_detail")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["id"] == "item_admin_generation_detail"
    assert item["status"] == "failed"
    assert item["error_code"] == "PROVIDER_INTERNAL_SECRET"
    assert item["error_message"] == "任务执行失败，请根据错误码定位。"
    assert len(item["provider_stages"]) == 1
    assert item["provider_stages"][0]["provider"] == "tengli"
    assert (
        item["provider_stages"][0]["error_message"]
        == "任务执行失败，请根据错误码定位。"
    )

    assert len(data["input_assets"]) == 1
    assert len(data["output_assets"]) == 1
    assert len(data["intermediate_assets"]) == 1
    input_asset = data["input_assets"][0]
    output_asset = data["output_assets"][0]
    pending_asset = data["intermediate_assets"][0]
    assert input_asset["access_url"].startswith("http")
    assert input_asset["download_url"].startswith("http")
    assert output_asset["access_url"].startswith("http")
    assert output_asset["download_url"].startswith("http")
    assert pending_asset["access_url"] is None
    assert pending_asset["download_url"] is None
    assert pending_asset["access_expires_at"] is None
    assert "private-input.png" not in response.text
    assert "private-output.png" not in response.text
    for forbidden in (
        "private provider stack",
        "private prompt",
        "test-token",
        "leased_by",
    ):
        assert forbidden not in response.text


def test_admin_accounts_are_read_only_and_show_safe_operational_summary(
    client, settings, monkeypatch
) -> None:
    settings.announcement_admin_emails = ["admin@example.com"]
    monkeypatch.setattr(
        "app.ecommerce_crawl_tasks._schedule", lambda app, task_id: None
    )
    account_response = client.post(
        "/api/ecommerce/accounts",
        json={
            "workspace_id": "ws_test",
            "platform": "taobao",
            "account_key": "operator@example.com",
            "display_name": "淘宝运营账号",
        },
    )
    assert account_response.status_code == 201
    account = account_response.json()["data"]
    task_response = client.post(
        "/api/ecommerce/crawl-tasks",
        json={
            "workspace_id": "ws_test",
            "url": "https://item.taobao.com/item.htm?id=987654321",
            "platform": "taobao",
            "account_id": account["id"],
        },
    )
    assert task_response.status_code == 202
    task_id = task_response.json()["data"]["id"]
    now = datetime.now(UTC)
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        task = session.get(CrawlTaskRecord, task_id)
        assert task is not None
        task.status = "failed"
        task.stage = "session"
        task.progress = 100
        task.error_code = "RISK_CONTROL"
        task.error_message_safe = "平台触发风控验证，请完成人工验证后重试。"
        task.finished_at = now
        session.add(
            CrawlProfileLeaseRecord(
                id="lease_admin_account_detail",
                profile_id=account["profile_id"],
                platform="taobao",
                task_id=task_id,
                owner_id="worker-admin-detail",
                fencing_token=4,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
        session.commit()

    response = client.get("/api/v1/admin/accounts?needs_relogin=true")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["pagination"]["total"] == 1
    item = data["items"][0]
    assert item["label"] == "淘宝运营账号"
    assert item["failed_task_count"] == 1
    assert item["risk_control_count"] == 1
    assert item["profile_busy"] is True
    assert item["active_lease"]["task_id"] == task_id
    assert item["recent_tasks"][0]["error_code"] == "RISK_CONTROL"
    assert item["recent_tasks"][0]["error_message"] == "平台触发风控，需要人工处理。"
    for forbidden in (
        "profile_path",
        "operator@example.com",
        "source_url",
        "request_payload",
    ):
        assert forbidden not in response.text

    invalid = client.get("/api/v1/admin/accounts?platform=amazon")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_ACCOUNT_PLATFORM"


def test_admin_account_filters_are_scoped_and_expired_leases_are_not_busy(
    client, settings
) -> None:
    assert client.get("/api/v1/admin/accounts").status_code == 403
    settings.announcement_admin_emails = ["admin@example.com"]
    now = datetime.now(UTC)
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        session.add(
            WorkspaceRecord(
                id="ws_admin_accounts_other",
                name="账号筛选工作区",
                status="active",
                created_by_user_id="usr_test",
            )
        )
        session.add_all(
            [
                EcommerceAccountRecord(
                    id="eac_admin_ready",
                    workspace_id="ws_test",
                    created_by="usr_test",
                    platform="taobao",
                    account_key="ready@example.com",
                    display_name="已登录淘宝",
                    profile_id="profile_admin_ready",
                    profile_path="/private/profile/admin-ready",
                    status="active",
                    login_state="ready",
                ),
                EcommerceAccountRecord(
                    id="eac_admin_login",
                    workspace_id="ws_test",
                    created_by="usr_test",
                    platform="1688",
                    account_key="login@example.com",
                    display_name="待登录1688",
                    profile_id="profile_admin_login",
                    profile_path="/private/profile/admin-login",
                    status="active",
                    login_state="needs_login",
                ),
                EcommerceAccountRecord(
                    id="eac_admin_paused",
                    workspace_id="ws_test",
                    created_by="usr_test",
                    platform="jd",
                    account_key="paused@example.com",
                    display_name="已暂停京东",
                    profile_id="profile_admin_paused",
                    profile_path="/private/profile/admin-paused",
                    status="paused",
                    login_state="unknown",
                ),
                EcommerceAccountRecord(
                    id="eac_admin_other_workspace",
                    workspace_id="ws_admin_accounts_other",
                    created_by="usr_test",
                    platform="pinduoduo",
                    account_key="other@example.com",
                    display_name="其他工作区拼多多",
                    profile_id="profile_admin_other",
                    profile_path="/private/profile/admin-other",
                    status="active",
                    login_state="ready",
                ),
                # Amazon accounts may exist in legacy data, but the domestic
                # admin operations slice must not surface them.
                EcommerceAccountRecord(
                    id="eac_admin_amazon",
                    workspace_id="ws_test",
                    created_by="usr_test",
                    platform="amazon",
                    account_key="amazon@example.com",
                    display_name="Amazon legacy",
                    profile_id="profile_admin_amazon",
                    profile_path="/private/profile/admin-amazon",
                    status="active",
                    login_state="ready",
                ),
                CrawlTaskRecord(
                    id="crw_admin_account_recent",
                    workspace_id="ws_test",
                    created_by="usr_test",
                    platform="1688",
                    source_url="https://detail.1688.com/offer/private",
                    canonical_url="https://detail.1688.com/offer/private",
                    request_hash="b" * 64,
                    status="failed",
                    stage="session",
                    progress=100,
                    error_code="RISK_CONTROL",
                    error_message_safe="平台触发风控验证，请完成人工验证后重试。",
                    account_id="eac_admin_login",
                    finished_at=now,
                ),
                CrawlProfileLeaseRecord(
                    id="lease_admin_account_expired",
                    profile_id="profile_admin_login",
                    platform="1688",
                    task_id="crw_admin_account_recent",
                    owner_id="old-worker",
                    fencing_token=9,
                    acquired_at=now - timedelta(minutes=10),
                    heartbeat_at=now - timedelta(minutes=6),
                    expires_at=now - timedelta(minutes=1),
                ),
            ]
        )
        session.commit()

    all_accounts = client.get("/api/v1/admin/accounts")
    assert all_accounts.status_code == 200
    all_data = all_accounts.json()["data"]
    assert all_data["pagination"]["total"] == 4
    assert {item["platform"] for item in all_data["items"]} == {
        "taobao",
        "1688",
        "jd",
        "pinduoduo",
    }
    assert "Amazon legacy" not in all_accounts.text
    assert "amazon@example.com" not in all_accounts.text

    workspace_accounts = client.get(
        "/api/v1/admin/accounts", params={"workspace_id": "ws_test"}
    )
    assert workspace_accounts.status_code == 200
    assert workspace_accounts.json()["data"]["pagination"]["total"] == 3

    filters = {
        "platform=1688": {"platform": "1688"},
        "status=paused": {"status": "paused"},
        "login_state=needs_login": {"login_state": "needs_login"},
        "needs_relogin=true": {"needs_relogin": True},
    }
    for query, expected in filters.items():
        response = client.get(f"/api/v1/admin/accounts?{query}")
        assert response.status_code == 200, query
        items = response.json()["data"]["items"]
        assert len(items) == 1, query
        for key, value in expected.items():
            assert items[0][key] == value, query

    no_relogin = client.get(
        "/api/v1/admin/accounts", params={"needs_relogin": "false"}
    )
    assert no_relogin.status_code == 200
    assert no_relogin.json()["data"]["pagination"]["total"] == 3

    expired = client.get(
        "/api/v1/admin/accounts", params={"platform": "1688"}
    )
    assert expired.status_code == 200
    expired_item = expired.json()["data"]["items"][0]
    assert expired_item["profile_busy"] is False
    assert expired_item["lease_state"] == "expired"
    assert expired_item["active_lease"] is None
    assert expired_item["last_lease"]["task_id"] == "crw_admin_account_recent"
    assert (
        expired_item["recent_tasks"][0]["error_message"]
        == "平台触发风控，需要人工处理。"
    )

    for query, code in (
        ("status=not-a-status", "INVALID_ACCOUNT_STATUS"),
        ("login_state=not-a-state", "INVALID_ACCOUNT_LOGIN_STATE"),
    ):
        response = client.get(f"/api/v1/admin/accounts?{query}")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == code


def test_admin_logs_require_admin_and_validate_filters(client, settings) -> None:
    assert client.get("/api/v1/admin/logs").status_code == 403
    settings.announcement_admin_emails = ["admin@example.com"]
    invalid_kind = client.get("/api/v1/admin/logs?kind=unknown")
    assert invalid_kind.status_code == 422
    assert invalid_kind.json()["error"]["code"] == "INVALID_LOG_KIND"
    invalid_range = client.get(
        "/api/v1/admin/logs?created_from=2026-09-02T00:00:00Z&created_to=2026-09-01T00:00:00Z"
    )
    assert invalid_range.status_code == 422
    assert invalid_range.json()["error"]["code"] == "INVALID_TIME_RANGE"
    invalid_status = client.get("/api/v1/admin/logs?status=not-a-real-status")
    assert invalid_status.status_code == 422
    assert invalid_status.json()["error"]["code"] == "INVALID_LOG_STATUS"
    invalid_platform = client.get("/api/v1/admin/logs?platform=amazon")
    assert invalid_platform.status_code == 422
    assert invalid_platform.json()["error"]["code"] == "INVALID_LOG_PLATFORM"
