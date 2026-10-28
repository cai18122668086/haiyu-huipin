import base64
from io import BytesIO

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def admin_access(client):
    client.app.state.settings.announcement_admin_emails = ["admin@example.com"]


def _worker(client):
    enrollment = client.post(
        "/api/ecommerce/workers/enrollments",
        json={"workspace_id": "ws_test", "device_id": "remote-device"},
    ).json()["data"]
    return client.post(
        "/api/ecommerce/workers/register",
        json={
            "enrollment_token": enrollment["enrollment_token"],
            "device_id": "remote-device",
            "protocol_version": "worker.v1",
            "agent_version": "test",
            "capabilities": ["browser.taobao", "login.taobao"],
        },
    ).json()["data"]


def test_parallel_accounts_keep_login_and_crawl_mutually_exclusive(client, monkeypatch):
    import app.ecommerce_crawl_tasks as crawl_tasks

    monkeypatch.setattr(crawl_tasks, "_schedule", lambda *_args: None)
    worker = _worker(client)
    worker_id = worker["worker"]["id"]
    headers = {"Authorization": f"Bearer {worker['session_token']}"}
    accounts = []
    for number in range(2):
        response = client.post(
            "/api/ecommerce/accounts",
            json={
                "workspace_id": "ws_test",
                "platform": "taobao",
                "account_key": f"parallel-{number}",
                "worker_id": worker_id,
            },
        )
        assert response.status_code == 201
        account = response.json()["data"]
        client.patch(
            f"/api/ecommerce/accounts/{account['id']}", json={"login_state": "ready"}
        )
        accounts.append(account)

    def queue(account):
        response = client.post(
            "/api/ecommerce/crawl-tasks",
            json={
                "workspace_id": "ws_test",
                "platform": "taobao",
                "url": "https://item.taobao.com/item.htm?id=123456789012",
                "account_id": account["id"],
            },
        )
        assert response.status_code == 202, response.text
        return response.json()["data"]["id"]

    def claim():
        response = client.post(
            f"/api/ecommerce/workers/{worker_id}/tasks/claim", headers=headers, json={}
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    first_task = queue(accounts[0])
    login = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": accounts[0]["id"], "platform": "taobao"},
    )
    assert login.status_code == 200, login.text
    with client.app.state.session_factory() as session:
        selected, configured = crawl_tasks._select_automatic_account(
            session, workspace_id="ws_test", platform="taobao", lock_accounts=True
        )
        assert configured and selected.id == accounts[1]["id"]
    assert claim() is None
    client.post("/api/ecommerce/session/cancel", json={"account_id": accounts[0]["id"]})
    assert claim()["id"] == first_task
    assert (
        crawl_tasks._acquire_profile_lease(
            client.app.state.session_factory,
            profile_id=accounts[0]["profile_id"],
            platform="taobao",
            task_id=first_task,
            owner_id=worker_id,
            allow_reentry=False,
        )
        is None
    )
    blocked = client.post(
        "/api/ecommerce/session/start",
        json={"account_id": accounts[0]["id"], "platform": "taobao"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ACCOUNT_BUSY"
    queue(accounts[0])
    assert claim() is None
    second_task = queue(accounts[1])
    assert claim()["id"] == second_task


def test_remote_login_job_claim_frame_and_ready(client):
    worker = _worker(client)
    account_response = client.post(
        "/api/ecommerce/accounts",
        json={
            "workspace_id": "ws_test",
            "platform": "taobao",
            "account_key": "remote-seller",
            "worker_id": worker["worker"]["id"],
        },
    )
    assert account_response.status_code == 201, account_response.text
    account = account_response.json()["data"]
    created = client.post(
        "/api/ecommerce/login-jobs",
        json={"account_id": account["id"], "worker_id": worker["worker"]["id"]},
    )
    assert created.status_code == 201, created.text
    job = created.json()["data"]["login_job"]
    assert job["state"] == "queued"

    headers = {"Authorization": f"Bearer {worker['session_token']}"}
    claimed = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/login-jobs/claim",
        headers=headers,
        json={"platforms": ["taobao"]},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["data"]["id"] == job["id"]

    output = BytesIO()
    Image.new("RGB", (1, 1), "white").save(output, format="JPEG")
    frame = base64.b64encode(output.getvalue()).decode("ascii")
    uploaded = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/login-jobs/{job['id']}/frames",
        headers=headers,
        json={"data": frame, "content_type": "image/jpeg", "sequence": 1},
    )
    assert uploaded.status_code == 200, uploaded.text
    preview = client.get(f"/api/ecommerce/session/preview?session_id={job['id']}")
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"

    ready = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/login-jobs/{job['id']}/state",
        headers=headers,
        json={"state": "ready"},
    )
    assert ready.status_code == 200, ready.text
    status = client.get(f"/api/ecommerce/login-jobs/{job['id']}")
    assert status.status_code == 200, status.text
    assert status.json()["data"]["state"] == "ready"


def test_remote_login_input_is_queued_for_the_owning_worker(client):
    worker = _worker(client)
    account = client.post(
        "/api/ecommerce/accounts",
        json={
            "workspace_id": "ws_test",
            "platform": "taobao",
            "account_key": "remote-input",
            "worker_id": worker["worker"]["id"],
        },
    ).json()["data"]
    job = client.post(
        "/api/ecommerce/login-jobs",
        json={"account_id": account["id"], "worker_id": worker["worker"]["id"]},
    ).json()["data"]["login_job"]
    headers = {"Authorization": f"Bearer {worker['session_token']}"}
    claimed = client.post(
        f"/api/ecommerce/workers/{worker['worker']['id']}/login-jobs/claim",
        headers=headers,
        json={"platforms": ["taobao"]},
    )
    assert claimed.status_code == 200

    input_response = client.post(
        "/api/ecommerce/session/input",
        json={
            "account_id": account["id"],
            "session_id": job["id"],
            "type": "text",
            "text": "example@example.com",
        },
    )
    assert input_response.status_code == 200, input_response.text
    control = client.get(
        f"/api/ecommerce/workers/{worker['worker']['id']}/login-jobs/{job['id']}/control",
        headers=headers,
    )
    assert control.status_code == 200
    assert control.json()["data"]["actions"] == [
        {"type": "text", "text": "example@example.com", "button": "left"}
    ]


def test_remote_login_rejects_bad_frame_and_offline_start(client):
    worker = _worker(client)
    account_response = client.post(
        "/api/ecommerce/accounts",
        json={
            "workspace_id": "ws_test",
            "platform": "taobao",
            "account_key": "offline-seller",
            "worker_id": worker["worker"]["id"],
        },
    )
    assert account_response.status_code == 201, account_response.text
    account = account_response.json()["data"]
    with client.app.state.session_factory() as session:
        from app.models import EcommerceWorkerRecord

        record = session.get(EcommerceWorkerRecord, worker["worker"]["id"])
        record.status = "offline"
        session.commit()
    response = client.post(
        "/api/ecommerce/login-jobs",
        json={"account_id": account["id"], "worker_id": worker["worker"]["id"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOCAL_WORKER_OFFLINE"
