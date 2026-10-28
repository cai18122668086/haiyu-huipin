#!/usr/bin/env python3
"""Focused regressions for AI-led visual judgment and deterministic guards."""

from argparse import Namespace
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from harness import (
    ARTWORK_AUDIT_DIMENSIONS,
    HarnessError,
    PLAN_AUDIT_DIMENSIONS,
    VERSION,
    cmd_generate_next,
    sha256_file,
    validate_artwork_audit,
    validate_plan,
    validate_plan_audit,
    write_json,
)


def png(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big") + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00placeholder"
    )


def observation(prefix: str) -> dict:
    fields = (
        "subject_and_scale", "layout_and_reading_path", "carrier_morphology",
        "commercial_function", "camera_light_depth", "typography_structure",
        "brand_and_human_presence", "meaningful_absences", "cross_screen_role",
    )
    return {field: f"{prefix} concrete visual evidence for {field}" for field in fields}


def comparison(dimensions: tuple[str, ...], match: bool = True) -> list[dict]:
    return [
        {"dimension": dimension, "match": match, "evidence": f"distinct observed evidence for {dimension}"}
        for dimension in dimensions
    ]


def fixture(root: Path) -> tuple[dict, dict, Path, Path]:
    product, u1, u2 = root / "product.png", root / "u1.png", root / "u2.png"
    png(product, 600, 1000)
    png(u1, 600, 1000)
    png(u2, 500, 1000)
    session = {"input": {"fidelity_mode": "REMIX_STRICT", "product_images": [str(product)]}}
    units = [
        {"id": "U01", "crop_path": str(u1), "crop_sha256": sha256_file(u1), "pixel_width": 600, "pixel_height": 1000, "column_index": 0, "screen_index": 0, "screen_count_in_column": 2, "screen_boundary_reason": "first viewport-level composition starts at the column top", "ai_reference_observation": observation("plan-u1")},
        {"id": "U02", "crop_path": str(u2), "crop_sha256": sha256_file(u2), "pixel_width": 500, "pixel_height": 1000, "column_index": 0, "screen_index": 1, "screen_count_in_column": 2, "screen_boundary_reason": "second dominant headline resets the composition", "ai_reference_observation": observation("plan-u2")},
    ]
    modules = [
        {"id": "M01", "reference_unit_id": "U01", "reference_path": str(u1), "output_canvas": {"width": 600, "height": 1000}, "visual_contract": observation("contract-u1"), "page_duty": "first page visual hook", "buyer_conclusion": "easy one-touch drinking", "generation_brief": "replicate the first crop exactly", "copy_units": ["一键弹开"]},
        {"id": "M02", "reference_unit_id": "U02", "reference_path": str(u2), "output_canvas": {"width": 512, "height": 1024}, "visual_contract": observation("contract-u2"), "page_duty": "second page trust proof", "buyer_conclusion": "material proof feels credible", "generation_brief": "preserve one continuous proof band", "copy_units": ["316不锈钢"]},
    ]
    plan = {"plan_contract_version": "6", "fidelity_mode": "REMIX_STRICT", "reference_units": units, "sequence_contract": {"segmentation_basis": "SCREEN_NOT_COLUMN", "reading_order": "COLUMN_MAJOR_SCREEN_TOP_TO_BOTTOM", "declared_screen_count": 2, "source_unit_order": ["U01", "U02"]}, "modules": modules}
    return session, plan, u1, product


def check_column_screen_order_is_programmatic() -> None:
    with TemporaryDirectory() as temp:
        session, plan, _, _ = fixture(Path(temp))
        broken = deepcopy(plan)
        broken["sequence_contract"]["source_unit_order"] = ["U02", "U01"]
        try:
            validate_plan(broken, session)
        except HarnessError as exc:
            assert "column-major screen" in str(exc)
            return
        raise AssertionError("row/selected order must not pass")


