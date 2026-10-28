"""Unknown accepted images are reconciled, never cloned into empty retry jobs."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import JobItemRecord, JobRecord, ReplicaDocumentRecord, utc_now


@pytest.mark.parametrize("item_only", [False, True])
@pytest.mark.parametrize("generic", [False, True])
def test_unknown_replica_retry_does_not_create_a_replacement(
    client, item_only, generic
):
    jid, did = "job_" + uuid4().hex, "rep_" + uuid4().hex
    with client.app.state.session_factory() as db:
        db.add(
            JobRecord(
                id=jid,
                workspace_id="ws_test",
                type="reference_remix",
                configuration={"replica_session_id": did},
                status="failed",
                error_code=None if item_only else "PROVIDER_OUTCOME_UNKNOWN",
                finished_at=utc_now(),
            )
        )
        db.flush()
        if item_only:
            db.add(
                JobItemRecord(
                    id="item_" + uuid4().hex,
                    workspace_id="ws_test",
                    job_id=jid,
                    item_key="screen-1",
                    ordinal=0,
                    status="failed",
                    error_code="IMAGE_RESULT_DOWNLOAD_UNCONFIRMED",
                )
            )
        db.add(
            ReplicaDocumentRecord(
                id=did,
                workspace_id="ws_test",
                created_by="usr_test",
                kind="session",
                fingerprint=uuid4().hex * 2,
                status="failed",
                payload={"replication_method": "exact", "job_ids": [jid]},
            )
        )
        db.commit()
    for _ in range(2):
        response = client.post(
            f"/api/v1/workspaces/ws_test/jobs/{jid}/retry"
            if generic
            else f"/api/v1/workspaces/ws_test/replica/sessions/{did}/jobs/{jid}/retry",
            headers={"Idempotency-Key": str(uuid4())},
            json={"scope": "full"} if generic else {"request_id": str(uuid4())},
        )
        assert response.status_code == 409, response.text
        assert (
            response.json()["error"]["code"]
            == "REPLICA_OUTCOME_RECONCILIATION_REQUIRED"
        )
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(JobRecord)) == 1
        assert db.get(ReplicaDocumentRecord, did).payload == {
            "replication_method": "exact",
            "job_ids": [jid],
        }
