#!/usr/bin/env python3
"""Lightweight state guard for AI-led benchmark visual remixing.

The AI owns visual understanding and side-by-side judgment. This runtime owns
only deterministic invariants: immutable sequence, one active token, file and
aspect-ratio binding, bounded repair, and accepted-only delivery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "6.0.0"
PLAN_CONTRACT_VERSION = "6"
FIDELITY_MODES = {"REMIX_STRICT", "REMIX_BALANCED", "STYLE_INSPIRED"}
OBSERVATION_FIELDS = (
    "subject_and_scale", "layout_and_reading_path", "carrier_morphology",
    "commercial_function", "camera_light_depth", "typography_structure",
    "brand_and_human_presence", "meaningful_absences", "cross_screen_role",
)
PLAN_AUDIT_DIMENSIONS = (
    "layout_geometry", "carrier_morphology", "commercial_function", "expression",
    "brand_human_presence", "typography_structure", "meaningful_absences", "aspect_ratio",
)
ARTWORK_AUDIT_DIMENSIONS = (
    "product_identity", "subject_and_scale", "layout_and_reading_path",
    "carrier_morphology", "commercial_function", "camera_light_depth",
    "typography_structure", "brand_and_human_presence", "meaningful_absences",
    "copy_and_claim_safety",
)
ACCEPTED = {"COMPLETE", "COMPLETE_WITH_RESIDUALS"}
TERMINAL = ACCEPTED | {"REJECTED_BEST_EFFORT"}


class HarnessError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"Cannot read JSON {p}: {exc}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"Expected a JSON object: {p}")
    return value


def write_json(path: str | Path, value: dict[str, Any]) -> None:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_text(value: Any, label: str, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise HarnessError(f"{label} must contain at least {minimum} meaningful characters")
    return value.strip()


def require_file(value: Any, label: str) -> str:
    p = Path(require_text(value, label)).expanduser().resolve()
    if not p.is_file() or p.stat().st_size <= 0:
        raise HarnessError(f"{label} does not exist or is empty: {p}")
    return str(p)


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise HarnessError(f"{label} must be an array")
    return value


def validate_observation(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise HarnessError(f"{label} must be an object written from actual pixel observation")
    result = {field: require_text(value.get(field), f"{label}.{field}", 8) for field in OBSERVATION_FIELDS}
    if len(set(result.values())) != len(result):
        raise HarnessError(f"{label} repeats generic observations; describe each visual dimension independently")
    return result


def image_dimensions(path: str | Path) -> tuple[int, int]:
    data = Path(path).read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        index = 2
        sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                break
            length = int.from_bytes(data[index:index + 2], "big")
            if marker in sof and index + 7 < len(data):
                return int.from_bytes(data[index + 5:index + 7], "big"), int.from_bytes(data[index + 3:index + 5], "big")
            if length < 2:
                break
            index += length
    raise HarnessError("Artwork must be a readable PNG or JPEG")


def load_session(path: str | Path) -> dict[str, Any]:
    session = read_json(path)
    if session.get("runtime_version") != VERSION:
        raise HarnessError("Unsupported or stale session version; start a fresh v6 session")
    return session


def save_session(path: str | Path, session: dict[str, Any]) -> None:
    session["updated_at"] = now()
    write_json(path, session)


def validate_input(value: dict[str, Any]) -> dict[str, Any]:
    products = [require_file(x, f"product_images[{i}]") for i, x in enumerate(require_list(value.get("product_images"), "product_images"))]
    references = [require_file(x, f"reference_images[{i}]") for i, x in enumerate(require_list(value.get("reference_images"), "reference_images"))]
    if not products or not references:
        raise HarnessError("At least one product image and one benchmark image are required")
    mode = value.get("fidelity_mode", "REMIX_STRICT")
    if mode not in FIDELITY_MODES:
        raise HarnessError("fidelity_mode is invalid")
    points = value.get("selling_points", [])
    if not isinstance(points, list) or not all(isinstance(x, str) and x.strip() for x in points):
        raise HarnessError("selling_points must be an array of non-empty strings")
    geometry = value.get("product_geometry", [])
    if geometry is None:
        geometry = []
    if not isinstance(geometry, list) or not all(isinstance(x, str) and x.strip() for x in geometry):
        raise HarnessError("product_geometry must be an array of non-empty strings describing the real product form")
    return {
        "product_name": require_text(value.get("product_name"), "product_name"),
        "product_images": products,
        "reference_images": references,
        "selling_points": [x.strip() for x in points],
        "product_geometry": [x.strip() for x in geometry],
        "input_mode": "USER_FACTS" if points else "IMAGE_ONLY_INFERENCE",
        "campaign_type": value.get("campaign_type", "AUTO"),
        "fidelity_mode": mode,
    }


def validate_plan(value: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    if value.get("plan_contract_version") != PLAN_CONTRACT_VERSION:
        raise HarnessError(f"plan_contract_version must be {PLAN_CONTRACT_VERSION}")
    if value.get("fidelity_mode") != session["input"]["fidelity_mode"]:
        raise HarnessError("Plan fidelity mode must match input")
    units = require_list(value.get("reference_units"), "reference_units")
    if not units:
        raise HarnessError("reference_units cannot be empty")
    unit_by_id: dict[str, dict[str, Any]] = {}
    positions: dict[str, tuple[int, int]] = {}
    declared_counts: dict[int, int] = {}
    screens_by_column: dict[int, list[int]] = {}
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise HarnessError(f"reference_units[{index}] must be an object")
        unit_id = require_text(unit.get("id"), f"reference_units[{index}].id")
        if unit_id in unit_by_id:
            raise HarnessError(f"Duplicate reference unit: {unit_id}")
        unit["crop_path"] = require_file(unit.get("crop_path"), f"{unit_id}.crop_path")
        if unit.get("crop_sha256") != sha256_file(unit["crop_path"]):
            raise HarnessError(f"{unit_id}.crop_sha256 does not match physical crop")
        width, height = image_dimensions(unit["crop_path"])
        if (unit.get("pixel_width"), unit.get("pixel_height")) != (width, height):
            raise HarnessError(f"{unit_id} declared dimensions do not match physical crop")
        column, screen = unit.get("column_index"), unit.get("screen_index")
        count = unit.get("screen_count_in_column")
        if not isinstance(column, int) or column < 0 or not isinstance(screen, int) or screen < 0:
            raise HarnessError(f"{unit_id} needs non-negative column_index/screen_index")
        if not isinstance(count, int) or count < 1:
            raise HarnessError(f"{unit_id}.screen_count_in_column must be positive")
        require_text(unit.get("screen_boundary_reason"), f"{unit_id}.screen_boundary_reason", 12)
        if column in declared_counts and declared_counts[column] != count:
            raise HarnessError(f"Column {column} has conflicting screen_count_in_column values")
        declared_counts[column] = count
        screens_by_column.setdefault(column, []).append(screen)
        unit["ai_reference_observation"] = validate_observation(unit.get("ai_reference_observation"), f"{unit_id}.ai_reference_observation")
        unit_by_id[unit_id] = unit
        positions[unit_id] = (column, screen)
    for column, count in declared_counts.items():
        actual = sorted(screens_by_column[column])
        expected = list(range(count))
        if actual != expected:
            raise HarnessError(f"Column {column} must contain every screen_index exactly once: {expected}")
    expected_order = [x[0] for x in sorted(positions.items(), key=lambda item: item[1])]
    sequence = value.get("sequence_contract")
    if not isinstance(sequence, dict) or sequence.get("segmentation_basis") != "SCREEN_NOT_COLUMN":
        raise HarnessError("sequence_contract.segmentation_basis must be SCREEN_NOT_COLUMN")
    if sequence.get("reading_order") != "COLUMN_MAJOR_SCREEN_TOP_TO_BOTTOM":
        raise HarnessError("sequence_contract.reading_order must be COLUMN_MAJOR_SCREEN_TOP_TO_BOTTOM")
    if sequence.get("declared_screen_count") != len(units):
        raise HarnessError("sequence_contract.declared_screen_count must equal the number of screen crops")
    if sequence.get("source_unit_order") != expected_order:
        raise HarnessError(f"source_unit_order must be exact column-major screen order: {expected_order}")
    modules = require_list(value.get("modules"), "modules")
    if len(modules) != len(expected_order):
        raise HarnessError("Exactly one module is required for every reference unit")
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise HarnessError(f"modules[{index}] must be an object")
        module_id = require_text(module.get("id"), f"modules[{index}].id")
        unit_id = require_text(module.get("reference_unit_id"), f"{module_id}.reference_unit_id")
        if unit_id != expected_order[index]:
            raise HarnessError(f"{module_id} is out of order; expected {expected_order[index]}")
        module["reference_path"] = require_file(module.get("reference_path"), f"{module_id}.reference_path")
        if module["reference_path"] != unit_by_id[unit_id]["crop_path"]:
            raise HarnessError(f"{module_id} must use only its scheduled physical crop")
        canvas = module.get("output_canvas")
        if not isinstance(canvas, dict) or any(not isinstance(canvas.get(x), int) or canvas[x] < 512 for x in ("width", "height")):
            raise HarnessError(f"{module_id}.output_canvas must contain width/height >= 512")
        reference_ratio = unit_by_id[unit_id]["pixel_width"] / unit_by_id[unit_id]["pixel_height"]
        if abs(canvas["width"] / canvas["height"] - reference_ratio) / reference_ratio > 0.01:
            raise HarnessError(f"{module_id}.output_canvas must preserve reference crop ratio within 1%")
        module["reference_aspect_ratio"] = reference_ratio
        module["visual_contract"] = validate_observation(module.get("visual_contract"), f"{module_id}.visual_contract")
        for field in ("page_duty", "buyer_conclusion", "generation_brief"):
            require_text(module.get(field), f"{module_id}.{field}", 8)
        copy = require_list(module.get("copy_units"), f"{module_id}.copy_units")
        if not all(isinstance(x, str) and x.strip() for x in copy):
            raise HarnessError(f"{module_id}.copy_units must contain approved strings only")
    result = dict(value)
    result["reference_units"], result["modules"] = units, modules
    return result


def validate_dimension_comparisons(value: Any, expected: tuple[str, ...], label: str) -> list[dict[str, Any]]:
    items = require_list(value, label)
    if [x.get("dimension") for x in items if isinstance(x, dict)] != list(expected):
        raise HarnessError(f"{label} must contain every dimension once in required order")
    evidences = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("match"), bool):
            raise HarnessError(f"{label}[{index}] requires a boolean match")
        evidences.append(require_text(item.get("evidence"), f"{label}[{index}].evidence", 12))
    if len(set(evidences)) != len(evidences):
        raise HarnessError(f"{label} repeats evidence; inspect each dimension independently")
    return items


def validate_plan_audit(value: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    if value.get("method") != "AI_FRESH_REFERENCE_AUDIT":
        raise HarnessError("Plan audit method must be AI_FRESH_REFERENCE_AUDIT")
    units = {x["id"]: x for x in session["plan"]["reference_units"]}
    audits = require_list(value.get("unit_audits"), "unit_audits")
    expected = session["plan"]["sequence_contract"]["source_unit_order"]
    if [x.get("unit_id") for x in audits if isinstance(x, dict)] != expected:
        raise HarnessError("Plan audit must inspect every screen crop in exact column-major screen order")
    for audit in audits:
        unit = units[audit["unit_id"]]
        if require_file(audit.get("reference_path"), "plan audit reference_path") != unit["crop_path"]:
            raise HarnessError("Plan audit used the wrong crop")
        if audit.get("reference_sha256") != unit["crop_sha256"]:
            raise HarnessError("Plan audit crop hash is stale")
        fresh = validate_observation(audit.get("fresh_pixel_observation"), f"{audit['unit_id']}.fresh_pixel_observation")
        if fresh == unit["ai_reference_observation"]:
            raise HarnessError("Plan audit copied the planning observation verbatim; perform a fresh visual pass")
        comparisons = validate_dimension_comparisons(audit.get("comparisons"), PLAN_AUDIT_DIMENSIONS, "plan audit comparisons")
        mismatches = require_list(audit.get("material_mismatches"), "plan audit material_mismatches")
        derived = "PASS" if all(x["match"] for x in comparisons) and not mismatches else "FAIL"
        if audit.get("decision") != derived:
            raise HarnessError("Plan audit decision conflicts with its observed comparisons")
        if derived != "PASS":
            raise HarnessError(f"Plan audit failed for {audit['unit_id']}; re-observe and recompile")
    return value


def compile_prompt(session: dict[str, Any], module: dict[str, Any], repair: bool) -> str:
    parts = [
        "Create ONE complete Chinese ecommerce visual for the current token only.",
        "Reference image 1 is the binding composition blueprint; product images define product identity.",
    ]
    product_geometry = session.get("input", {}).get("product_geometry") or []
    if product_geometry:
        parts.append(
            "PRODUCT GEOMETRY LOCK (binding, must be reproduced exactly, do not substitute a generic product): "
            + "；".join(product_geometry)
        )
    parts += [
        "AI-observed reference facts: " + json.dumps(module["visual_contract"], ensure_ascii=False),
        "Page duty: " + module["page_duty"], "Buyer conclusion: " + module["buyer_conclusion"],
        "Generation brief: " + module["generation_brief"],
        "Approved visible copy only: " + json.dumps(module["copy_units"], ensure_ascii=False),
        "Reproduce subject scale, layout, carrier morphology, commercial function, camera/light/depth, typography silhouette, presence/absence, and cross-screen role one-to-one.",
        "Do not turn continuous evidence, comparison, pain-solution, report, or exploded structures into generic repeated cards.",
        "Do not add a standalone brand when the observed reference has none. Never copy benchmark branding, watermark, celebrity identity, exact copy, or unsupported claims.",
        f"Target canvas {module['output_canvas']['width']}×{module['output_canvas']['height']} with the reference crop aspect ratio.",
        "Do not generate, preview, save, or assemble any later module before this exact page is bound and audited.",
    ]
    if repair:
        parts.insert(0, "EDIT the supplied failed complete visual once. Preserve passed pixels and fix every listed mismatch together.")
        parts.append("Required repair deltas: " + json.dumps(session["current"]["repair_plan"], ensure_ascii=False))
    return "\n".join(parts)


def cmd_start(args: argparse.Namespace) -> None:
    path = Path(args.session).resolve()
    if path.exists():
        raise HarnessError(f"Refusing to overwrite existing session: {path}")
    session = {"runtime_version": VERSION, "created_at": now(), "updated_at": now(), "state": "STARTED", "input": validate_input(read_json(args.input)), "plan": None, "module_index": 0, "modules": [], "current": None}
    write_json(path, session)
    print(json.dumps({"state": "STARTED", "session": str(path)}, ensure_ascii=False))


def cmd_compile_plan(args: argparse.Namespace) -> None:
    session = load_session(args.session)
    if session["state"] not in {"STARTED", "PLAN_REJECTED"}:
        raise HarnessError("compile-plan is legal only before generation")
    session["plan"] = validate_plan(read_json(args.plan), session)
    session["plan_sha256"] = sha256_file(args.plan)
    session["module_index"] = 0
    session["modules"] = [{"id": x["id"], "status": "PENDING", "attempts": []} for x in session["plan"]["modules"]]
    session["current"] = None
    session["state"] = "PLAN_AUDIT_REQUIRED"
    save_session(args.session, session)
    request = {"method": "AI_FRESH_REFERENCE_AUDIT", "plan_sha256": session["plan_sha256"], "instructions": "Open every crop. Ignore plan wording first, write a fresh pixel observation, then compare it with the declared contract. No copied all-pass template.", "units": [{"unit_id": u["id"], "reference_path": u["crop_path"], "reference_sha256": u["crop_sha256"]} for u in session["plan"]["reference_units"]]}
    out = Path(args.session).resolve().with_suffix(".plan-audit-request.json")
    write_json(out, request)
    print(json.dumps({"state": session["state"], "plan_audit_request": str(out)}, ensure_ascii=False))


def cmd_audit_plan(args: argparse.Namespace) -> None:
    session = load_session(args.session)
    if session["state"] != "PLAN_AUDIT_REQUIRED":
        raise HarnessError(f"audit-plan is illegal in state {session['state']}")
    try:
        session["plan_audit"] = validate_plan_audit(read_json(args.judgment), session)
    except HarnessError:
        session["state"] = "PLAN_REJECTED"
        save_session(args.session, session)
        raise
    session["state"] = "READY"
    save_session(args.session, session)
    print(json.dumps({"state": "READY", "next_module": session["modules"][0]["id"]}, ensure_ascii=False))


def cmd_generate_next(args: argparse.Namespace) -> None:
    session = load_session(args.session)
    if session["state"] not in {"READY", "REPAIR_PREPARED"}:
        raise HarnessError(f"generate-next is illegal in state {session['state']}")
    index = session["module_index"]
    if index >= len(session["modules"]):
        raise HarnessError("No unfinished module")
    if any(x["status"] not in TERMINAL for x in session["modules"][:index]):
        raise HarnessError("Cannot skip an earlier screen module")
    record, module = session["modules"][index], session["plan"]["modules"][index]
    expected_unit = session["plan"]["sequence_contract"]["source_unit_order"][index]
    if record["id"] != module["id"] or module["reference_unit_id"] != expected_unit:
        raise HarnessError("Current module does not match immutable sequence")
    repair = session["state"] == "REPAIR_PREPARED"
    attempt_number = len(record["attempts"]) + 1
    if attempt_number > 2:
        raise HarnessError("Shared repair budget is exhausted")
    token = str(uuid.uuid4())
    references = [module["reference_path"]] + session["input"]["product_images"]
    if repair:
        references.insert(0, record["attempts"][-1]["artwork"])
    request = {"token": token, "sequence_index": index, "module_id": module["id"], "reference_unit_id": expected_unit, "attempt": attempt_number, "mode": "REPAIR" if repair else "INITIAL", "prompt": compile_prompt(session, module, repair), "reference_images": references, "canvas": module["output_canvas"]}
    record["attempts"].append({"number": attempt_number, "token": token, "status": "AWAITING_ARTWORK", "artwork": None, "judgment": None})
    session["current"] = {"token": token, "sequence_index": index, "module_id": module["id"], "reference_unit_id": expected_unit, "repair_plan": session.get("current", {}).get("repair_plan") if repair else None}
    session["state"] = "AWAITING_ARTWORK"
    write_json(args.out, request)
    save_session(args.session, session)
    print(json.dumps({"state": session["state"], "token": token, "module_id": module["id"]}, ensure_ascii=False))


def current_attempt(session: dict[str, Any], token: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    current, index = session.get("current"), session["module_index"]
    if not current or current.get("token") != token or current.get("sequence_index") != index:
        raise HarnessError("Stale token or wrong sequence slot")
    module, record = session["plan"]["modules"][index], session["modules"][index]
    if current.get("module_id") != module["id"] or current.get("reference_unit_id") != module["reference_unit_id"]:
        raise HarnessError("Token is bound to a different module/reference unit")
    attempt = record["attempts"][-1]
    if attempt["token"] != token:
        raise HarnessError("Attempt token mismatch")
    return module, record, attempt


def cmd_record_artwork(args: argparse.Namespace) -> None:
    session = load_session(args.session)
    if session["state"] != "AWAITING_ARTWORK":
        raise HarnessError(f"record-artwork is illegal in state {session['state']}")
    module, _, attempt = current_attempt(session, args.token)
    artwork = require_file(args.image, "complete visual")
    width, height = image_dimensions(artwork)
    if abs(width / height - module["reference_aspect_ratio"]) / module["reference_aspect_ratio"] > 0.01:
        raise HarnessError("Artwork uses the wrong aspect ratio; generic campaign canvases cannot replace the scheduled crop")
    attempt.update({"artwork": artwork, "pixel_width": width, "pixel_height": height, "status": "AWAITING_AUDIT"})
    session["state"] = "AWAITING_AUDIT"
    save_session(args.session, session)
    print(json.dumps({"state": session["state"], "artwork": artwork}, ensure_ascii=False))


def validate_artwork_audit(value: dict[str, Any], module: dict[str, Any], artwork: str, product_geometry: list[str] | None = None) -> tuple[dict[str, Any], str]:
    if value.get("method") != "AI_FRESH_SIDE_BY_SIDE_PIXEL_AUDIT":
        raise HarnessError("Artwork audit must use AI_FRESH_SIDE_BY_SIDE_PIXEL_AUDIT")
    expected_artwork = str(Path(artwork).expanduser().resolve())
    if require_file(value.get("reference_path"), "audit reference_path") != module["reference_path"] or require_file(value.get("artwork_path"), "audit artwork_path") != expected_artwork:
        raise HarnessError("Audit used the wrong reference or artwork")
    if value.get("reference_sha256") != sha256_file(module["reference_path"]) or value.get("artwork_sha256") != sha256_file(expected_artwork):
        raise HarnessError("Audit hashes do not match viewed pixels")
    reference_observation = validate_observation(value.get("fresh_reference_observation"), "fresh_reference_observation")
    artwork_observation = validate_observation(value.get("fresh_artwork_observation"), "fresh_artwork_observation")
    if reference_observation == module["visual_contract"]:
        raise HarnessError("Audit copied the plan contract instead of freshly observing the reference")
    if artwork_observation == reference_observation:
        raise HarnessError("Reference and artwork observations are verbatim duplicates; describe actual output pixels")
    comparisons = validate_dimension_comparisons(value.get("comparisons"), ARTWORK_AUDIT_DIMENSIONS, "artwork comparisons")
    mismatches = require_list(value.get("major_mismatches"), "major_mismatches")
    for index, mismatch in enumerate(mismatches):
        require_text(mismatch, f"major_mismatches[{index}]", 8)
    if product_geometry:
        geometry_match = value.get("product_geometry_match")
        if not isinstance(geometry_match, bool) or (geometry_match is False and not mismatches):
            raise HarnessError(
                "product_geometry is locked for this run; the artwork audit must explicitly confirm "
                "product_geometry_match=true against the locked geometry, or list geometry deviations in major_mismatches"
            )
    derived = "PASS" if all(x["match"] for x in comparisons) and not mismatches else "FAIL"
    if value.get("decision") != derived:
        raise HarnessError("Artwork decision conflicts with observed comparisons")
    value["derived_decision"] = derived
    return value, derived


def advance(session: dict[str, Any], status: str) -> None:
    session["modules"][session["module_index"]]["status"] = status
    session["module_index"] += 1
    session["current"] = None
    session["state"] = "SET_COMPLETE" if session["module_index"] >= len(session["modules"]) else "READY"


def cmd_audit_current(args: argparse.Namespace) -> None:
    session = load_session(args.session)
    if session["state"] != "AWAITING_AUDIT":
        raise HarnessError(f"audit-current is illegal in state {session['state']}")
    module, record, attempt = current_attempt(session, session["current"]["token"])
    judgment, decision = validate_artwork_audit(read_json(args.judgment), module, attempt["artwork"], session.get("input", {}).get("product_geometry"))
    attempt["judgment"] = judgment
    if decision == "PASS":
        attempt["status"] = "COMPLETE"
        advance(session, "COMPLETE")
    elif len(record["attempts"]) >= 2:
        attempt["status"] = "REJECTED_BEST_EFFORT"
        record["residuals"] = judgment["major_mismatches"]
        advance(session, "REJECTED_BEST_EFFORT")
    else:
        attempt["status"] = "REPAIR_REQUIRED"
        session["state"] = "REPAIR_READY"
    save_session(args.session, session)
    print(json.dumps({"state": session["state"], "module_status": record["status"]}, ensure_ascii=False))


def cmd_prepare_repair(args: argparse.Namespace) -> None:
    session = load_session(args.session)
    if session["state"] != "REPAIR_READY":
        raise HarnessError(f"prepare-repair is illegal in state {session['state']}")
    repair = read_json(args.repair_plan)
    failures = require_list(repair.get("material_failures"), "material_failures")
    expected = session["modules"][session["module_index"]]["attempts"][-1]["judgment"]["major_mismatches"]
    if failures != expected:
        raise HarnessError("Repair plan must include every observed major mismatch in order")
    require_text(repair.get("single_edit_instruction"), "single_edit_instruction", 12)
    session["current"]["repair_plan"] = repair
    session["state"] = "REPAIR_PREPARED"
    save_session(args.session, session)
    print(json.dumps({"state": session["state"], "failure_count": len(failures)}, ensure_ascii=False))


def cmd_inspect(args: argparse.Namespace) -> None:
    session = load_session(args.session)
    print(json.dumps({"state": session["state"], "module_index": session["module_index"], "module_count": len(session["modules"]), "current": session.get("current"), "module_statuses": [{"id": x["id"], "status": x["status"], "attempts": len(x["attempts"])} for x in session["modules"]]}, ensure_ascii=False, indent=2))


def cmd_finalize(args: argparse.Namespace) -> None:
    session = load_session(args.session)
    if session["state"] != "SET_COMPLETE":
        raise HarnessError(f"finalize is illegal in state {session['state']}")
    rejected = [x["id"] for x in session["modules"] if x["status"] not in ACCEPTED]
    if rejected:
        raise HarnessError("Strict delivery blocked by rejected modules: " + ", ".join(rejected))
    outputs = []
    for index, record in enumerate(session["modules"]):
        outputs.append({"sequence_index": index, "module_id": record["id"], "reference_unit_id": session["plan"]["modules"][index]["reference_unit_id"], "status": record["status"], "artwork": record["attempts"][-1]["artwork"]})
    manifest = {"runtime_version": VERSION, "product_name": session["input"]["product_name"], "reading_order": "COLUMN_MAJOR_SCREEN_TOP_TO_BOTTOM", "outputs": outputs, "completed_at": now()}
    write_json(args.output, manifest)
    session["manifest"], session["state"] = str(Path(args.output).resolve()), "FINALIZED"
    save_session(args.session, session)
    print(json.dumps({"state": session["state"], "manifest": session["manifest"]}, ensure_ascii=False))


def cmd_prepare_overview(args: argparse.Namespace) -> None:
    session = load_session(args.session)
    if session["state"] != "FINALIZED":
        raise HarnessError("Overview inputs are available only after strict finalization")
    outputs = read_json(session["manifest"]).get("outputs", [])
    expected = [x["id"] for x in session["modules"]]
    if [x.get("module_id") for x in outputs] != expected or any(x.get("status") not in ACCEPTED for x in outputs):
        raise HarnessError("Overview cannot contain skipped, reordered, pending, or rejected pages")
    request = {"reading_order": "COLUMN_MAJOR_SCREEN_TOP_TO_BOTTOM", "module_ids": expected, "ordered_images": [x["artwork"] for x in outputs], "instruction": "Use every screen image exactly once in this exact order; do not sample or add stray files."}
    write_json(args.out, request)
    print(json.dumps({"overview_request": str(Path(args.out).resolve()), "image_count": len(outputs)}, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AI-led reference remix state guard")
    sub = p.add_subparsers(dest="command", required=True)
    specs = {
        "start": (cmd_start, ("--input", "--session")), "compile-plan": (cmd_compile_plan, ("--session", "--plan")),
        "audit-plan": (cmd_audit_plan, ("--session", "--judgment")), "generate-next": (cmd_generate_next, ("--session", "--out")),
        "record-artwork": (cmd_record_artwork, ("--session", "--token", "--image")), "audit-current": (cmd_audit_current, ("--session", "--judgment")),
        "prepare-repair": (cmd_prepare_repair, ("--session", "--repair-plan")), "inspect": (cmd_inspect, ("--session",)),
        "finalize": (cmd_finalize, ("--session", "--output")), "prepare-overview": (cmd_prepare_overview, ("--session", "--out")),
    }
    for name, (func, arguments) in specs.items():
        item = sub.add_parser(name)
        for argument in arguments:
            item.add_argument(argument, required=True)
        item.set_defaults(func=func)
    return p


def main() -> int:
    try:
        args = parser().parse_args()
        args.func(args)
        return 0
    except HarnessError as exc:
        print(f"ERROR: {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
