"""The same commercial evidence policy reaches planning, audit and image dispatch."""

import asyncio
import json

import pytest

from app.replica_native import (
    VISUAL_EVIDENCE_POLICY,
    VISUAL_EVIDENCE_POLICY_VERSION,
    digest,
)

from .test_replica_audit_evidence import fixture
from .test_replica_product_evidence import setup


@pytest.mark.parametrize(
    "stage", ["identity-segmentation", "exact-plan-r0", "exact-pixel-audit-token"]
)
def test_planning_and_audit_share_policy_without_overwriting_model_failure(
    tmp_path, stage
):
    decision = {
        "decision": "FAIL",
        "major_mismatches": ["Clearly changed supported product silhouette"],
    }
    run, calls, received, context, reference, artwork = fixture(tmp_path, decision)
    value = asyncio.run(
        run.contract(
            stage,
            "Observe supplied pixels",
            context,
            "audit-current",
            "judgment",
            paths=[reference, artwork],
        )
    )
    assert value["decision"] == received[0]["decision"] == "FAIL"
    assert value["major_mismatches"] == decision["major_mismatches"]
    contract = json.loads(calls[0].product_information[0])
    assert contract["visual_evidence_policy"] == {
        "version": VISUAL_EVIDENCE_POLICY_VERSION,
        "instruction": VISUAL_EVIDENCE_POLICY,
    }
    assert VISUAL_EVIDENCE_POLICY in calls[0].system_prompt


@pytest.mark.parametrize("method", ["exact", "style"])
@pytest.mark.parametrize("repair", [False, True])
def test_policy_reaches_initial_and_repair_images_and_is_bound_to_dispatch_hash(
    tmp_path, method, repair
):
    render, calls, original, _, root = setup(tmp_path, method, repair)
    original_hash = digest(original.read_bytes())
    asyncio.run(render())
    sent = calls[0]
    receipt = json.loads((root / "invocations.json").read_text())["fixture"]
    assert VISUAL_EVIDENCE_POLICY in sent.prompt
    assert receipt["visual_evidence_policy_version"] == VISUAL_EVIDENCE_POLICY_VERSION
    assert receipt["dispatch_prompt_sha256"] == digest(sent.prompt.encode())
    assert digest(original.read_bytes()) == original_hash
    asyncio.run(render())
    assert len(calls) == 1
