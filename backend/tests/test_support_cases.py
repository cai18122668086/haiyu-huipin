from uuid import uuid4

from sqlalchemy import select

from app.models import AuditEventRecord
from tests.test_admin_billing import enable_admin
from tests.test_customer_credit_billing import seed_job

BASE = "/api/v1/workspaces/ws_test/support-cases"
ADMIN = "/api/v1/admin/billing/support-cases"


def test_support_is_scoped_and_retries_do_not_duplicate(client):
    with client.app.state.session_factory() as session:
        job, _ = seed_job(session, count=1, deliverables=False)
        job_id = job.id
        session.commit()
    headers = {"Idempotency-Key": str(uuid4())}
    body = {"job_id": job_id, "issue": "测试任务需要核对图片与积分"}
    first = client.post(BASE, headers=headers, json=body)
    assert first.status_code == 201, first.text
    assert (
        client.post(BASE, headers=headers, json=body).json()["id"] == first.json()["id"]
    )
    assert len(client.get(BASE).json()) == 1
    assert client.get("/api/v1/workspaces/ws_other/support-cases").status_code == 404
    assert (
        client.post(
            BASE, headers=headers, json={**body, "issue": "改成其他问题"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            BASE,
            headers={"Idempotency-Key": str(uuid4())},
            json={**body, "job_id": "job_foreign"},
        ).status_code
        == 404
    )
    assert client.get(ADMIN).status_code == 403
    assert (
        client.patch(
            ADMIN + "/" + first.json()["id"],
            json={"status": "resolved", "resolution": "未授权"},
        ).status_code
        == 403
    )
    enable_admin(client)
    assert len(client.get(ADMIN).json()) == 1
    assert (
        client.patch(
            ADMIN + "/" + first.json()["id"],
            json={"status": "resolved", "resolution": ""},
        ).status_code
        == 422
    )
    result = client.patch(
        ADMIN + "/" + first.json()["id"],
        json={"status": "resolved", "resolution": "已核对原任务，问题处理完成"},
    )
    assert result.status_code == 200
    assert client.get(BASE).json()[0]["status"] == "resolved"
    with client.app.state.session_factory() as session:
        assert (
            session.scalar(
                select(AuditEventRecord).where(
                    AuditEventRecord.action == "support.updated"
                )
            )
            is not None
        )
