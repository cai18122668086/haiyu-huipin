"""C4 requirement: accepted pixels alone do not authenticate their audit."""

import json
from pathlib import Path

import pytest

from app.domestic_detail.generation import (
    DetailGenerationError,
    begin_invocation,
    bind_workspace,
    finish_invocation,
    verify_accepted_canvas,
)
from tests.test_domestic_detail_pixels import inspect
from tests.test_native_detail_failure_transition import audit
from tests.test_native_detail_failure_transition import pair_audit as pair_audit


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_cited_receipts",
        "changed_identity_observation",
        "changed_atomic_observation",
        "disagreeing_sibling_audit",
        "snapshot_missing",
        "snapshot_changed",
        "snapshot_escape",
        "snapshot_wrong_token",
        "snapshot_wrong_pair",
        "accepted_boolean_replaced_by_integer",
        "atomic_boolean_replaced_by_integer",
    ],
)
def test_accepted_recovery_requires_immutable_review_evidence(pair_audit, tamper):
    root, current, judgment = pair_audit
    judgment.update(status="PASS", material_failures=[])
    if tamper == "missing_cited_receipts":
        judgment["review_provenance"] = [
            {
                "phase": "aurora_detail_pair_audit",
                "binding_sha256": "a" * 64,
                "request_file": "reviews/missing.request.json",
                "request_file_sha256": "b" * 64,
                "response_file": "reviews/missing.response.json",
                "response_file_sha256": "c" * 64,
                "original_message_sha256": "d" * 64,
            }
        ]
    audit((root, current, judgment))
    lineage = inspect(root, "ACCEPTED")
    binding = {"sha256": "b" * 64}
    bind_workspace(root, binding, create=True)
    receipt = begin_invocation(
        root,
        binding,
        {
            "token": lineage["token"],
            "pair_id": "P01",
            "signed_request_sha256": lineage["signed_request_sha256"],
        },
        provider="synthetic",
        model="synthetic",
        pricing_version="test",
        idempotency_key="synthetic-original",
        prompt="synthetic",
        size="3x8",
        quality="high",
        sources=[],
    )
    finish_invocation(
        receipt,
        outcome="succeeded",
        provider_request_id="synthetic",
        artifact_sha256=lineage["source"]["sha256"],
        artifact_payload=Path(lineage["source"]["path"]).read_bytes(),
    )
    path = root / "session.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    if tamper == "changed_identity_observation":
        for module in state["modules"]:
            module["attempts"][-1]["judgment"]["identity_silhouette"][
                "visible_evidence"
            ] = "Altered accepted identity observation; pixel bytes unchanged"
    elif tamper == "changed_atomic_observation":
        for module in state["modules"]:
            module["attempts"][-1]["judgment"]["module_judgments"][0]["observations"][
                "structural_zone_count"
            ] = 99
    elif tamper == "disagreeing_sibling_audit":
        state["modules"][1]["attempts"][-1]["judgment"]["residual_issues"] = [
            "Changed sibling audit despite one accepted pair canvas"
        ]
    elif tamper in {
        "accepted_boolean_replaced_by_integer",
        "atomic_boolean_replaced_by_integer",
    }:
        for module in state["modules"]:
            report = module["attempts"][-1]["judgment"]
            if tamper == "accepted_boolean_replaced_by_integer":
                report["checks"]["identity"]["pass"] = 1
            else:
                report["module_judgments"][0]["observations"][
                    "evidence_is_primary_visual"
                ] = 1
    elif tamper == "snapshot_missing":
        Path(
            state["modules"][0]["attempts"][-1]["review_evidence"]["snapshot_file"]
        ).unlink()
    elif tamper == "snapshot_changed":
        original = Path(
            state["modules"][0]["attempts"][-1]["review_evidence"]["snapshot_file"]
        )
        original.write_bytes(original.read_bytes() + b"changed snapshot bytes")
    elif tamper.startswith("snapshot_"):
        field, value = {
            "snapshot_escape": ("snapshot_file", str(root.parent / "escaped.json")),
            "snapshot_wrong_token": ("token", "different-render-token"),
            "snapshot_wrong_pair": ("pair_id", "P02"),
        }[tamper]
        for module in state["modules"]:
            module["attempts"][-1]["review_evidence"][field] = value
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(DetailGenerationError):
        verify_accepted_canvas(
            [root],
            binding,
            pair_id="P01",
            artifact_sha256=lineage["canvas"]["sha256"],
        )


@pytest.mark.parametrize("location", ["request", "response"])
def test_original_receipt_preserves_boolean_type(tmp_path, location):
    from app.domestic_detail.review import DetailReviewError, ReviewStore

    store = ReviewStore(tmp_path / "reviews", {"phase": "synthetic", "single": True})
    store.begin()
    store.finish(
        {"payload": {"observed": True}, "audit_raw_content": '{"observed":true}'}
    )
    path = store.request_path if location == "request" else store.response_path
    original = json.loads(path.read_bytes())
    if location == "request":
        original["binding"]["single"] = 1
    else:
        original["response"]["observed"] = 1
    path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(DetailReviewError):
        store.completed()
