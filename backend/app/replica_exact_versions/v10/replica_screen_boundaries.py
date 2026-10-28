# ruff: noqa: E501
"""Physical screen reconciliation and explicit, provenance-preserving corrections."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from .replica_exact_contracts import canonical_hash
from .replica_native import digest, segmentation_errors, write_json


def inherited_intake(
    run: Any,
    data: dict[str, Any],
    sources: Any,
    schema: dict[str, Any],
    system: str,
    image_order: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Reuse an observation under its OLD identity, never as a new model verdict."""
    parent = getattr(run, "segmentation_parent", None)
    version = (parent or {}).get("model_protocol", {}).get("version")
    if not parent or version not in {
        "exact-compact-v5",
        "exact-compact-v6",
        "exact-compact-v7",
        "exact-compact-v8",
        "exact-compact-v9",
    }:
        return None
    from .replica_exact_compact import run_class

    legacy = run_class("exact", version)
    binding = sys.modules[legacy.__module__].protocol_binding()
    if (
        parent["model_protocol"] != binding
        or parent.get("source_hash") != run.source_hash
    ):
        return None
    record = parent.get("files", {}).get("compact-checkpoints.json", {})
    text = record.get("text", "")
    if digest(text.encode()) != record.get("sha256"):
        raise ValueError("Original analysis checkpoint hash changed")
    results = json.loads(text).get("results", {})
    identity = canonical_hash(
        {
            "protocol": binding,
            "phase": "intake",
            "context": run._data("intake", data),
            "observation_pass": None,
            "schema": schema,
            "system": system,
            "planner": run._planner_identity(),
            "images": [s.sha256 for s in sources],
            "image_order": image_order,
        }
    )
    cached = results.get(identity)
    if cached is None or cached.get("phase") != "intake":
        return None
    if canonical_hash(cached["raw"]) != cached["response_sha256"]:
        raise ValueError("Original intake observation hash changed")
    run.checkpoints.setdefault("inherited_observations", {})[identity] = {
        "source_protocol": binding,
        "source_response_sha256": cached["response_sha256"],
        "phase": "intake",
    }
    return deepcopy(cached)


def disagreements(
    columns: list[dict[str, Any]],
    split: dict[str, Any],
    inventory: dict[str, Any],
) -> list[dict[str, Any]]:
    result = []
    for source, proposed, observed in zip(
        columns, split["columns"], inventory["columns"], strict=True
    ):
        left, right = proposed["boundary_ys"], observed["boundary_ys"]
        # Pixel observations are approximate. A count match alone is insufficient.
        tolerance = max(3, min(24, round(source["height"] * 0.02)))
        count_differs = len(proposed["screens"]) != len(observed["screens"])
        boundary_differs = len(left) != len(right) or any(
            abs(a - b) > tolerance for a, b in zip(left, right, strict=False)
        )
        if count_differs or boundary_differs:
            result.append(
                {
                    "column_id": source["column_id"],
                    "reference_index": source["reference_index"],
                    "split_count": len(proposed["screens"]),
                    "inventory_count": len(observed["screens"]),
                    "split_boundaries": left,
                    "inventory_boundaries": right,
                    "reason": "count" if count_differs else "boundaries",
                }
            )
    return result