def check_declared_screens_cannot_collapse_to_one_column_crop() -> None:
    with TemporaryDirectory() as temp:
        session, plan, _, _ = fixture(Path(temp))
        broken = deepcopy(plan)
        broken["reference_units"] = broken["reference_units"][:1]
        broken["modules"] = broken["modules"][:1]
        broken["sequence_contract"]["declared_screen_count"] = 1
        broken["sequence_contract"]["source_unit_order"] = ["U01"]
        try:
            validate_plan(broken, session)
        except HarnessError as exc:
            assert "every screen_index" in str(exc)
            return
        raise AssertionError("one whole-column crop must not replace two declared screens")


def check_each_crop_keeps_its_ratio() -> None:
    with TemporaryDirectory() as temp:
        session, plan, _, _ = fixture(Path(temp))
        plan["modules"][0]["output_canvas"] = {"width": 750, "height": 1000}
        try:
            validate_plan(plan, session)
        except HarnessError as exc:
            assert "crop ratio" in str(exc)
            return
        raise AssertionError("generic 3:4 canvas must not replace a tall crop")


def check_plan_audit_must_be_fresh() -> None:
    with TemporaryDirectory() as temp:
        session_base, plan, _, _ = fixture(Path(temp))
        plan = validate_plan(plan, session_base)
        session = {"plan": plan}
        audits = []
        for unit in plan["reference_units"]:
            audits.append({"unit_id": unit["id"], "reference_path": unit["crop_path"], "reference_sha256": unit["crop_sha256"], "fresh_pixel_observation": deepcopy(unit["ai_reference_observation"]), "comparisons": comparison(PLAN_AUDIT_DIMENSIONS), "material_mismatches": [], "decision": "PASS"})
        try:
            validate_plan_audit({"method": "AI_FRESH_REFERENCE_AUDIT", "unit_audits": audits}, session)
        except HarnessError as exc:
            assert "copied" in str(exc)
            return
        raise AssertionError("copied planning observations must not self-certify")


def check_visual_mismatch_cannot_be_overridden() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        session_base, plan, reference, _ = fixture(root)
        module = validate_plan(plan, session_base)["modules"][0]
        artwork = root / "artwork.png"
        png(artwork, 600, 1000)
        checks = comparison(ARTWORK_AUDIT_DIMENSIONS)
        checks[3]["match"] = False
        judgment = {"method": "AI_FRESH_SIDE_BY_SIDE_PIXEL_AUDIT", "reference_path": str(reference), "artwork_path": str(artwork), "reference_sha256": sha256_file(reference), "artwork_sha256": sha256_file(artwork), "fresh_reference_observation": observation("fresh-reference"), "fresh_artwork_observation": observation("fresh-artwork"), "comparisons": checks, "major_mismatches": ["continuous proof band became generic cards"], "decision": "PASS"}
        try:
            validate_artwork_audit(judgment, module, str(artwork))
        except HarnessError as exc:
            assert "conflicts" in str(exc)
            return
        raise AssertionError("optimistic PASS must not override observed mismatch")


def check_only_current_module_can_generate() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        base, plan, _, product = fixture(root)
        plan = validate_plan(plan, base)
        session_path, request_path = root / "session.json", root / "request.json"
        state = {"runtime_version": VERSION, "state": "READY", "input": {"product_images": [str(product)]}, "plan": plan, "module_index": 1, "modules": [{"id": "M01", "status": "PENDING", "attempts": []}, {"id": "M02", "status": "PENDING", "attempts": []}], "current": None}
        write_json(session_path, state)
        try:
            cmd_generate_next(Namespace(session=str(session_path), out=str(request_path)))
        except HarnessError as exc:
            assert "skip" in str(exc)
            return
        raise AssertionError("later module must not generate while the first is pending")


if __name__ == "__main__":
    check_column_screen_order_is_programmatic()
    check_declared_screens_cannot_collapse_to_one_column_crop()
    check_each_crop_keeps_its_ratio()
    check_plan_audit_must_be_fresh()
    check_visual_mismatch_cannot_be_overridden()
    check_only_current_module_can_generate()
    print("v6 screen-first runtime: 6/6 passed")
