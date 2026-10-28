"""Explicit report choices, immutable final packets and source-scoped history."""

import copy
import hashlib
import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.aurora_detail_runtime import detail_workspace
from app.domain import JobType
from app.models import JobPlanRecord, JobRecord, WorkspaceRecord, utc_now
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_campaign_jobs import (
    request_review,
    synthetic_campaign_observation,
)
from tests.test_domestic_detail_delivery import (
    explicit_synthetic_delivery,  # noqa: F401
    ready,
)
from tests.test_domestic_detail_execution import _gateway


def test_review_history_is_paginated_and_bound_to_source_and_workspace(client):
    assert client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs").status_code == 200
    with client.app.state.session_factory() as db:
        db.add(
            WorkspaceRecord(
                id="ws_other",
                name="Other synthetic workspace",
                created_by_user_id="usr_test",
            )
        )
        db.flush()
        for name in ("job_source_a", "job_source_b"):
            db.add(
                JobRecord(
                    id=name,
                    workspace_id=WORKSPACE_ID,
                    type=JobType.DETAIL_PACK.value,
                    configuration={
                        "domestic_detail_pipeline": "aurora-domestic-detail-v4.3.1"
                    },
                )
            )
        stamp = utc_now()
        for i, (workspace, source) in enumerate(
            [(WORKSPACE_ID, "job_source_a")] * 3
            + [(WORKSPACE_ID, "job_source_b"), ("ws_other", "job_source_a")]
        ):
            db.add(
                JobRecord(
                    id=f"job_review_{i}",
                    workspace_id=workspace,
                    type="domestic_detail_review",
                    status="queued",
                    configuration={"source_job_id": source},
                    created_at=stamp,
                )
            )
        db.commit()
    url = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/job_source_a/campaign-reviews"
    first = client.get(url, params={"limit": 2})
    assert first.status_code == 200, first.text
    assert [r["id"] for r in first.json()["items"]] == ["job_review_2", "job_review_1"]
    assert first.json()["has_more"] is True
    cursor = first.json()["next_cursor"]
    second = client.get(url, params={"limit": 2, "cursor": cursor})
    assert [r["id"] for r in second.json()["items"]] == [
        "job_review_0"
    ] and second.json()["has_more"] is False
    other = client.get(
        url.replace("job_source_a", "job_source_b"), params={"cursor": cursor}
    )
    assert other.status_code == 422, other.text
    assert client.get(url.replace("job_source_a", "job_missing")).status_code == 404


