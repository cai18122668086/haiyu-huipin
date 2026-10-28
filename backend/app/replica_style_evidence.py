"""Physical crop evidence from PR127, composed with one durable analysis journal.

No workflow/plan state is owned here. Only the native Skill changes that state;
the caller owns phase checkpoints and hash-bound model response reuse.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .replica_exact_contracts import canonical_hash
from .replica_native import ReplicaNativeError, digest, read_json, write_json

PROVENANCE = "style-crop-provenance.json"

FROZEN_ANALYSIS = (
    "overview_detection",
    "source_content_analysis",
    "narrative_logic",
    "source_semantics",
    "benchmark_sequence",
)

FLAGS = (
    "source_product_presence",
    "source_brand_presence",
    "source_packaging_presence",
    "source_logo_presence",
    "source_sku_presence",
    "competitor_presence",
    "source_copy_presence",
    "source_environment_presence",
)


class StyleEvidenceInvalid(ReplicaNativeError):
    code = "REPLICA_STYLE_EVIDENCE_INVALID"
    message = "部分参考步骤缺少经过复查的真实风格裁图，已有分析已保留，可继续修正。"


CROP_PATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "anyOf": [{"required": ["units"]}, {"required": ["crop_specs"]}],
    "properties": {
        "units": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]*$"},
                    "source_crop_path": {"type": "string"},
                    "reference_input_id": {"type": "string", "minLength": 1},
                    "source_content_step_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "source_role": {
                        "enum": [
                            "style_mechanism_only",
                            "layout_only",
                            "color_only",
                            "human_action_only",
                        ]
                    },
                    "source_semantics": {"type": "array", "minItems": 1},
                    "review_status": {"const": "visual_model"},
                    **{flag: {"type": "boolean"} for flag in FLAGS},
                },
            },
        },
        "crop_specs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "reference_input_id", "bbox"],
                "properties": {
                    "id": {"type": "string"},
                    "reference_input_id": {"type": "string"},
                    "bbox": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {"type": "integer"},
                    },
                },
            },
        },
        "mechanisms": {"type": "object"},
    },
}


def crop_patch(
    analysis: dict[str, Any], response: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Only editable crop fields cross the model boundary; omissions are not edits.

    A proposal may omit reviews. A final review must explicitly cover every
    resulting unit and all safety flags; old flags never fill a missing review.
    """
    if any(Draft202012Validator(CROP_PATCH_SCHEMA).iter_errors(response)):
        raise StyleEvidenceInvalid("Return only units/crop_specs/mechanisms crop patch")
    result = deepcopy(analysis)
    result.pop("crop_specs", None)
    old = {u["id"]: u for u in result["units"]}
    rows = response.get("units", [])
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise StyleEvidenceInvalid("Crop patch contains duplicate unit IDs")
    new_specs = {s["id"] for s in response.get("crop_specs", [])}
    reviewed = set()
    for row in rows:
        uid = row["id"]
        if uid not in old and (
            uid not in new_specs
            or not all(
                row.get(k)
                for k in (
                    "reference_input_id",
                    "source_content_step_ids",
                    "source_role",
                    "source_semantics",
                )
            )
        ):
            raise StyleEvidenceInvalid(
                "New crop units require a physical crop spec and source bindings"
            )
        merged = {**old.get(uid, {}), **deepcopy(row)}
        for field in (*FLAGS, "review_status"):
            if field not in row:
                merged.pop(field, None)
        old[uid] = merged
        if row.get("review_status") == "visual_model" and all(
            type(row.get(flag)) is bool for flag in FLAGS
        ):
            reviewed.add(uid)
    result["units"] = list(old.values())
    for field in ("crop_specs", "mechanisms"):
        if field in response:
            result[field] = deepcopy(response[field])
    return result, reviewed == set(old)


def apply_patch_response(
    run: Any, analysis: dict[str, Any], response: dict[str, Any]
) -> tuple[dict[str, Any], bool, bool]:
    try:
        patched, complete = crop_patch(analysis, response)
        return patched, complete, run._apply_specs(patched)
    except StyleEvidenceInvalid as error:
        response_hash = canonical_hash(response)
        for record in run.journal["requests"].values():
            if record["sha256"] == response_hash:
                record["invalid_contract"] = True
                record["validation_failure"] = {
                    "command": "style-crop-patch",
                    "detail": str(error),
                }
        raise


