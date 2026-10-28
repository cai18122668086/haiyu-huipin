"""Inspect the actual edit transport and compiled local scope, not prompt labels."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
from app.domestic_detail.plans import canonical_hash
from app.xinghe_agent import XingheImageProvider
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_atomic_review import visible_failure
from tests.test_domestic_detail_delivery import (  # noqa: F401
    explicit_synthetic_delivery,
    ready,
)
from tests.test_domestic_detail_execution import _aurora_approval_payload, _gateway
from tests.test_domestic_detail_plans import planned_job
from tests.test_native_detail_local_adjustment import adjustment, inventory


def fail_first_pair(monkeypatch, residual=False):
    attempts = {}

    async def observe(self, request, native_request, sources, artifact):
        value = self._native_detail_judgment(native_request, artifact)
        pair = native_request["pair_id"]
        attempts[pair] = attempts.get(pair, 0) + 1
        if pair == "P01" and (attempts[pair] == 1 or residual):
            visible_failure(value["module_judgments"][0], "geometry")
            value.update(status="MATERIAL_FAIL", material_failures=[])
        return value

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", observe
    )
    return attempts


def run_job(client):
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
    return client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}").json()


@pytest.mark.parametrize("residual", [False, True])
def test_actual_second_image_request_contains_the_exact_first_native_draft(
    client,
    monkeypatch,
    residual,
):
    attempts = fail_first_pair(monkeypatch, residual)
    job = run_job(client)
    assert job["status"] == "awaiting_final_review", job
    gateway = _gateway(client)
    assert len(gateway.image_calls) == 5 and attempts["P01"] == 2
    initial, repair = gateway.image_calls[:2]
    assert not [s for s in initial.sources if s.source_role == "generated_edit_canvas"]
    assert repair.sources[0].source_role == "generated_edit_canvas"
    root = detail_workspace(job["id"])
    state = json.loads((root / "session.json").read_bytes())
    first = state["modules"][0]["attempts"][0]
    original = Path(first["pair_canvas"]).read_bytes()
    assert repair.sources[0].payload == original
    assert (
        repair.sources[0].sha256
        == hashlib.sha256(original).hexdigest()
        == first["normalized_image_sha256"]
    )
    assert {
        (s.asset_id, s.sha256)
        for s in repair.sources
        if s.source_role == "product_reference"
    } == {
        (s.asset_id, s.sha256)
        for s in initial.sources
        if s.source_role == "product_reference"
    }
    receipts = [
        json.loads(p.read_bytes())
        for p in (root / "generation-calls").glob("*.json")
        if not p.name.endswith(".completion.json")
    ]
    edited = [
        r
        for r in receipts
        if r["pair_id"] == "P01" and r["native_request"].get("edit_canvas_evidence")
    ]
    assert len(edited) == 1
    proof = edited[0]["native_request"]["edit_canvas_evidence"]
    assert (
        proof["sha256"] == repair.sources[0].sha256
        and proof["source_role"] == "generated_edit_canvas"
    )
    assert edited[0]["source_assets"][0]["sha256"] == proof["sha256"]
    assert len(state["modules"][0]["attempts"]) == 2
    if residual:
        assert state["modules"][0]["status"] == "COMPLETE_WITH_RESIDUALS"
        assert state["modules"][0]["residual_issues"]


@pytest.mark.parametrize("tamper", ["wrong_edit_target", "missing_paid_original"])
def test_edit_canvas_evidence_failure_stops_before_a_second_image_call(
    client,
    monkeypatch,
    tamper,
):
    fail_first_pair(monkeypatch)
    import app.domestic_detail.edit_canvas as edit_canvas

    resolve = edit_canvas.resolve_native_edit_canvas
    changed = []

    def corrupt(runner, workspace, state, request, binding):
        if request.get("edit_target") and not changed:
            changed.append(True)
            if tamper == "wrong_edit_target":
                request = {**request, "edit_target": workspace / "input.json"}
            else:
                files = list((workspace / "generation-calls").glob("*.artifact"))
                assert len(files) == 1
                files[0].unlink()
        return resolve(runner, workspace, state, request, binding)

    monkeypatch.setattr(edit_canvas, "resolve_native_edit_canvas", corrupt)
    job = run_job(client)
    assert changed and len(_gateway(client).image_calls) == 1
    assert job["status"] in {"failed", "partial"}
    assert not any(i["status"] == "pending" for i in job["items"])


def test_owned_local_fork_compiles_target_only_delta_and_uses_original_pair_base(
    client,
):
    source = ready(client)
    source_root = detail_workspace(source["id"])
    original = inventory(source_root)
    root = source_root.parent / "job_owned_compile_revision"
    root.mkdir()
    snapshot = root / "source-session.json"
    snapshot.write_bytes((source_root / "session.json").read_bytes())
    binding = json.loads((source_root / "generation-binding.json").read_bytes())
    delta = adjustment("M02")
    delta["change_only"] = ["TARGET_ONLY_TITLE_MOVE_LEFT_20261018"]
    delta["preserve"] = ["TARGET_ONLY_KEEP_TEXTURE_20261018"]
    delta["must_not_regress"] = ["TARGET_ONLY_KEEP_NUMERIC_BLOCK_20261018"]
    intent = {
        "version": "domestic-detail-local-revision-v1",
        "job_id": root.name,
        "source_job_id": source["id"],
        "workspace_id": WORKSPACE_ID,
        "actor_id": "usr_local",
        "approval_text": "我明确授权只按本次局部修改要求开启新周期。",
        "source_session_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "source_generation_binding_sha256": binding["sha256"],
        "adjustment": delta,
    }
    intent["sha256"] = canonical_hash(intent)
    intent_file = root / "local-revision-intent.json"
    intent_file.write_text(json.dumps(intent), encoding="utf-8")
    gateway = _gateway(client)
    counts = len(gateway.planner_calls), len(gateway.image_calls)
    runner = AuroraDetailHarnessRunner()
    runner.run(
        "fork-local-revision",
        root,
        {
            "session": root / "session.json",
            "source-session": snapshot,
            "source-session-sha256": intent["source_session_sha256"],
            "revision-intent": intent_file,
        },
    )
    result = runner.run(
        "generate-next",
        root,
        {"session": root / "session.json", "out": root / "request.json"},
    )
    raw = json.loads((root / "request.json").read_bytes())
    upper, lower = raw["screen_prompts"]
    for token in [
        *delta["change_only"],
        *delta["preserve"],
        *delta["must_not_regress"],
    ]:
        assert token in lower and token not in upper
    assert "Preserve this sibling screen" in upper
    state = json.loads((root / "session.json").read_bytes())
    from app.domestic_detail.edit_canvas import resolve_native_edit_canvas

    artifact, proof = resolve_native_edit_canvas(runner, root, state, raw, {})
    assert artifact.payload == Path(raw["edit_target"]).read_bytes()
    assert (
        proof["sha256"]
        == state["modules"][0]["attempts"][-1]["normalized_image_sha256"]
    )
    assert result["module_ids"] == ["M01", "M02"]
    assert state["state"] == "AWAITING_GENERATED_MODULE"
    fork_args = {
        "session": root / "session.json",
        "source-session": snapshot,
        "source-session-sha256": intent["source_session_sha256"],
        "revision-intent": intent_file,
    }
    before = (root / "session.json").read_bytes()
    assert runner.run("fork-local-revision", root, fork_args)["reused"] is True
    assert (root / "session.json").read_bytes() == before
    # Issued requests also spend the new revision's two-slot ceiling, even if a
    # technical interruption occurs before a render is recorded. No reset can
    # silently turn an unknown request into a third potentially paid request.
    state.update(state="AUTHORIZED", active_token=None, active_render_container=None)
    for module in state["modules"][:2]:
        module["status"] = "USER_ADJUSTMENT"
    (root / "session.json").write_text(json.dumps(state), encoding="utf-8")
    runner.run(
        "generate-next",
        root,
        {"session": root / "session.json", "out": root / "request2.json"},
    )
    state = json.loads((root / "session.json").read_bytes())
    state.update(state="AUTHORIZED", active_token=None, active_render_container=None)
    for module in state["modules"][:2]:
        module["status"] = "USER_ADJUSTMENT"
    (root / "session.json").write_text(json.dumps(state), encoding="utf-8")
    before = (root / "session.json").read_bytes()
    assert runner.run("fork-local-revision", root, fork_args)["reused"] is True
    from app.aurora_detail_runtime import AuroraDetailHarnessError

    with pytest.raises(AuroraDetailHarnessError, match="budget exhausted"):
        runner.run(
            "generate-next",
            root,
            {"session": root / "session.json", "out": root / "request3.json"},
        )
    assert not (root / "request3.json").exists()
    assert (root / "session.json").read_bytes() == before
    original_requests = json.loads(snapshot.read_bytes()).get("generation_requests", [])
    state["generation_requests"] = original_requests
    (root / "session.json").write_text(json.dumps(state), encoding="utf-8")
    changed = (root / "session.json").read_bytes()
    with pytest.raises(AuroraDetailHarnessError, match="signed request history"):
        runner.run(
            "generate-next",
            root,
            {"session": root / "session.json", "out": root / "request4.json"},
        )
    with pytest.raises(AuroraDetailHarnessError, match="signed request history"):
        runner.run("fork-local-revision", root, fork_args)
    assert not (root / "request4.json").exists()
    assert (root / "session.json").read_bytes() == changed
    assert counts == (len(gateway.planner_calls), len(gateway.image_calls))
    assert inventory(source_root) == original
