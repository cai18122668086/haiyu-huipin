from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import CrawlTaskRecord, EcommerceWorkerRecord, utc_now


def _enroll(client, device_id="device-test"):
    response = client.post(
        "/api/ecommerce/workers/enrollments",
        json={"workspace_id": "ws_test", "device_id": device_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _register(client, device_id="device-test", capabilities=None):
    enrollment = _enroll(client, device_id)
    response = client.post(
        "/api/ecommerce/workers/register",
        json={
            "enrollment_token": enrollment["enrollment_token"],
            "device_id": device_id,
            "protocol_version": "worker.v1",
            "agent_version": "0.1.0",
            "capabilities": capabilities or ["browser.taobao"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _queued_task(client, task_id="crw_worker_test", max_attempts=1):
    with client.app.state.session_factory() as session:
        session.add(
            CrawlTaskRecord(
                id=task_id,
                workspace_id="ws_test",
                created_by="usr_test",
                platform="taobao",
                source_url="https://item.taobao.com/item.htm?id=123456789012",
                canonical_url="https://item.taobao.com/item.htm?id=123456789012",
                request_hash="a" * 64,
                status="queued",
                stage="queued",
                max_attempts=max_attempts,
                profile_id="domestic-taobao",
                request_payload={"platform": "taobao", "includeDescImages": True},
                queued_at=utc_now(),
            )
        )
        session.commit()


def test_worker_registration_is_scope_bound_and_one_time(client):
    enrollment = _enroll(client)
    payload = {
        "enrollment_token": enrollment["enrollment_token"],
        "device_id": "device-test",
        "protocol_version": "worker.v1",
        "agent_version": "0.1.0",
        "capabilities": ["browser.taobao", "login.taobao"],
    }
    registered = client.post("/api/ecommerce/workers/register", json=payload)
    assert registered.status_code == 201, registered.text
    data = registered.json()["data"]
    assert data["worker"]["status"] == "online"
    assert data["worker"]["capabilities"] == ["browser.taobao", "login.taobao"]
    assert "session_token_hash" not in data["worker"]

    replay = client.post("/api/ecommerce/workers/register", json=payload)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "WORKER_ENROLLMENT_INVALID"


def test_worker_recovery_re_registers_after_session_expiry(client):
    data = _register(client, "device-recovery")
    worker_id = data["worker"]["id"]
    recovery_token = data["recovery_token"]
    recovered = client.post(
        "/api/ecommerce/workers/recover",
        json={
            "worker_id": worker_id,
            "recovery_token": recovery_token,
            "device_id": "device-recovery",
            "protocol_version": "worker.v1",
            "agent_version": "0.1.1",
            "capabilities": ["browser.jd", "login.jd"],
        },
    )
    assert recovered.status_code == 200, recovered.text
    recovered_data = recovered.json()["data"]
    assert recovered_data["worker"]["id"] == worker_id
    assert recovered_data["session_token"] != data["session_token"]
    assert recovered_data["recovery_token"] != recovery_token

    replay = client.post(
        "/api/ecommerce/workers/recover",
        json={
            "worker_id": worker_id,
            "recovery_token": recovery_token,
            "device_id": "device-recovery",
            "protocol_version": "worker.v1",
            "agent_version": "0.1.1",
            "capabilities": ["browser.jd"],
        },
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "WORKER_RECOVERY_INVALID"


def test_worker_protocol_rejects_sensitive_or_unknown_fields(client):
    enrollment = _enroll(client, "device-strict")
    payload = {
        "enrollment_token": enrollment["enrollment_token"],
        "device_id": "device-strict",
        "protocol_version": "worker.v1",
        "agent_version": "0.1.0",
        "capabilities": [],
        "cookies": "must-not-be-accepted",
    }
    response = client.post("/api/ecommerce/workers/register", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_worker_token_refresh_rotates_and_invalidates_previous_token(client):
    data = _register(client, "device-token-refresh")
    worker_id = data["worker"]["id"]
    old_token = data["session_token"]
    refreshed = client.post(
        f"/api/ecommerce/workers/{worker_id}/token/refresh",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert refreshed.status_code == 200, refreshed.text
    new_token = refreshed.json()["data"]["session_token"]
    assert new_token and new_token != old_token

    old_heartbeat = client.post(
        f"/api/ecommerce/workers/{worker_id}/heartbeat",
        headers={"Authorization": f"Bearer {old_token}"},
        json={
            "protocol_version": "worker.v1",
            "agent_version": "0.1.0",
            "ready": True,
        },
    )
    assert old_heartbeat.status_code == 401
    assert old_heartbeat.json()["error"]["code"] == "WORKER_AUTH_INVALID"

    current_heartbeat = client.post(
        f"/api/ecommerce/workers/{worker_id}/heartbeat",
        headers={"Authorization": f"Bearer {new_token}"},
        json={
            "protocol_version": "worker.v1",
            "agent_version": "0.1.0",
            "ready": True,
        },
    )
    assert current_heartbeat.status_code == 200


def test_worker_heartbeat_and_expiry_are_explicit(client):
    enrollment = _enroll(client, "device-heartbeat")
    registered = client.post(
        "/api/ecommerce/workers/register",
        json={
            "enrollment_token": enrollment["enrollment_token"],
            "device_id": "device-heartbeat",
            "protocol_version": "worker.v1",
            "agent_version": "0.1.0",
            "capabilities": ["browser.taobao"],
        },
    )
    data = registered.json()["data"]
    heartbeat = client.post(
        f"/api/ecommerce/workers/{data['worker']['id']}/heartbeat",
        headers={"Authorization": f"Bearer {data['session_token']}"},
        json={
            "protocol_version": "worker.v1",
            "agent_version": "0.1.1",
            "ready": True,
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["data"]["status"] == "online"

    with client.app.state.session_factory() as session:
        worker = session.scalar(
            select(EcommerceWorkerRecord).where(
                EcommerceWorkerRecord.id == data["worker"]["id"]
            )
        )
        assert worker is not None
        worker.last_seen_at = datetime.now(UTC) - timedelta(seconds=1000)
        session.commit()
    listed = client.get("/api/ecommerce/workers", params={"workspace_id": "ws_test"})
    assert listed.status_code == 200
    assert listed.json()["data"][0]["status"] == "offline"
    assert listed.json()["data"][0]["error_code"] == "WORKER_OFFLINE"


def test_worker_can_sync_account_login_state_without_browser_material(client):
    worker = _register(client, "device-session-sync")
    with client.app.state.session_factory() as session:
        from app.models import EcommerceAccountRecord

        account = EcommerceAccountRecord(
            id="eac_session_sync",
            workspace_id="ws_test",
            created_by="usr_test",
            platform="taobao",
            account_key="seller-session-sync",
            profile_id="account-profile-eac_session_sync",
            profile_path="/profiles/eac_session_sync",
            status="active",
            login_state="unknown",
        )
        session.add(account)
        session.commit()
    response = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/accounts/eac_session_sync/session",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
        json={"login_state": "ready"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "account_id": "eac_session_sync",
        "login_state": "ready",
    }


def test_worker_claim_renews_lease_and_acknowledges_cancel(client):
    worker = _register(client, "device-claim")
    task_id = "crw_worker_claim"
    _queued_task(client, task_id)
    claimed = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/claim",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    task = claimed.json()["data"]
    assert task["id"] == task_id
    assert task["status"] == "running"
    fencing_token = task["lease"]["fencing_token"]

    renewed = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/{task_id}/lease",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
        json={"fencing_token": fencing_token},
    )
    assert renewed.status_code == 200

    cancelled = client.post(f"/api/ecommerce/crawl-tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    control = client.get(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/{task_id}/control",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
    )
    assert control.status_code == 200
    assert control.json()["data"]["cancel_requested"] is True

    done = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/{task_id}/complete",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
        json={"fencing_token": fencing_token, "outcome": "cancelled"},
    )
    assert done.status_code == 200
    assert done.json()["data"]["status"] == "cancelled"


def test_worker_timeout_requeues_only_when_attempts_remain(client):
    worker = _register(client, "device-timeout")
    task_id = "crw_worker_timeout"
    _queued_task(client, task_id, max_attempts=2)
    claimed = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/claim",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
        json={"platforms": ["taobao"]},
    )
    task = claimed.json()["data"]
    timed_out = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/{task_id}/complete",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
        json={
            "fencing_token": task["lease"]["fencing_token"],
            "outcome": "timed_out",
        },
    )
    assert timed_out.status_code == 200
    assert timed_out.json()["data"]["status"] == "queued"


def test_worker_result_upload_persists_snapshot_and_rejects_sensitive_fields(client):
    worker = _register(client, "device-result")
    task_id = "crw_worker_result"
    _queued_task(client, task_id)
    claimed = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/claim",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
        json={},
    )
    lease = claimed.json()["data"]["lease"]["fencing_token"]
    result = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/{task_id}/result",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
        json={
            "fencing_token": lease,
            "result": {
                "platform": "taobao",
                "numIid": "123456789012",
                "title": "本机代理商品结果",
                "price": "39.90",
                "imageUrls": [],
                "descImageUrls": [],
                "skuImageUrls": [],
            },
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["status"] == "succeeded"
    replay = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/tasks/{task_id}/result",
        headers={"Authorization": f"Bearer {worker['session_token']}"},
        json={
            "fencing_token": lease,
            "result": {"platform": "taobao", "title": "重复提交应幂等"},
        },
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["idempotent"] is True
    stored = client.get(f"/api/ecommerce/crawl-tasks/{task_id}/result")
    assert stored.status_code == 200
    assert stored.json()["data"]["result"]["title"] == "本机代理商品结果"

    second_worker = _register(client, "device-result-2")
    second_task_id = "crw_worker_result_sensitive"
    _queued_task(client, second_task_id)
    second_claim = client.post(
        f"/api/ecommerce/workers/{second_worker['worker']['id']}/tasks/claim",
        headers={"Authorization": f"Bearer {second_worker['session_token']}"},
        json={},
    )
    sensitive = client.post(
        f"/api/ecommerce/workers/{second_worker['worker']['id']}/tasks/{second_task_id}/result",
        headers={"Authorization": f"Bearer {second_worker['session_token']}"},
        json={
            "fencing_token": second_claim.json()["data"]["lease"]["fencing_token"],
            "result": {
                "platform": "taobao",
                "title": "不应接受",
                "platformData": {"cookies": "forbidden"},
            },
        },
    )
    assert sensitive.status_code == 422
