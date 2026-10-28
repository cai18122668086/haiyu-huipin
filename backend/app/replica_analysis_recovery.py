# ruff: noqa: E501
"""Application admission for explicit analysis correction, without rewriting history."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

from .replica_native import digest


def document_file(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    record = snapshot.get("files", {}).get(name, {})
    text = record.get("text", "")
    if not text or digest(text.encode()) != record.get("sha256"):
        return {}
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def segmentation_diagnostics(values: dict[str, Any]) -> dict[str, Any] | None:
    if values.get("segmentation_diagnostics"):
        return values["segmentation_diagnostics"]
    snapshot = values.get("_analysis_checkpoint", {})
    inventory = document_file(snapshot, "screen-inventory.json")
    if not inventory:
        # v5 stopped on invalid coverage before writing its inventory summary.
        # Its hash-bound raw observations still live in the durable checkpoint.
        results = list(
            document_file(snapshot, "compact-checkpoints.json")
            .get("results", {})
            .values()
        )
        split_record = next(
            (r for r in reversed(results) if r.get("phase") == "screen_split"), None
        )
        counter_record = next(
            (r for r in reversed(results) if r.get("phase") == "screen_inventory"), None
        )
        if split_record and counter_record:
            from .replica_exact_contracts import canonical_hash

            if all(
                canonical_hash(r.get("raw")) == r.get("response_sha256")
                for r in (split_record, counter_record)
            ):
                inventory = {
                    "split": split_record["raw"],
                    "independent_inventory": counter_record["raw"],
                }
    if inventory.get("diagnostics"):
        return inventory["diagnostics"]
    context = document_file(snapshot, "context.json")
    columns = {c["column_id"]: c for c in context.get("reference_columns", [])}
    split = inventory.get("split", {}).get("columns", [])
    observed = inventory.get("independent_inventory", {}).get("columns", [])
    if not split or len(split) != len(observed):
        return None
    return {
        "version": "legacy-count-summary-v1",
        "coverage_valid": inventory.get("independent_inventory", {}).get(
            "all_columns_covered", False
        ),
        "split_total": sum(len(c.get("screens", [])) for c in split),
        "inventory_total": sum(len(c.get("screens", [])) for c in observed),
        "columns": [
            {
                "column_id": a["column_id"],
                "reference_index": columns.get(a["column_id"], {}).get(
                    "reference_index"
                ),
                "split_count": len(a.get("screens", [])),
                "inventory_count": len(b.get("screens", [])),
                "reason": "count",
            }
            for a, b in zip(split, observed, strict=True)
            if len(a.get("screens", [])) != len(b.get("screens", []))
        ],
    }


def segmentation_repair_available(row: Any, input_hash: str) -> bool:
    values = row.payload
    return bool(
        row.status == "failed"
        and values.get("replication_method") == "exact"
        and values.get("error_code") == "REPLICA_SEGMENTATION_INVALID"
        and not values.get("job_ids")
        and not values.get("plans")
        and values.get("_analysis_input_hash") == input_hash
        and values.get("_analysis_checkpoint", {})
        .get("model_protocol", {})
        .get("version")
        in {
            "exact-compact-v5",
            "exact-compact-v6",
            "exact-compact-v7",
            "exact-compact-v8",
            "exact-compact-v9",
            "exact-compact-v10",
            "exact-compact-v11",
            "exact-compact-v12",
            "exact-compact-v13", "exact-compact-v14", "exact-compact-v15",
        }
        and segmentation_diagnostics(values)
    )


def style_revision_available(row: Any, input_hash: str) -> bool:
    from .replica_style_analysis import JOURNAL, policy

    values = row.payload
    checkpoint = values.get("_analysis_checkpoint", {})
    return bool(
        row.status == "failed"
        and values.get("replication_method") == "style"
        and values.get("error_code") == "REPLICA_PLAN_AUDIT_REJECTED"
        and not values.get("job_ids")
        and values.get("_analysis_input_hash") == input_hash
        and checkpoint.get("analysis_policy") == policy()
        and document_file(checkpoint, JOURNAL).get("cursor", {}).get("phase")
        == "rejected"
    )


def exact_revision_available(row: Any, input_hash: str) -> bool:
    """Continue a rejected current-protocol plan through explicit HTTP intent."""
    values = row.payload
    snapshot = values.get("_analysis_checkpoint", {})
    if (
        row.status != "failed"
        or values.get("replication_method") != "exact"
        or values.get("error_code") != "REPLICA_PLAN_AUDIT_REJECTED"
        or values.get("job_ids")
        or values.get("plans")
        or values.get("native_snapshot")
        or values.get("_analysis_input_hash") != input_hash
        or snapshot.get("method") != "exact"
    ):
        return False
    from .replica_exact_compact import protocol_binding

    binding = protocol_binding()
    if (
        values.get("_exact_model_protocol") != binding["version"]
        or snapshot.get("model_protocol") != binding
    ):
        return False
    cursor = document_file(snapshot, "compact-checkpoints.json").get("plan_cursor", {})
    state = document_file(snapshot, "session.json")
    context = document_file(snapshot, "context.json")
    plan, audit = cursor.get("plan"), cursor.get("audit", {})
    native_plan = deepcopy(state.get("plan"))
    expected_plan = deepcopy(plan)
    # Native compile-plan annotates this server-derived ratio on each module.
    for candidate in (native_plan, expected_plan):
        if isinstance(candidate, dict):
            for module in candidate.get("modules", []):
                module.pop("reference_aspect_ratio", None)
    return bool(
        cursor.get("phase") == "revise"
        and type(cursor.get("rounds")) is int
        and cursor["rounds"] >= 3
        and plan
        and state.get("state") == "PLAN_REJECTED"
        and native_plan == expected_plan
        and not state.get("current")
        and context.get("model_protocol") == binding
        and any(r.get("decision") == "FAIL" for r in audit.get("unit_audits", []))
    )


def configure_exact_revision(run: Any, values: dict[str, Any], checkpoint: Any) -> None:
    """Extend only the explicit revision, retaining rounds and model caches.

    The native adapter already revises only failed modules and hashes each
    review subject. Application admission supplies another bounded round window;
    the persisted receipt prevents retries from granting another window.
    """
    from .replica_exact_contracts import canonical_hash
    from .replica_native import ReplicaNativeError

    authority = values.get("_exact_plan_revision")
    if not authority:
        return
    cursor = run.checkpoints.get("plan_cursor", {})
    receipt = cursor.get("explicit_revision", {})
    if receipt.get("id") != authority.get("id"):
        if (
            not authority.get("id")
            or canonical_hash(checkpoint) != authority.get("parent_snapshot_sha256")
            or cursor.get("phase") != "revise"
            or run.state().get("state") != "PLAN_REJECTED"
        ):
            raise ReplicaNativeError("方案修正授权与原始检查点不一致。")
        receipt = {
            **authority,
            "stop_after": cursor["rounds"] + type(run).planning_revisions,
        }
        cursor["explicit_revision"] = receipt
    if {k: receipt.get(k) for k in authority} != authority:
        raise ReplicaNativeError("方案修正授权已变化。")
    run.planning_revisions = receipt["stop_after"]


def prepare_segmentation_retry(values: dict[str, Any]) -> dict[str, Any]:
    from .replica_exact_compact import VERSION

    values = dict(values)
    checkpoint = values["_analysis_checkpoint"]
    diagnostic = segmentation_diagnostics(values) or {}
    values["_segmentation_revision"] = {
        "id": uuid4().hex,
        "coverage_retry": diagnostic.get("coverage_valid") is False,
    }
    if checkpoint["model_protocol"]["version"] != VERSION:
        # Keep the complete original snapshot; never relabel old FAILs as new results.
        values["_analysis_lineage"] = [*values.get("_analysis_lineage", []), checkpoint]
        values["_segmentation_parent"] = checkpoint
        values["_exact_model_protocol"] = VERSION
        values.pop("_analysis_checkpoint", None)
        values.pop("_analysis_input_hash", None)
    return values


def configure_run(run: Any, values: dict[str, Any], checkpoint: Any) -> None:
    if values.get("replication_method") == "exact":
        from .replica_analysis_attempt import AttemptScopedPlanner

        run.gateway = AttemptScopedPlanner(
            run.gateway, values.get("analysis_attempt_id", "initial")
        )
        configure_exact_revision(run, values, checkpoint)
        correction = values.get("_segmentation_revision", {})
        run.segmentation_revision = correction.get("id", "initial")
        run.segmentation_coverage_retry = correction.get("coverage_retry", False)
        run.segmentation_parent = values.get("_segmentation_parent")
        if run.segmentation_parent and not checkpoint:
            run.usage = list(run.segmentation_parent.get("usage", []))
            run.cost = int(run.segmentation_parent.get("cost", 0))
    else:
        run.authorized_revision = values.get("_style_revision_requested")
        run.evidence_revision = values.get("analysis_attempt_id", "initial")
        authority = values.get("_style_analysis_revision")
        if (
            authority
            and run.journal.get("cursor", {}).get("applied_revision")
            != run.authorized_revision
        ):
            from .replica_exact_contracts import canonical_hash
            from .replica_native import ReplicaNativeError

            if canonical_hash(checkpoint) != authority.get("parent_snapshot_sha256"):
                raise ReplicaNativeError(
                    "Style correction authority does not match its original snapshot"
                )