def crop_errors(
    analysis: dict[str, Any], provenance: dict[str, Any], references: dict[str, Any]
) -> list[str]:
    """Bind every source step to real crops within its physical source panel."""
    errors: list[str] = []
    panels = {p["panel_id"]: p for p in analysis["overview_detection"]["panels"]}
    steps = analysis["source_content_analysis"]["sequence"]
    step_ids = {s["id"] for s in steps}
    usable = []
    for unit in analysis.get("units", []):
        uid = unit.get("id", "?")
        path = Path(unit.get("source_crop_path", ""))
        record = provenance.get(str(path))
        if (
            not record
            or not path.is_file()
            or digest(path.read_bytes()) != record["crop_sha256"]
        ):
            errors.append(f"{uid}: crop needs server-created physical provenance")
            continue
        source = references.get(unit.get("reference_input_id"))
        if (
            not source
            or not Path(source["path"]).is_file()
            or digest(Path(source["path"]).read_bytes()) != source["sha256"]
            or record["source_path"] != source["path"]
            or record["source_sha256"] != source["sha256"]
        ):
            errors.append(f"{uid}: crop must come from its registered reference")
            continue
        if (
            any(unit.get(flag) is not False for flag in FLAGS)
            or unit.get("review_status") != "visual_model"
        ):
            errors.append(
                f"{uid}: fresh visual review must confirm all contamination flags"
            )
            continue
        bound = unit.get("source_content_step_ids")
        if (
            not isinstance(bound, list)
            or not bound
            or not set(bound).issubset(step_ids)
        ):
            errors.append(f"{uid}: bind actual known source_content_step_ids")
            continue
        usable.append((unit, record))
    for step in steps:
        matched = False
        for unit, record in usable:
            if step["id"] not in unit["source_content_step_ids"]:
                continue
            for pid in step.get("source_panel_ids", []):
                panel = panels.get(pid)
                if (
                    not panel
                    or unit.get("reference_input_id") != panel["reference_input_id"]
                ):
                    continue
                left, top, right, bottom = record["bbox"]
                pl, pt, pr, pb = panel["bbox"]
                if pl <= left < right <= pr and pt <= top < bottom <= pb:
                    matched = True
        if not matched:
            errors.append(
                f"{step['id']}: missing reviewed crop inside its source panel"
            )
    return errors


def apply_specs(run: Any, analysis: dict[str, Any]) -> bool:
    specs = analysis.pop("crop_specs", [])
    if not isinstance(specs, list):
        raise StyleEvidenceInvalid("crop_specs must be a list")
    units = {u["id"]: u for u in analysis.get("units", [])}
    refs = {
        r["id"]: r for r in run.state()["inputs"] if r["role"] == "benchmark_reference"
    }
    seen = set()
    changed = False
    for spec in specs:
        uid = spec.get("id", "")
        rid = spec.get("reference_input_id")
        if (
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", uid)
            or uid in seen
            or uid not in units
            or rid not in refs
        ):
            raise StyleEvidenceInvalid(
                "crop_specs must cite unique units and registered references"
            )
        seen.add(uid)
        source = run.path(refs[rid]["path"])
        if digest(source.read_bytes()) != refs[rid]["sha256"]:
            raise StyleEvidenceInvalid("Original reference bytes changed")
        target = run.root / "crops" / f"style-{uid}.png"
        old = digest(target.read_bytes()) if target.exists() else None
        run.crop(source, spec["bbox"], target)
        units[uid]["source_crop_path"] = str(target)
        units[uid]["reference_input_id"] = rid
        changed |= old != digest(target.read_bytes())
    return changed


