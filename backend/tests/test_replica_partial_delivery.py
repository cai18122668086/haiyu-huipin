"""Public partial downloads, durable retries, and retained upstream receipts."""

import asyncio
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from app.models import JobAssetRecord, JobItemRecord, ReplicaDocumentRecord
from app.provider import ProviderOutcome
from app.replica_exact_compact import CompactExactReplicaRun
from app.task_runner import MockTaskRunner
from tests.test_api import dispatch_pending
from tests.test_replica_compact import SyntheticGateway


def create_case(
    client, monkeypatch, tmp_path, *, fallback=True, screens=2, gateway_override=None
):
    gateway = gateway_override or SyntheticGateway()
    gateway.reject_always = fallback
    if screens != 2:
        original_plan = gateway.plan

        async def expanded_plan(request):
            import json

            envelope = json.loads(request.product_information[0])
            response = await original_plan(request)
            if envelope["stage"] in {"screen_split", "screen_inventory"}:
                for column in response.payload["columns"]:
                    column["screens"] = [
                        deepcopy(column["screens"][0]) for _ in range(screens)
                    ]
                    column["boundary_ys"] = [
                        round(640 * i / screens) for i in range(1, screens)
                    ]
            return response

        gateway.plan = expanded_plan
    client.app.state.settings.reference_remix_provider_enabled = True
    client.app.state.settings.replica_exact_compact_workspaces = ("ws_test",)
    client.app.state.replica_gateway = gateway
    client.app.state.task_runner = MockTaskRunner(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        reference_remix_provider_enabled=True,
        aurora_work_root=tmp_path / "worker",
    )
    init = CompactExactReplicaRun.__init__

    def isolated(self, *args, **kwargs):
        kwargs.setdefault("root", tmp_path / "native")
        init(self, *args, **kwargs)

    monkeypatch.setattr(CompactExactReplicaRun, "__init__", isolated)
    ids = []
    for size in [(64, 96), (256, 640)]:
        data = BytesIO()
        Image.new("RGB", size, "blue").save(data, "PNG")
        response = client.post(
            "/api/v1/workspaces/ws_test/assets",
            headers={"Idempotency-Key": uuid4().hex},
            files={"file": ("input.png", data.getvalue(), "image/png")},
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    route = "/api/v1/workspaces/ws_test/replica"
    response = client.post(
        route + "/sessions",
        json={
            "request_id": uuid4().hex,
            "replication_method": "exact",
            "product_asset_ids": ids[:1],
            "references": [{"asset_id": ids[1]}],
            "facts": "Supported synthetic product",
            "auto_start_after_plan": True,
        },
    )
    assert response.status_code == 202, response.text
    did = response.json()["id"]
    doc = client.get(route + "/documents/" + did).json()
    return gateway, route, did, (doc["payload"].get("job_ids") or [None])[0]


@pytest.mark.parametrize("fallback", [True, False])
@pytest.mark.parametrize("generic_retry", [True, False])
def test_first_image_downloads_before_failure_and_retry_only_generates_missing(
    client,
    monkeypatch,
    tmp_path,
    fallback,
    generic_retry,
):
    gateway, route, did, jid = create_case(
        client, monkeypatch, tmp_path, fallback=fallback
    )
    original_edit = gateway.edit
    failed = False

    async def edit(request):
        nonlocal failed
        if len(gateway.image_requests) == 1 and not failed:
            failed = True
            gateway.image_requests.append(request)
            # Screen 1 must be publicly linked BEFORE screen 2 is dispatched.
            with client.app.state.session_factory() as db:
                assert (
                    len(
                        db.scalars(
                            select(JobAssetRecord).where(
                                JobAssetRecord.job_id == jid,
                                JobAssetRecord.direction == "output",
                                JobAssetRecord.is_deliverable.is_(True),
                            )
                        ).all()
                    )
                    == 1
                )
            return SimpleNamespace(
                outcome=ProviderOutcome.FAILED,
                artifact=None,
                error_code="IMAGE_ASYNC_TASK_FAILED",
                error_message="上游图片任务失败",
                http_status=200,
                provider_request_id="original-failed-task",
                normalized_usage={"reported": False},
                actual_cost_micros=None,
                estimated_cost_micros=90000,
                raw_usage={
                    "metrics": {
                        "provider_error": {
                            "code": "supplier_request_failed",
                            "message": "Image result unavailable",
                        },
                    }
                },
            )
        return await original_edit(request)

    gateway.edit = edit
    dispatch_pending(client)
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert job["status"] == "partial", job
    assert job["error_code"] == "IMAGE_ASYNC_TASK_FAILED"
    assert job["error_details"]["provider_request_id"] == "original-failed-task"
    assert job["error_details"]["provider_error"]["code"] == "supplier_request_failed"
    outputs = [a for a in job["assets"] if a["direction"] == "output"]
    assert len(outputs) == 1 and outputs[0]["ordinal"] == 0
    aid = outputs[0]["asset_id"]
    access = client.post(
        f"/api/v1/workspaces/ws_test/assets/{aid}/access-url",
        json={"disposition": "attachment", "variant": "original"},
    )
    assert access.status_code == 200, access.text
    image = client.get(access.json()["url"])
    assert image.status_code == 200
    with client.app.state.session_factory() as db:
        before = deepcopy(
            db.get(ReplicaDocumentRecord, did).payload["native_executions"][jid]
        )
    # Public recovery must be idempotent and never invoke the image provider.
    for _ in range(2):
        recovered = client.post(route + f"/sessions/{did}/jobs/{jid}/recover")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["recovered"] == 0
    if generic_retry:
        retry = client.post(
            f"/api/v1/workspaces/ws_test/jobs/{jid}/retry",
            headers={"Idempotency-Key": uuid4().hex},
            json={"scope": "full"},
        )
        assert retry.status_code == 201, retry.text
        new_id = retry.json()["id"]
    else:
        retry = client.post(
            route + f"/sessions/{did}/jobs/{jid}/retry",
            json={"request_id": uuid4().hex},
        )
        assert retry.status_code == 202, retry.text
        new_id = retry.json()["payload"]["job_ids"][0]
    queued = client.get(f"/api/v1/workspaces/ws_test/jobs/{new_id}").json()
    assert queued["retry_scope"] == "failed"
    assert queued["items"][0]["status"] == "reused"
    assert [a["asset_id"] for a in queued["assets"] if a["direction"] == "output"] == [
        aid
    ]
    dispatch_pending(client)
    final = client.get(f"/api/v1/workspaces/ws_test/jobs/{new_id}").json()
    assert final["status"] == "completed", (
        final["error_code"],
        final["error_message"],
        final["error_details"],
    )
    assert len(gateway.image_requests) == 3  # 1 success + 1 failure + only 1 retry.
    assert [
        a["asset_id"]
        for a in final["assets"]
        if a["direction"] == "output" and a["ordinal"] == 0
    ] == [aid]
    assert client.get(access.json()["url"]).content == image.content
    with client.app.state.session_factory() as db:
        assert (
            db.get(ReplicaDocumentRecord, did).payload["native_executions"][jid]
            == before
        )


def test_historical_group_failure_recovers_five_and_only_retries_last_three(
    client, monkeypatch, tmp_path
):
    from app.models import JobRecord
    from app.replica_native import read_json, write_json

    client.app.state.storage.validator.max_image_pixels = 40_000_000
    gateway, route, did, jid = create_case(client, monkeypatch, tmp_path, screens=8)
    with client.app.state.session_factory() as db:
        row = db.get(ReplicaDocumentRecord, did)
        values = deepcopy(row.payload)
        snapshot = values["native_snapshot"]
    # Construct the old release's five generated files, frozen in its own
    # snapshot, with the sixth-screen provider failure incorrectly on item 0.
    from app.replica_completion import PLAN, STATE, CompletionRun

    run = CompletionRun(
        "exact",
        did,
        "ws_test",
        gateway,
        client.app.state.storage,
        root=Path(snapshot["root"]),
    )
    run.source_metadata = snapshot
    run.binding = snapshot.get("model_protocol")
    asyncio.run(run.restore(snapshot))
    plan, state = read_json(run.root / PLAN), run.state()
    assert len(state["modules"]) == 8
    for index in range(5):
        output = run.root / "artwork" / f"historic-{index}.png"
        output.parent.mkdir(exist_ok=True)
        Image.new("RGB", tuple(plan["units"][index]["canvas"]), "blue").save(output)
        from app.replica_native import digest

        state["modules"][index].update(
            status="GENERATED", artwork=str(output), sha256=digest(output.read_bytes())
        )
    state["modules"][5]["status"] = "AWAITING_ARTWORK"
    state["module_index"] = 5
    write_json(run.root / STATE, state)
    run.usage.append(
        {
            "kind": "image",
            "network_requests": 1,
            "error_code": "IMAGE_ASYNC_TASK_FAILED",
            "provider_request_id": "original-screen-6",
            "metrics": {
                "provider_error": {
                    "code": "supplier_request_failed",
                    "message": "Original failure",
                }
            },
        }
    )
    frozen = asyncio.run(run.snapshot())
    with client.app.state.session_factory() as db:
        row = db.get(ReplicaDocumentRecord, did)
        row.payload = {
            **values,
            "native_executions": {
                jid: {"snapshot": frozen, "delivery": [], "state": "AWAITING_ARTWORK"}
            },
        }
        job = db.get(JobRecord, jid)
        from app.models import utc_now

        job.status, job.finished_at = "failed", utc_now()
        for item in db.scalars(
            select(JobItemRecord).where(JobItemRecord.job_id == jid)
        ):
            item.status = "failed" if item.ordinal == 0 else "skipped"
            item.error_code = "REPLICA_SKILL_BLOCKED"
        db.commit()
    for expected in [5, 0]:
        recovered = client.post(route + f"/sessions/{did}/jobs/{jid}/recover")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["recovered"] == expected
    recovered = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert recovered["succeeded_item_count"] == 5
    assert recovered["failed_item_count"] == 1
    assert (
        recovered["items"][5]["error_details"]["provider_request_id"]
        == "original-screen-6"
    )
    assert len([a for a in recovered["assets"] if a["direction"] == "output"]) == 5
    retry = client.post(
        route + f"/sessions/{did}/jobs/{jid}/retry", json={"request_id": uuid4().hex}
    )
    assert retry.status_code == 202, retry.text
    new_id = retry.json()["payload"]["job_ids"][0]

    # Match the original tiny synthetic per-screen canvases; count only new calls.
    async def render(request):
        from tests.test_replica_resolution import result

        gateway.image_requests.append(request)
        return result(tuple(int(v) for v in request.size.split("x")))

    gateway.edit = render
    dispatch_pending(client)
    final = client.get(f"/api/v1/workspaces/ws_test/jobs/{new_id}").json()
    assert final["status"] == "completed", (final["error_code"], final["error_message"])
    assert len(gateway.image_requests) == 3
    assert [i["status"] for i in final["items"][:5]] == ["reused"] * 5
    assert len([a for a in final["assets"] if a["direction"] == "output"]) == 8