async def segment_references(
    run: Any, stage: str, context: Any, **kwargs: Any
) -> dict[str, Any]:
    from .replica_exact_compact import CompactScreenBoundaryMismatch

    observation = await run._visual_stage(stage, context, **kwargs)
    data = context.get("data", context)
    columns = []
    for index, column in enumerate(observation["columns"]):
        path = run.root / "crops" / f"reference-column-{index:03}.png"
        source = Path(run.context["reference_paths"][column["reference_index"]])
        run.crop(source, column["bbox"], path)
        left, top, right, bottom = column["bbox"]
        columns.append(
            {
                **column,
                "column_id": f"C{index + 1:03}",
                "width": right - left,
                "height": bottom - top,
                "crop_path": str(path),
            }
        )
    run.context["reference_columns"] = columns
    write_json(run.root / "context.json", run.context)
    inputs = {"columns": columns, "reference_dimensions": data["reference_dimensions"]}
    split = await run._visual_stage(
        "identity-screen-split-0", inputs, paths=[Path(c["crop_path"]) for c in columns]
    )
    inventory = await run._visual_stage(
        "identity-screen-inventory-0",
        inputs,
        paths=[
            *map(Path, run.context["reference_paths"]),
            *[Path(c["crop_path"]) for c in columns],
        ],
    )
    original_observations = {
        "initial_split": deepcopy(split),
        "initial_independent_inventory": deepcopy(inventory),
    }
    geometry_resolutions: list[dict[str, Any]] = []
    if getattr(run, "binding", {}).get("version") == "exact-compact-v10":
        from .replica_boundary_geometry import resolve_boundaries

        split, inventory, geometry_resolutions = resolve_boundaries(
            columns, split, inventory
        )
    failed = disagreements(columns, split, inventory)
    directed = [d["column_id"] for d in failed]
    if inventory["all_columns_covered"] and failed:
        targeted = {
            **inputs,
            "columns": [c for c in columns if c["column_id"] in directed],
            "coverage_scope": "requested_columns_only",
            "boundary_mismatch": {
                "correction_revision": getattr(run, "segmentation_revision", "initial"),
                "previous_split": [
                    c for c in split["columns"] if c["column_id"] in directed
                ],
                "independent_inventory": [
                    c for c in inventory["columns"] if c["column_id"] in directed
                ],
            },
        }
        repaired = await run._visual_stage(
            "identity-screen-split-repair",
            targeted,
            paths=[Path(c["crop_path"]) for c in targeted["columns"]],
        )
        replacements = {c["column_id"]: c for c in repaired["columns"]}
        split = {
            "columns": [replacements.get(c["column_id"], c) for c in split["columns"]]
        }
        # Preserve other columns. The fresh counter sees pixels and column metadata,
        # without the repair proposal or its expected count/boundaries.
        recount_context = {
            **inputs,
            "columns": targeted["columns"],
            "coverage_scope": "requested_columns_only",
        }
        references = sorted({c["reference_index"] for c in targeted["columns"]})
        recount = await run._visual_stage(
            "identity-screen-inventory-1-"
            + str(getattr(run, "segmentation_revision", "initial")),
            recount_context,
            paths=[
                *[Path(run.context["reference_paths"][i]) for i in references],
                *[Path(c["crop_path"]) for c in targeted["columns"]],
            ],
        )
        original_observations.update(
            targeted_split=deepcopy(repaired), independent_recount=deepcopy(recount)
        )
        replacements = {c["column_id"]: c for c in recount["columns"]}
        inventory = {
            "all_columns_covered": recount["all_columns_covered"],
            "columns": [
                replacements.get(c["column_id"], c) for c in inventory["columns"]
            ],
        }
        if getattr(run, "binding", {}).get("version") == "exact-compact-v10":
            split, inventory, additional = resolve_boundaries(columns, split, inventory)
            geometry_resolutions.extend(additional)
    failed = disagreements(columns, split, inventory)
    diagnostic = {
        "version": "physical-screen-boundaries-v1",
        "coverage_valid": inventory["all_columns_covered"],
        "split_total": sum(len(c["screens"]) for c in split["columns"]),
        "inventory_total": sum(len(c["screens"]) for c in inventory["columns"]),
        "columns": failed,
        "source_pixel_boundary_resolutions": geometry_resolutions,
    }
    run.context["segmentation_diagnostics"] = diagnostic
    write_json(
        run.root / "screen-inventory.json",
        {
            "split": split,
            "independent_inventory": inventory,
            "directed_repair_columns": directed,
            "mismatched_columns": [d["column_id"] for d in failed],
            "diagnostics": diagnostic,
            "original_model_observations": original_observations,
        },
    )
    await run.checkpoint()
    if not inventory["all_columns_covered"] or failed:
        raise CompactScreenBoundaryMismatch(
            "Physical screen boundaries remain inconsistent after targeted correction"
        )
    units = []
    for column, proposed in zip(columns, split["columns"], strict=True):
        left, top, right, _ = column["bbox"]
        edges = [0, *proposed["boundary_ys"], column["height"]]
        for index, screen in enumerate(proposed["screens"]):
            units.append(
                {
                    "reference_index": column["reference_index"],
                    "column_index": column["column_index"],
                    "screen_index": index,
                    "bbox": [left, top + edges[index], right, top + edges[index + 1]],
                    "screen_boundary_reason": f"Distinct screen: {screen['headline']}; buyer question: {screen['buyer_question']}",
                }
            )
    result = {k: observation[k] for k in ("product_geometry", "product_crops", "route")}
    result["units"] = units
    errors = segmentation_errors(
        result,
        [(p["width"], p["height"]) for p in data["product_dimensions"]],
        [(r["width"], r["height"]) for r in data["reference_dimensions"]],
    )
    if errors:
        raise CompactScreenBoundaryMismatch("; ".join(errors))
    return result