async def review_crops(run: Any, analysis: dict[str, Any]) -> dict[str, Any]:
    cursor = run.journal["cursor"]
    evidence = cursor.setdefault("evidence", {})
    if evidence.get("candidate") is not None:
        analysis = deepcopy(evidence["candidate"])
    baseline = deepcopy(evidence.get("source_analysis", analysis))
    for field in FROZEN_ANALYSIS:
        if field in baseline:
            analysis[field] = deepcopy(baseline[field])
        else:
            analysis.pop(field, None)
    evidence.update(source_analysis=baseline, candidate=analysis)
    errors: list[str] = list(evidence.get("validation_errors", []))
    if errors:
        corrected = await run._ask_model(
            "style-crop-repair-contract",
            "Return only a crop patch matching response_schema; omit source fields. "
            "Correct native evidence validation errors. For replacement crop "
            "pixels return crop_specs in original reference coordinates; do not "
            "relabel a FAIL, erase steps or claim that unseen crops were reviewed.",
            {
                "analysis": analysis,
                "workflow": run.state(),
                "validation_errors": errors,
                "correction_revision": getattr(run, "evidence_revision", "initial"),
                "response_schema": CROP_PATCH_SCHEMA,
            },
        )
        try:
            corrected, _, _ = apply_patch_response(run, analysis, corrected)
        except StyleEvidenceInvalid:
            await run.checkpoint()
            raise
        evidence["candidate"] = analysis = corrected
        await run.checkpoint()
    for attempt in range(run.contract_attempts):
        await run.checkpoint()
        reviewed = await run._ask_model(
            f"style-crop-review-{attempt}",
            "Inspect ACTUAL sanitized crop pixels and physical source panels. "
            "Return ONLY units/crop_specs/optional mechanisms matching "
            "response_schema. "
            "Server retains frozen observations; do not echo or change them. "
            "Final review includes EVERY current_unit_id, review_status=visual_model "
            "and ALL eight boolean contamination flags. Never omit an unsafe unit; "
            "report its real flags. Crop proposals may omit reviews, but cannot pass. "
            "Every step needs a useful safe crop from its OWN physical panel; "
            "do not bind one swatch to unrelated panels. Full analysis panels "
            "support global composition and reading path. Crops supplement them "
            "with actual type/material/action evidence where available, without "
            "readable source wording, products, logos or literal environments. "
            "Partial unreadable glyph/stroke fragments and face-free/product-free "
            "action details are allowed. For recrops return crop_specs: "
            "[{id,reference_input_id,bbox:[l,t,r,b]}] in ORIGINAL coordinates, "
            "plus new metadata for added IDs. Server crops first; a fresh "
            "pixel review inspects those new bytes. Never declare unseen crops "
            "safe or edit source steps to hide missing coverage.",
            {
                "analysis": analysis,
                "workflow": run.state(),
                "validation_errors": errors,
                "response_schema": CROP_PATCH_SCHEMA,
                "current_unit_ids": [u["id"] for u in analysis["units"]],
                "correction_revision": getattr(run, "evidence_revision", "initial"),
            },
        )
        try:
            reviewed, complete_review, changed = apply_patch_response(
                run, analysis, reviewed
            )
        except StyleEvidenceInvalid as error:
            errors = [str(error)]
            await run.checkpoint()
            continue
        evidence["candidate"] = analysis = reviewed
        await run.checkpoint()
        if changed:
            errors = ["New physical crop bytes require a fresh visual review"]
            continue
        if not complete_review:
            errors = ["Fresh review requires every current unit and all eight flags"]
            continue
        provenance = (
            read_json(run.root / PROVENANCE) if (run.root / PROVENANCE).exists() else {}
        )
        refs = {
            r["id"]: r
            for r in run.state()["inputs"]
            if r["role"] == "benchmark_reference"
        }
        errors = crop_errors(reviewed, provenance, refs)
        if not errors:
            evidence.pop("validation_errors", None)
            return reviewed
        corrected = await run._ask_model(
            f"style-crop-repair-{attempt}",
            "Correct only missing/invalid crops in validation_errors. Return "
            "a crop patch matching response_schema. Omit source fields. crop_specs: "
            "[{id,reference_input_id,bbox:[l,t,r,b]}] for new/replaced crops, "
            "using ORIGINAL reference pixels. Preserve panel metadata, content "
            "steps, narrative and source semantics exactly. Bind useful actual "
            "type/material/action crops to their own source_content_step_ids. "
            "Never just relabel swatches, claim PASS or rewrite page copy.",
            {
                "analysis": analysis,
                "validation_errors": errors,
                "workflow": run.state(),
                "correction_revision": getattr(run, "evidence_revision", "initial"),
                "response_schema": CROP_PATCH_SCHEMA,
                "current_unit_ids": [u["id"] for u in analysis["units"]],
            },
        )
        try:
            corrected, _, _ = apply_patch_response(run, analysis, corrected)
        except StyleEvidenceInvalid as error:
            errors = [str(error)]
            await run.checkpoint()
            continue
        evidence["candidate"] = analysis = corrected
        await run.checkpoint()
    raise StyleEvidenceInvalid("; ".join(errors))