@pytest.mark.parametrize("boilerplate", [False, True])
def test_selected_snapshot_keeps_accepted_delivery_immutable(
    client, monkeypatch, boilerplate
):
    from app.xinghe_agent import PlannerCallResult, normalize_usage

    source = ready(client)
    gateway = _gateway(client)
    original_plan = gateway.plan

    async def observe(request):
        if request.planning_profile != "aurora_detail_campaign_audit":
            return await original_plan(request)
        gateway.planner_calls.append(request)
        value = synthetic_campaign_observation(
            json.loads(request.product_information[0]), boilerplate=boilerplate
        )
        return PlannerCallResult(
            payload=value,
            provider_request_id="synthetic-selection",
            raw_usage={"input_tokens": 9, "output_tokens": 8},
            normalized_usage=normalize_usage({"input_tokens": 9, "output_tokens": 8}),
            api_latency_seconds=0.01,
            actual_cost_micros=17,
            audit_raw_content=json.dumps(value, ensure_ascii=False),
        )

    monkeypatch.setattr(gateway, "plan", observe)
    review = request_review(client, source).json()
    dispatch_pending(client)
    prefix = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source['id']}"
    report = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review['id']}/campaign-review/report"
    ).json()
    base = client.post(prefix + "/detail-final-review").json()
    original = (detail_workspace(source["id"]) / "session.json").read_bytes()
    selected = {
        "review_job_id": review["id"],
        "report_sha256": report["report"]["sha256"],
    }
    assert (
        client.post(
            prefix + "/detail-final-review",
            json={**selected, "report_sha256": "a" * 64},
        ).status_code
        == 409
    )
    with client.app.state.session_factory() as db:
        job = db.get(JobRecord, review["id"])
        config = dict(job.configuration)
        job.configuration = {**config, "source_job_id": "job_other"}
        db.commit()
    assert (
        client.post(prefix + "/detail-final-review", json=selected).status_code == 409
    )
    with client.app.state.session_factory() as db:
        db.get(JobRecord, review["id"]).configuration = config
        db.commit()
    response = client.post(prefix + "/detail-final-review", json=selected)
    assert response.status_code == 200, response.text
    packet = response.json()
    supplement = packet["supplementary_campaign_review"]
    assert (
        packet["sha256"] != base["sha256"] and supplement["report"] == report["report"]
    )
    assert supplement["report"]["native_gate_accepted"] is (not boilerplate)
    if boilerplate:
        assert packet["quality_state"] == "WITH_RESIDUALS"
    assert (
        packet["campaign_audit_status"] == "NOT_REQUESTED"
        and "独立整页审核补充记录" in packet["document_markdown"]
    )
    assert client.post(prefix + "/detail-final-review").json() == base
    assert client.post(prefix + "/detail-final-review", json=selected).json() == packet
    assert (detail_workspace(source["id"]) / "session.json").read_bytes() == original
    counts = (len(gateway.planner_calls), len(gateway.image_calls))
    rawpath = next((detail_workspace(review["id"]) / "reviews").glob("*.reply.txt"))
    raw = rawpath.read_bytes()
    rawpath.write_bytes(b"changed original reply")
    body = {
        "approval_text": "我已审阅全部结果与明确选择的独立报告，并接受所列残差。",
        "final_review_sha256": packet["sha256"],
    }
    assert (
        client.post(prefix + "/finalize", headers=idem(), json=body).status_code == 409
    )
    rawpath.write_bytes(raw)
    completed = client.post(prefix + "/finalize", headers=idem(), json=body)
    assert completed.status_code == 200, completed.text
    if boilerplate:
        assert (
            completed.json()["plan"]["payload"]["final_manifest"]["quality_state"]
            == "WITH_RESIDUALS"
        )
    assert (
        client.post(prefix + "/detail-final-review", json=selected).status_code == 409
    )
    # Another valid earlier packet cannot replace the accepted SQL pointer.
    with client.app.state.session_factory() as db:
        plan = db.get(JobPlanRecord, source["plan"]["id"])
        original_payload = copy.deepcopy(plan.payload)
        altered = copy.deepcopy(original_payload)
        altered["final_manifest"]["final_review"]["sha256"] = base["sha256"]
        plan.payload = altered
        db.commit()
    assert client.post(prefix + "/detail-final-review").status_code == 409
    with client.app.state.session_factory() as db:
        db.get(JobPlanRecord, source["plan"]["id"]).payload = original_payload
        db.commit()
    # Completed delivery reads its own accepted evidence snapshot.
    rawpath.write_bytes(b"origin unavailable after accepted snapshot")
    assert client.post(prefix + "/detail-final-review").json() == packet
    archive = client.get(prefix + "/deliverables.zip")
    assert archive.status_code == 200, archive.text
    with ZipFile(BytesIO(archive.content)) as z:
        assert json.loads(z.read("final-review.json")) == packet
        assert z.read("supplementary-review/original-message.txt") == raw
        for name, proof in supplement["files"].items():
            assert (
                hashlib.sha256(z.read("supplementary-review/" + name)).hexdigest()
                == proof["sha256"]
            )
        native = json.loads(z.read("native-final-manifest.json"))
        assert "supplementary_campaign_review" not in native
    frozen = (
        detail_workspace(source["id"])
        / "final-review"
        / packet["sha256"]
        / "supplementary-review"
        / "original-message.txt"
    )
    saved = frozen.read_bytes()
    frozen.write_bytes(b"changed accepted snapshot")
    assert client.post(prefix + "/detail-final-review").status_code == 409
    frozen.write_bytes(saved)
    assert client.post(prefix + "/detail-final-review").json() == packet
    assert counts == (len(gateway.planner_calls), len(gateway.image_calls))


def test_long_owned_supplement_paths_publish_original_bytes(tmp_path):
    from app.domestic_detail.campaign_selection import _read, preserve_selection

    directory = tmp_path / ("h" * 64) / ("s" * 64)
    files = {"reviews/" + "r" * 43 + ".reply.txt": "原始回复\n".encode()}
    preserve_selection(directory, files)
    for name, data in files.items():
        assert _read(directory / "supplementary-review", name) == data
    preserve_selection(directory, files)
