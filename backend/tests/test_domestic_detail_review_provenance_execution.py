"""Actual synthetic jobs retain auditable provider messages through recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from app.aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
from app.domestic_detail.generation import DetailGenerationError, verify_accepted_canvas
from app.domestic_detail.plans import canonical_hash
from app.domestic_detail.review import DetailReviewError
from app.domestic_detail.review_provenance import verify_review_provenance
from app.models import JobItemRecord
from app.xinghe_agent import (
    PlannerCallResult,
    XingheImageProvider,
    _artifact_from_bytes,
    normalize_usage,
)
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_atomic_review import visible_failure
from tests.test_domestic_detail_execution import _aurora_approval_payload, _gateway
from tests.test_domestic_detail_plans import planned_job


@pytest.mark.parametrize(
    "mode",
    ["complete", "identity_followup", "repair", "residual", "missing_before_second"],
)
def test_complete_job_recovery_verifies_original_messages_without_resend(
    client, monkeypatch, mode
):
    planned = planned_job(client)
    gateway = _gateway(client)
    monkeypatch.setattr(gateway, "provider_name", "offline-original-review")
    prior_plan = gateway.plan
    sampler = XingheImageProvider(
        client.app.state.session_factory,
        gateway,
        storage=client.app.state.storage,
        skill_root=AuroraDetailHarnessRunner().skill_root,
        max_concurrency=1,
        skill_id="aurora-domestic-ecommerce-detail-page",
    )
    observations = {}
    pair_calls = {}
    review_calls = []

    async def review(request):
        if request.planning_profile not in {
            "aurora_detail_pair_audit",
            "aurora_detail_identity_audit",
        }:
            return await prior_plan(request)
        review_calls.append(request)
        context = json.loads(request.product_information[0])
        candidate = request.sources[-1]
        if request.planning_profile == "aurora_detail_pair_audit":
            artifact = _artifact_from_bytes(
                candidate.payload,
                filename=candidate.filename,
                expected_size="1440x3840",
                preserve_provider_artifact=True,
            )
            raw = sampler._native_detail_judgment(context, artifact)
            pair = context["pair_id"]
            pair_calls[pair] = pair_calls.get(pair, 0) + 1
            if (
                pair == "P01"
                and mode in {"repair", "residual"}
                and (pair_calls[pair] == 1 or mode == "residual")
            ):
                visible_failure(raw["module_judgments"][0], "geometry")
                raw.update(status="MATERIAL_FAIL", material_failures=[])
            observations[candidate.sha256] = deepcopy(raw["identity_silhouette"])
            if mode == "identity_followup":
                raw.pop("identity_silhouette")
        else:
            raw = {**observations[candidate.sha256], "audit_complete": True}
        return PlannerCallResult(
            payload=raw,
            provider_request_id=f"offline-review-{len(review_calls)}",
            raw_usage={"total_tokens": 11},
            normalized_usage=normalize_usage({"total_tokens": 11}),
            api_latency_seconds=0.01,
            actual_cost_micros=1,
            audit_raw_content=json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        )

    monkeypatch.setattr(gateway, "plan", review)
    removed = []
    if mode == "missing_before_second":
        original_execute = XingheImageProvider.execute

        async def interrupt_original_evidence(self, request):
            result = await original_execute(self, request)
            if (
                request.job_id == planned["id"]
                and not removed
                and result.outcome.value == "succeeded"
            ):
                root = detail_workspace(request.job_id)
                state = json.loads((root / "session.json").read_bytes())
                ref = state["modules"][0]["attempts"][-1]["judgment"][
                    "review_provenance"
                ][0]
                req = root / ref["request_file"]
                raw = req.with_name(req.name.replace(".request.json", ".reply.txt"))
                raw.unlink()
                removed.append(raw)
            return result

        monkeypatch.setattr(XingheImageProvider, "execute", interrupt_original_evidence)

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
    if mode == "missing_before_second":
        assert current["status"] == "partial", current
        assert len(removed) == 1 and not removed[0].exists()
        with client.app.state.session_factory() as db:
            items = db.scalars(
                select(JobItemRecord)
                .where(JobItemRecord.job_id == planned["id"])
                .order_by(JobItemRecord.ordinal)
            ).all()
            assert items[0].status == "succeeded"
            assert items[1].status == "failed"
            assert items[1].error_code == "NATIVE_DETAIL_REVIEW_PROVENANCE_INVALID"
            assert all(item.status == "skipped" for item in items[2:])
        for _ in range(2):
            asyncio.run(client.app.state.task_runner.run(planned["id"]))
        assert len(gateway.image_calls) == 1 and len(review_calls) == 1
        return
    assert current["status"] == "awaiting_final_review", current
    assert len(gateway.image_calls) == (5 if mode in {"repair", "residual"} else 4)
    assert len(review_calls) == (
        8 if mode == "identity_followup" else len(gateway.image_calls)
    )
    root = detail_workspace(planned["id"])
    binding = json.loads((root / "generation-binding.json").read_bytes())
    runner = AuroraDetailHarnessRunner()
    lineage = runner.run(
        "inspect-pair-artifacts",
        root,
        {
            "session": root / "session.json",
            "pair-id": "P01",
            "selection": "ACCEPTED",
        },
    )
    evidence = runner.run(
        "inspect-pair-review-evidence",
        root,
        {
            "session": root / "session.json",
            "pair-id": "P01",
        },
    )
    canvas_sha = lineage["canvas"]["sha256"]
    for _ in range(2):
        assert (
            verify_accepted_canvas(
                [root], binding, pair_id="P01", artifact_sha256=canvas_sha
            )
            == lineage
        )
    count = len(review_calls), len(gateway.image_calls)
    citation = evidence["audit_input"]["review_provenance"][0]
    response_file = root / citation["response_file"]
    snapshot = Path(evidence["snapshot_reference"]["snapshot_file"])
    original_files = [snapshot]
    for reference in evidence["audit_input"]["review_provenance"]:
        req = root / reference["request_file"]
        original_files += [
            req,
            root / reference["response_file"],
            req.with_name(req.name.replace(".request.json", ".reply.txt")),
        ]
    for path in original_files:
        original = path.read_bytes()
        for payload in (None, original + b"altered original evidence"):
            if payload is None:
                path.unlink()
            else:
                path.write_bytes(payload)
            with pytest.raises(DetailGenerationError):
                verify_accepted_canvas(
                    [root], binding, pair_id="P01", artifact_sha256=canvas_sha
                )
            path.write_bytes(original)
        assert path.read_bytes() == original
    assert (len(review_calls), len(gateway.image_calls)) == count

    # A retry workspace reuses the exact original snapshots/messages in its source.
    clone = root.parent / (root.name + "-isolated-checkpoint-copy")
    clone.mkdir()
    for name in (
        "generation-binding.json",
        "input.json",
        "identity.json",
        "plan.json",
        "native-contract-binding.json",
    ):
        (clone / name).write_bytes((root / name).read_bytes())
    state = json.loads((root / "session.json").read_bytes())
    state["session_dir"] = str(clone)
    state["checkpoint_source_job_id"] = planned["id"]
    (clone / "session.json").write_text(json.dumps(state), encoding="utf-8")
    assert (
        verify_accepted_canvas(
            [clone, root], binding, pair_id="P01", artifact_sha256=canvas_sha
        )
        == lineage
    )
    if mode in {"repair", "residual"}:
        assert len(state["modules"][0]["attempts"]) == 2
        first = state["modules"][0]["attempts"][0]["review_evidence"]
        assert first["token"] != evidence["snapshot_reference"]["token"]
        assert first["snapshot_file"] != str(snapshot)
        assert Path(first["snapshot_file"]).is_file()
        for module in state["modules"][:2]:
            module["attempts"][-1]["review_evidence"] = first
        (clone / "session.json").write_text(json.dumps(state), encoding="utf-8")
        with pytest.raises(DetailGenerationError):
            verify_accepted_canvas(
                [clone, root], binding, pair_id="P01", artifact_sha256=canvas_sha
            )
    assert (len(review_calls), len(gateway.image_calls)) == count

    # Exercise the inner raw-message check after the outer file hash is updated.
    calls = list((root / "generation-calls").glob("*.json"))
    invocation = next(
        json.loads(path.read_bytes())
        for path in calls
        if not path.name.endswith(".completion.json")
        and json.loads(path.read_bytes())["native_request"]["token"] == lineage["token"]
    )
    original = response_file.read_bytes()
    changed = json.loads(original)
    changed["result"]["payload"]["identity_silhouette"] = {
        "shape_consistent": True,
        "colorway_consistent": True,
        "texture_consistent": True,
        "comparison_scope": "VISIBLE_COMPLETE_PRODUCT",
        "visible_features": ["Altered original feature"],
        "visible_evidence": "Altered observation; original model text stays unchanged",
    }
    changed["response"] = changed["result"]["payload"]
    changed["result_sha256"] = canonical_hash(changed["result"])
    response_file.write_text(json.dumps(changed), encoding="utf-8")
    altered = deepcopy(evidence)
    altered["audit_input"]["review_provenance"][0]["response_file_sha256"] = (
        hashlib.sha256(response_file.read_bytes()).hexdigest()
    )
    with pytest.raises(DetailReviewError, match="Original model text"):
        verify_review_provenance(root, invocation, lineage, altered)
    response_file.write_bytes(original)
    assert (
        verify_accepted_canvas(
            [root], binding, pair_id="P01", artifact_sha256=canvas_sha
        )
        == lineage
    )
    asyncio.run(client.app.state.task_runner.run(planned["id"]))
    assert (len(review_calls), len(gateway.image_calls)) == count