def job_crop_manifest(
    run: Any, workflow: dict[str, Any], manifest: dict[str, Any], job: dict[str, Any]
) -> dict[str, Any]:
    """Adapt only native path-dedup collisions inside the current actual job.

    Byte-identical aliases retain the original pixel review; they are not new
    crops or new visual verdicts. The confirmed analysis/manifest is immutable.
    """
    units = manifest.get("units", [])
    if all(u.get("review_status") == "fixture" for u in units):
        return manifest
    steps = {s["id"]: s for s in workflow["reference_content_analysis"]["sequence"]}
    wanted = list(
        dict.fromkeys(
            sid
            for uid in job["unit_ids"]
            for sid in workflow["units"][uid].get("source_content_step_ids", [])
        )
    )
    seen, bound = set(), set()
    for sid in wanted:
        if sid not in steps:
            raise StyleEvidenceInvalid("Current job cites an unknown source step")
        for rid in steps[sid].get("source_reference_ids", []):
            for unit in units:
                if unit.get("reference_input_id") == rid and sid in unit.get(
                    "source_content_step_ids", []
                ):
                    binding_key = (unit["path"], rid)
                    if binding_key not in seen:
                        seen.add(binding_key)
                        bound.add(sid)
    missing = [sid for sid in wanted if sid not in bound]
    if not missing:
        return manifest
    refs = {
        r["id"]: r for r in workflow["inputs"] if r["role"] == "benchmark_reference"
    }
    provenance = (
        read_json(run.root / PROVENANCE) if (run.root / PROVENANCE).is_file() else {}
    )
    errors = crop_errors(
        {
            "overview_detection": workflow["overview_detection"],
            "source_content_analysis": workflow["reference_content_analysis"],
            "units": units,
        },
        provenance,
        refs,
    )
    if errors:
        raise StyleEvidenceInvalid(
            "Native reference binding lacks physical evidence: " + "; ".join(errors)
        )
    result = deepcopy(manifest)
    log_path = run.root / "style-binding-aliases.json"
    log = read_json(log_path) if log_path.exists() else {}
    for sid in missing:
        candidates = [
            u
            for u in units
            if u.get("reference_input_id") in steps[sid].get("source_reference_ids", [])
            and sid in u.get("source_content_step_ids", [])
        ]
        if not candidates:
            raise StyleEvidenceInvalid(
                "Missing physical crop for current job step " + sid
            )
        original = candidates[0]
        path = run.path(original["path"])
        raw = path.read_bytes()
        if digest(raw) != original["sha256"]:
            raise StyleEvidenceInvalid("Original reviewed crop changed before aliasing")
        key = canonical_hash(
            {
                "job": job["id"],
                "step": sid,
                "path": str(path),
                "sha256": original["sha256"],
            }
        )
        alias = run.path("style-generation/binding-aliases/" + key + path.suffix)
        alias.parent.mkdir(parents=True, exist_ok=True)
        if alias.exists():
            if digest(alias.read_bytes()) != original["sha256"]:
                raise StyleEvidenceInvalid("Existing crop binding alias changed")
        else:
            alias.write_bytes(raw)
        # Hash the written file, rather than trusting copy success or a model hash.
        if digest(alias.read_bytes()) != original["sha256"]:
            raise StyleEvidenceInvalid("Crop alias bytes differ from reviewed crop")
        proof = {
            "job_id": job["id"],
            "source_content_step_id": sid,
            "source_path": str(path),
            "alias_path": str(alias),
            "sha256": original["sha256"],
            "pixel_review": "inherited_identical_bytes",
            "source_review_status": original["review_status"],
            "derived_from": {
                "path": str(path),
                "sha256": original["sha256"],
                "reference_input_id": original["reference_input_id"],
                "source_path": provenance[str(path)]["source_path"],
                "source_sha256": provenance[str(path)]["source_sha256"],
                "bbox": provenance[str(path)]["bbox"],
            },
        }
        row = {
            **deepcopy(original),
            "id": original["id"] + "-binding-" + key[:12],
            "path": str(alias),
            "source_crop_path": str(alias),
            "source_content_step_ids": [sid],
            "binding_alias": proof,
        }
        result["units"].append(row)
        log[key] = proof
    result["record_count"] = len(result["units"])
    write_json(log_path, log)
    return result
