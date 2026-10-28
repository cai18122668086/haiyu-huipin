"""Convergence, fact provenance and compatibility regressions, not visual QA."""

import asyncio
from copy import deepcopy

import pytest

from app.replica_exact_compact import (
    CompactResponseInvalid,
    run_class,
)
from app.replica_exact_contracts import canonical_hash, resolve_facts, user_statements
from tests.test_replica_compact import SyntheticGateway, setup_run


def review(run, stage="exact-plan-audit-dependencies"):
    return asyncio.run(
        run.ask(
            stage,
            "",
            {
                **deepcopy(run.state()["plan"]),
                "product_evidence": run.context["product_evidence"],
            },
        )
    )


def test_same_review_is_reused_but_proposal_facts_and_model_changes_invalidate(
    tmp_path,
):
    run, gateway, payload, records = setup_run(tmp_path)
    asyncio.run(run.prepare(payload, records))
    before = len(gateway.requests)
    first = review(run)
    assert len(gateway.requests) == before
    changed = deepcopy(run.state()["plan"])
    changed["modules"][0]["generation_brief"] += " Move the subject further left."
    asyncio.run(
        run.ask(
            "exact-plan-audit-proposal-change",
            "",
            {
                **changed,
                "product_evidence": run.context["product_evidence"],
            },
        )
    )
    assert len(gateway.requests) == before + 1
    run.context["resolved_facts"]["facts"][0]["statement"] = (
        "Updated supported statement"
    )
    run.context["resolved_facts"]["version"] = canonical_hash(
        run.context["resolved_facts"]["facts"]
    )
    review(run)
    assert len(gateway.requests) == before + 3
    gateway.planner_model = "another-synthetic-planner"
    review(run)
    assert len(gateway.requests) == before + 5
    assert first["unit_audits"][0]["decision"] == "PASS"


def test_original_pixels_are_part_of_review_dependency(tmp_path):
    from PIL import Image

    run, gateway, payload, records = setup_run(tmp_path)
    asyncio.run(run.prepare(payload, records))
    before = len(gateway.requests)
    Image.new("RGB", (64, 96), "green").save(run.context["product_paths"][0], "PNG")
    review(run)
    assert len(gateway.requests) == before + 2


def test_historical_usage_does_not_impose_a_new_work_budget(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    asyncio.run(run.prepare(payload, records))
    before = len(gateway.requests)
    run.usage.append({"network_requests": 10000, "kind": "analysis"})
    changed = deepcopy(run.state()["plan"])
    changed["modules"][0]["generation_brief"] += " A newly requested composition."
    value = asyncio.run(
        run.ask(
            "exact-plan-audit-after-historical-usage",
            "",
            {**changed, "product_evidence": run.context["product_evidence"]},
        )
    )
    assert value["unit_audits"][0]["decision"] == "PASS"
    assert len(gateway.requests) == before + 1
    assert gateway.requests[-1].planning_deadline is None
    assert run.checkpoints["reviews"] == {"R001": "PASS", "R002": "PASS"}


def test_corrupt_review_evidence_cannot_be_reused_or_resampled(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    asyncio.run(run.prepare(payload, records))
    cached = next(
        v for v in run.checkpoints["results"].values() if v["phase"] == "review_plan"
    )
    cached["raw"]["unit_audits"][0]["decision"] = "FAIL"
    before = len(gateway.requests)
    with pytest.raises(CompactResponseInvalid, match="evidence changed"):
        review(run)
    assert len(gateway.requests) == before


def test_excluded_facts_cannot_authorize_new_copy_or_reach_plan_review(tmp_path):
    gateway = SyntheticGateway()
    original = gateway.plan

    async def exclude(request):
        result = await original(request)
        if request.group_key == "exact-fact-resolution":
            result.payload["facts"][0]["disposition"] = "excluded"
        return result

    gateway.plan = exclude
    run, _, payload, records = setup_run(tmp_path, gateway)
    with pytest.raises(CompactResponseInvalid, match="approved fact IDs"):
        asyncio.run(run.prepare(payload, records))
    assert not any("exact-plan-audit" in r.group_key for r in gateway.requests)
    assert not gateway.image_requests


def test_fact_resolution_preserves_exclusions_and_requires_all_sources():
    statements = user_statements("Synthetic red body\nSynthetic supported benefit")
    rows = [
        {
            "id": "F001",
            "statement": statements[0]["text"],
            "disposition": "excluded",
            "kind": "physical_identity",
            "user_statement_ids": ["U001"],
            "product_marks": [],
            "reason": "Synthetic readable labeling contradicts this physical detail",
        }
    ]
    with pytest.raises(ValueError, match="every user statement"):
        resolve_facts({"facts": rows}, statements, [])
    rows.append(
        {
            **rows[0],
            "id": "F002",
            "statement": statements[1]["text"],
            "disposition": "approved",
            "kind": "claim",
            "user_statement_ids": ["U002"],
        }
    )
    ledger = resolve_facts({"facts": rows}, statements, [])
    assert [r["disposition"] for r in ledger["facts"]] == ["excluded", "approved"]
    rows[1]["product_marks"] = [{"product_index": 0, "mark_index": 4}]
    with pytest.raises(ValueError, match="observed product mark"):
        resolve_facts({"facts": rows}, statements, [])


def test_r16_protocol_remains_loadable_with_its_original_binding(tmp_path):
    legacy = run_class("exact", "exact-compact-v1")
    prepared, gateway, payload, records = setup_run(tmp_path)
    run = legacy(
        "exact",
        prepared.document_id,
        prepared.workspace_id,
        gateway,
        prepared.storage,
        root=tmp_path,
    )
    assert run.binding == {
        "version": "exact-compact-v1",
        "content_sha256": (
            "ff3a1975dede71a0ba29673c1642c33e33f76df13d14404f0a893c5e12812b5f"
        ),
    }
    asyncio.run(run.prepare(payload, records))
    snapshot = asyncio.run(run.snapshot())
    restored = legacy(
        "exact", run.document_id, run.workspace_id, gateway, run.storage, root=tmp_path
    )
    asyncio.run(restored.restore(snapshot))
    assert restored.state()["state"] == "READY"
