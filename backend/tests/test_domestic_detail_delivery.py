"""C6 requirements: native finalization must retain accepted truth and evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
from app.models import AssetRecord, JobAssetRecord
from app.xinghe_agent import XingheImageProvider
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_atomic_review import visible_failure
from tests.test_domestic_detail_execution import _aurora_approval_payload, _gateway
from tests.test_domestic_detail_plans import planned_job

ORIGINAL_REVIEW = XingheImageProvider._native_detail_judgment_with_review

REVIEWS = {}

APPROVAL = "我已逐屏审阅本次合成任务的最终结果，并接受明确列出的残差。"


@pytest.fixture(autouse=True)
def explicit_synthetic_delivery(client, monkeypatch):
    # The default mock shortcut does not persist merchant pair canvases.
    # Explicit synthetic mode exercises real local output storage and C4 lineage.
    REVIEWS.clear()
    monkeypatch.setattr(_gateway(client), "provider_name", "synthetic")

    async def synthetic_observer(self, request, native_request, sources, artifact):
        return self._native_detail_judgment(native_request, artifact)

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", synthetic_observer
    )


def ready(client, *, prepare=True):
    planned = planned_job(client)
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}"
    ).json()
    assert current["status"] == "awaiting_final_review", current.get("error_message")
    if prepare:
        packet = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{current['id']}/detail-final-review"
        )
        assert packet.status_code == 200, packet.text
        REVIEWS[current["id"]] = packet.json()["sha256"]
    return current


def finalize(client, job_id, key=None):
    if job_id not in REVIEWS:
        packet = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}/detail-final-review"
        )
        assert packet.status_code == 200, packet.text
        REVIEWS[job_id] = packet.json()["sha256"]
    return client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}/finalize",
        headers=key or idem(),
        json={"approval_text": APPROVAL, "final_review_sha256": REVIEWS[job_id]},
    )


def native_state(job_id):
    return json.loads((detail_workspace(job_id) / "session.json").read_bytes())


def test_final_review_uses_native_version_and_finalizes_the_accepted_session(client):
    job = ready(client)
    gateway = _gateway(client)
    before = (len(gateway.planner_calls), len(gateway.image_calls))
    response = finalize(client, job["id"])
    assert response.status_code == 200, response.text
    state = native_state(job["id"])
    assert state["state"] == "FINALIZED"
    native = json.loads(Path(state["final"]["manifest"]).read_bytes())
    version = (AuroraDetailHarnessRunner().skill_root / "VERSION").read_text().strip()
    assert native["version"] == version
    assert native["approval"]["text"] == APPROVAL
    assert len(native["modules"]) == 8 and all(
        m["audit_binding"] for m in native["modules"]
    )
    public = response.json()["plan"]["payload"]["final_manifest"]
    assert public["version"] == native["version"]
    assert public["residual_issues"] == native["residual_issues"] == []
    assert not native.get("long_page"), "The optional preview must not be mandatory"
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before


def test_material_residuals_survive_final_delivery_without_extra_render(
    client, monkeypatch
):
    async def known_failure(self, request, native_request, sources, artifact):
        raw = self._native_detail_judgment(native_request, artifact)
        if native_request["pair_id"] == "P01":
            visible_failure(raw["module_judgments"][0], "geometry")
            raw.update(status="MATERIAL_FAIL", material_failures=[])
        return raw

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", known_failure
    )
    job = ready(client)
    state = native_state(job["id"])
    expected = [
        {"module_id": row["id"], "issues": row["residual_issues"]}
        for row in state["modules"]
        if row["residual_issues"]
    ]
    assert expected and state["modules"][0]["status"] == "COMPLETE_WITH_RESIDUALS"
    gateway = _gateway(client)
    assert len(gateway.image_calls) == 5
    response = finalize(client, job["id"])
    assert response.status_code == 200, response.text
    public = response.json()["plan"]["payload"]["final_manifest"]
    assert public.get("residual_issues") == expected
    assert native_state(job["id"])["state"] == "FINALIZED"
    assert len(gateway.image_calls) == 5


@pytest.mark.parametrize("tamper", ["original_review", "delivered_bytes"])
def test_final_review_rejects_changed_original_evidence_without_resend(
    client, monkeypatch, tamper
):
    job = ready(client)
    state = native_state(job["id"])
    if tamper == "original_review":
        snapshot = state["modules"][0]["attempts"][-1]["review_evidence"][
            "snapshot_file"
        ]
        Path(snapshot).unlink()
    else:
        with client.app.state.session_factory() as db:
            key = db.scalar(
                select(AssetRecord.object_key)
                .join(JobAssetRecord, JobAssetRecord.asset_id == AssetRecord.id)
                .where(
                    JobAssetRecord.job_id == job["id"],
                    JobAssetRecord.direction == "output",
                    JobAssetRecord.is_deliverable.is_(True),
                )
                .order_by(JobAssetRecord.ordinal)
            )
        assert key
        original = client.app.state.storage.read_bytes

        async def changed(object_key):
            payload = await original(object_key)
            return (
                payload + b"changed synthetic delivery"
                if object_key == key
                else payload
            )

        monkeypatch.setattr(client.app.state.storage, "read_bytes", changed)
    gateway = _gateway(client)
    before = (len(gateway.planner_calls), len(gateway.image_calls))
    response = finalize(client, job["id"])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"].startswith("NATIVE_DETAIL_")
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert current["status"] == "awaiting_final_review"
    assert native_state(job["id"])["state"] != "FINALIZED"
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before


def test_same_final_review_key_replays_without_a_second_finalize_or_provider_call(
    client,
):
    job = ready(client)
    key = idem()
    first = finalize(client, job["id"], key)
    assert first.status_code == 200, first.text
    gateway = _gateway(client)
    before = (len(gateway.planner_calls), len(gateway.image_calls))
    second = finalize(client, job["id"], key)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "completed"
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before


def count_native_finalizes(monkeypatch):
    calls = []
    original = AuroraDetailHarnessRunner.run

    def counted(self, command, workspace, arguments):
        if command == "finalize":
            calls.append(dict(arguments))
        return original(self, command, workspace, arguments)

    monkeypatch.setattr(AuroraDetailHarnessRunner, "run", counted)
    return calls


def test_final_delivery_recovers_lost_database_projection_without_native_repeat(
    client, monkeypatch
):
    from sqlalchemy.orm import Session

    job = ready(client)
    key = idem()
    calls = count_native_finalizes(monkeypatch)
    original_commit = Session.commit
    interrupted = []

    def fail_after_native(self):
        if native_state(job["id"])["state"] == "FINALIZED" and not interrupted:
            interrupted.append(True)
            raise OSError("synthetic database projection interruption")
        return original_commit(self)

    monkeypatch.setattr(Session, "commit", fail_after_native)
    with pytest.raises(OSError, match="synthetic database projection"):
        finalize(client, job["id"], key)
    monkeypatch.setattr(Session, "commit", original_commit)
    state = native_state(job["id"])
    path = Path(state["final"]["manifest"])
    original_file = path.read_bytes()
    original_session = (detail_workspace(job["id"]) / "session.json").read_bytes()
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert current["status"] == "awaiting_final_review" and len(calls) == 1
    before = (len(_gateway(client).planner_calls), len(_gateway(client).image_calls))
    replay = finalize(client, job["id"], key)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "completed" and len(calls) == 1
    assert path.read_bytes() == original_file
    assert (
        detail_workspace(job["id"]) / "session.json"
    ).read_bytes() == original_session
    assert (
        len(_gateway(client).planner_calls),
        len(_gateway(client).image_calls),
    ) == before


def test_final_delivery_adopts_native_result_after_completion_write_interruption(
    client, monkeypatch
):
    from app.domestic_detail import delivery

    job = ready(client)
    key = idem()
    calls = count_native_finalizes(monkeypatch)
    original_once = delivery._once

    def lose_completion(path, value):
        if path.name == "response.json":
            raise OSError("synthetic final completion write interruption")
        return original_once(path, value)

    monkeypatch.setattr(delivery, "_once", lose_completion)
    first = finalize(client, job["id"], key)
    assert first.status_code == 409
    assert native_state(job["id"])["state"] == "FINALIZED" and len(calls) == 1
    monkeypatch.setattr(delivery, "_once", original_once)
    replay = finalize(client, job["id"], key)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "completed" and len(calls) == 1


def test_completed_delivery_replay_rechecks_native_manifest_and_key_body(
    client, monkeypatch
):
    job = ready(client)
    key = idem()
    calls = count_native_finalizes(monkeypatch)
    first = finalize(client, job["id"], key)
    assert first.status_code == 200, first.text
    conflict = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/finalize",
        headers=key,
        json={"approval_text": "A different final approval is a different intent"},
    )
    assert conflict.status_code == 409 and len(calls) == 1
    path = Path(native_state(job["id"])["final"]["manifest"])
    path.write_bytes(path.read_bytes() + b"changed original final manifest")
    replay = finalize(client, job["id"], key)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"].startswith("NATIVE_DETAIL_")
    assert len(calls) == 1 and len(_gateway(client).image_calls) == 4


@pytest.mark.parametrize("missing_message", [False, True])
def test_final_review_requires_every_cited_original_model_message(
    client, monkeypatch, missing_message
):
    from app.xinghe_agent import (
        PlannerCallResult,
        _artifact_from_bytes,
        normalize_usage,
    )

    gateway = _gateway(client)
    old_plan = gateway.plan
    sampler = XingheImageProvider(
        client.app.state.session_factory,
        gateway,
        storage=client.app.state.storage,
        skill_root=AuroraDetailHarnessRunner().skill_root,
        max_concurrency=1,
        skill_id="aurora-domestic-ecommerce-detail-page",
    )

    async def raw_review(request):
        if request.planning_profile != "aurora_detail_pair_audit":
            return await old_plan(request)
        context = json.loads(request.product_information[0])
        candidate = request.sources[-1]
        artifact = _artifact_from_bytes(
            candidate.payload,
            filename=candidate.filename,
            expected_size="1440x3840",
            preserve_provider_artifact=True,
        )
        raw = sampler._native_detail_judgment(context, artifact)
        return PlannerCallResult(
            payload=raw,
            provider_request_id="synthetic-original-review",
            raw_usage={"total_tokens": 11},
            normalized_usage=normalize_usage({"total_tokens": 11}),
            api_latency_seconds=0.01,
            actual_cost_micros=1,
            audit_raw_content=json.dumps(raw, ensure_ascii=False),
        )

    monkeypatch.setattr(gateway, "plan", raw_review)
    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", ORIGINAL_REVIEW
    )
    job = ready(client)
    root = detail_workspace(job["id"])
    messages = list((root / "reviews").glob("*.reply.txt"))
    assert len(messages) == 4
    if missing_message:
        messages[0].unlink()
    before = (len(gateway.planner_calls), len(gateway.image_calls))
    response = finalize(client, job["id"])
    if missing_message:
        assert response.status_code == 409, response.text
        assert (
            response.json()["error"]["code"]
            == "NATIVE_DETAIL_REVIEW_PROVENANCE_INVALID"
        )
        assert native_state(job["id"])["state"] == "AWAITING_USER_FINAL_REVIEW"
    else:
        assert response.status_code == 200, response.text
        assert native_state(job["id"])["state"] == "FINALIZED"
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before


def test_selected_slice_recovery_finalizes_its_own_native_delivery_without_resend(
    client, monkeypatch
):
    from app.models import JobItemRecord, JobRecord, utc_now

    source = ready(client)
    original_root = detail_workspace(source["id"])
    original_session = (original_root / "session.json").read_bytes()
    gateway = _gateway(client)
    before = (len(gateway.planner_calls), len(gateway.image_calls))
    second_id = source["items"][1]["id"]
    with client.app.state.session_factory() as db:
        job = db.get(JobRecord, source["id"])
        job.status, job.stage = "partial", "partial"
        job.succeeded_item_count, job.failed_item_count = 7, 1
        job.finished_at = utc_now()
        item = db.get(JobItemRecord, second_id)
        item.status, item.error_code = "failed", "OUTPUT_STORAGE_FAILED"
        db.commit()
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source['id']}/retry",
        headers=idem(),
        json={"scope": "selected", "item_ids": [second_id]},
    )
    assert response.status_code == 201, response.text
    target = response.json()["id"]
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target}").json()
    assert current["status"] == "awaiting_final_review", current.get("error_message")
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before
    calls = count_native_finalizes(monkeypatch)
    completed = finalize(client, target)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert native_state(target)["state"] == "FINALIZED" and len(calls) == 1
    assert (original_root / "session.json").read_bytes() == original_session
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before


@pytest.mark.parametrize("field", ["storage_bucket", "content_type"])
def test_final_review_rejects_durable_metadata_that_does_not_describe_the_bytes(
    client, field
):
    job = ready(client)
    with client.app.state.session_factory() as db:
        asset = db.scalar(
            select(AssetRecord)
            .join(JobAssetRecord, JobAssetRecord.asset_id == AssetRecord.id)
            .where(
                JobAssetRecord.job_id == job["id"], JobAssetRecord.direction == "output"
            )
        )
        setattr(
            asset,
            field,
            "wrong-synthetic-bucket" if field == "storage_bucket" else "text/plain",
        )
        db.commit()
    gateway = _gateway(client)
    before = (len(gateway.planner_calls), len(gateway.image_calls))
    response = finalize(client, job["id"])
    assert response.status_code == 409, response.text
    assert native_state(job["id"])["state"] == "AWAITING_USER_FINAL_REVIEW"
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before
