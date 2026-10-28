#!/usr/bin/env python3
"""Generic product-identity contract and post-generation hard-gate helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
IDENTITY_SCHEMA = ROOT / "schemas" / "product-identity-contract-v0.1.2.schema.json"
AUDIT_SCHEMA = ROOT / "schemas" / "generated-output-audit-v0.1.2.schema.json"

# Templates declare observation slots only. They never supply product-specific values.
CATEGORY_OBSERVATION_SLOTS = {
    "FURNITURE": ["frame", "support", "seat", "legs_or_base", "connectors"],
    "HOME_APPLIANCE": ["shell", "panel", "buttons", "interfaces", "container", "accessories"],
    "ELECTRONICS": ["screen", "camera_or_sensor", "interfaces", "buttons", "logo", "accessories"],
    "BEAUTY_PERSONAL_CARE": ["container_shape", "cap_or_pump", "label", "liquid_color"],
    "FOOD": ["package_silhouette", "brand", "flavor_mark", "size_mark", "contents"],
    "APPAREL": ["silhouette", "length", "collar_or_waist", "sleeves_or_legs", "closures", "surface_pattern"],
    "GENERAL": ["main_body", "supporting_parts", "connectors", "logos", "accessories", "interfaces"],
}

CHECK_KEYS = [
    "product_identity_match",
    "color_partition_match",
    "silhouette_match",
    "component_match",
    "relative_geometry_match",
    "material_match",
    "view_evidence_match",
    "text_fact_match",
    "compliance_match",
    "page_role_match",
    "campaign_style_match",
]

ERROR_CODES = {
    "PRODUCT_COLOR_DRIFT",
    "PRODUCT_STRUCTURE_DRIFT",
    "COMPONENT_MISSING",
    "COMPONENT_INVENTED",
    "RELATIVE_GEOMETRY_DRIFT",
    "WRONG_VIEW_EVIDENCE",
    "PRODUCT_IDENTITY_INCONSISTENT_ACROSS_SET",
    "TEXT_HALLUCINATION",
    "OFFER_HALLUCINATION",
    "PAGE_ROLE_MISSING",
    "CAMPAIGN_STYLE_INCONSISTENT",
}

HARD_IDENTITY_CHECKS = {
    "product_identity_match",
    "color_partition_match",
    "silhouette_match",
    "component_match",
    "relative_geometry_match",
    "material_match",
    "view_evidence_match",
    "text_fact_match",
    "compliance_match",
}


def load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path: str | Path, value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize_category(category: str) -> str:
    value = category.lower()
    if any(x in value for x in ("家具", "椅", "桌", "床", "furniture")):
        return "FURNITURE"
    if any(x in value for x in ("小家电", "家电", "appliance")):
        return "HOME_APPLIANCE"
    if any(x in value for x in ("数码", "电子", "electronics")):
        return "ELECTRONICS"
    if any(x in value for x in ("美妆", "个护", "beauty")):
        return "BEAUTY_PERSONAL_CARE"
    if any(x in value for x in ("食品", "food")):
        return "FOOD"
    if any(x in value for x in ("服装", "服饰", "apparel", "fashion")):
        return "APPAREL"
    return "GENERAL"


def reference_records(paths: list[str], observations: dict[str, Any]) -> list[dict[str, Any]]:
    by_path = {str(Path(x.get("path", "")).resolve()): x for x in observations.get("references", [])}
    records = []
    for index, raw_path in enumerate(paths, 1):
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise ValueError(f"REFERENCE_IMAGE_MISSING:{path}")
        observed = by_path.get(str(path), {})
        records.append(
            {
                "evidence_id": f"ref-{index:02d}",
                "path": str(path),
                "sha256": sha256(path),
                "view_tags": sorted(set(observed.get("view_tags", ["UNSPECIFIED"]))),
            }
        )
    return records


def build_identity_contract(raw: dict[str, Any], observations: dict[str, Any]) -> dict[str, Any]:
    product = raw["product"]
    category_key = normalize_category(product["category"])
    identity = observations.get("identity") or {}
    required_identity = {
        "category_and_quantity": identity.get("category_and_quantity", {}),
        "silhouette_and_proportions": identity.get("silhouette_and_proportions", {}),
        "color_partitions": identity.get("color_partitions", []),
        "materials_and_surfaces": identity.get("materials_and_surfaces", []),
        "components": identity.get("components", []),
        "relative_geometry": identity.get("relative_geometry", []),
        "logos_and_labels": identity.get("logos_and_labels", []),
        "accessories_and_interfaces": identity.get("accessories_and_interfaces", []),
    }
    conflicts = list(observations.get("evidence_conflicts", []))
    coverage = observations.get("coverage", {})
    missing = [
        key
        for key in (
            "silhouette_and_proportions",
            "color_partitions",
            "materials_and_surfaces",
            "components",
            "relative_geometry",
        )
        if not required_identity[key]
    ]
    if missing:
        conflicts.append("MISSING_VISUAL_IDENTITY_FIELDS:" + ",".join(missing))
    quantity = int(identity.get("category_and_quantity", {}).get("quantity", 1))
    must_preserve = list(observations.get("must_preserve", []))
    must_not_change = list(observations.get("must_not_change", []))
    must_preserve.extend(
        [
            "preserve the exact current-product category and quantity",
            "preserve current-product silhouette, proportions, color partitions, materials, components and relative geometry",
            "preserve current-product logos, labels, accessories and interfaces exactly when present",
        ]
    )
    must_not_change.extend(
        [
            "do not replace the product with a similar or beautified model",
            "do not invent, remove, recolor or reconnect product components",
            "do not recolor the product to match the background; separate it only with background luminance, rim light and shadow",
        ]
    )
    contract = {
        "schema_version": "product-identity-contract-0.1.2",
        "contract_id": raw["request_id"] + ":product-identity",
        "product": {
            "name": product["name"],
            "category": product["category"],
            "quantity": quantity,
        },
        "reference_evidence": reference_records(
            product["same_product_reference_images"], observations
        ),
        "identity": required_identity,
        "must_preserve": sorted(set(must_preserve)),
        "must_not_change": sorted(set(must_not_change)),
        "view_specific_evidence": observations.get("view_specific_evidence", {}),
        "evidence_conflicts": sorted(set(conflicts)),
        "status": "READY"
        if not conflicts and coverage.get("semantic_visual_review") is True
        else "BLOCKED",
    }
    contract["identity"]["observation_slots_used"] = CATEGORY_OBSERVATION_SLOTS[category_key]
    jsonschema.Draft202012Validator(load(IDENTITY_SCHEMA)).validate(contract)
    return contract


def identity_prompt_block(contract: dict[str, Any], view_binding: dict[str, Any]) -> str:
    identity = contract["identity"]
    preserve = "；".join(contract["must_preserve"])
    forbid = "；".join(contract["must_not_change"])
    refs = "；".join(
        f"{x['evidence_id']}={x['path']}[{','.join(x['view_tags'])}]"
        for x in contract["reference_evidence"]
    )
    return (
        "【最高优先级 PRODUCT IDENTITY LOCK】"
        f"只允许编辑当前商品，商品身份合同={contract['contract_id']}。"
        f"原始同商品证据：{refs}。当前页视角证据绑定："
        f"{json.dumps(view_binding, ensure_ascii=False, sort_keys=True)}。"
        "previous_generated_images_used=false；严禁使用任何既往生成图作为参考。"
        "优先保留原商品像素级身份，只替换背景并增加电商版式；不得重新设计商品。"
        f"颜色分区={json.dumps(identity['color_partitions'], ensure_ascii=False)}；"
        f"部件={json.dumps(identity['components'], ensure_ascii=False)}；"
        f"相对结构={json.dumps(identity['relative_geometry'], ensure_ascii=False)}；"
        f"必须保持：{preserve}。禁止：{forbid}。"
        "文字、图标和信息卡只放在商品轮廓外。若复杂版式与商品一致性冲突，必须保商品并降低装饰复杂度。"
    )


def normalize_judgment(value: dict[str, Any]) -> dict[str, Any]:
    checks = {key: value.get("checks", {}).get(key, "FAIL") for key in CHECK_KEYS}
    checks = {key: ("PASS" if val == "PASS" else "FAIL") for key, val in checks.items()}
    errors = [x for x in value.get("errors", []) if x in ERROR_CODES]
    if any(checks[x] == "FAIL" for x in HARD_IDENTITY_CHECKS) and not errors:
        errors.append("PRODUCT_STRUCTURE_DRIFT")
    return {"checks": checks, "errors": sorted(set(errors))}


def build_audit(
    request_id: str,
    identity_contract_path: str | Path,
    generated_pages: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
    same_product_across_set: str,
) -> dict[str, Any]:
    if len(generated_pages) != len(judgments):
        raise ValueError("PAGE_JUDGMENT_COUNT_MISMATCH")
    pages = []
    for page, raw_judgment in zip(generated_pages, judgments):
        path = Path(page["generated_image"]).resolve()
        if not path.is_file():
            raise ValueError(f"GENERATED_IMAGE_MISSING:{path}")
        judgment = normalize_judgment(raw_judgment)
        blocked = any(judgment["checks"][x] == "FAIL" for x in HARD_IDENTITY_CHECKS)
        blocked = blocked or bool(judgment["errors"])
        pages.append(
            {
                "page_index": int(page["page_index"]),
                "generated_image": str(path),
                "generated_sha256": sha256(path),
                "checks": judgment["checks"],
                "errors": judgment["errors"],
                "status": "BLOCKED" if blocked else "PASS",
            }
        )
    set_errors = []
    if same_product_across_set != "PASS":
        set_errors.append("PRODUCT_IDENTITY_INCONSISTENT_ACROSS_SET")
    hard_pass = all(x["status"] == "PASS" for x in pages) and not set_errors
    audit = {
        "schema_version": "generated-output-audit-0.1.2",
        "request_id": request_id,
        "product_identity_contract_sha256": sha256(identity_contract_path),
        "pages": pages,
        "set_level": {
            "same_product_across_set": "PASS" if not set_errors else "FAIL",
            "errors": set_errors,
        },
        "hard_gate_status": "PASS" if hard_pass else "BLOCKED",
        "status": "READY_FOR_CONTROLLED_USE" if hard_pass else "BLOCKED",
        "profile01_semantics_read": False,
    }
    jsonschema.Draft202012Validator(load(AUDIT_SCHEMA)).validate(audit)
    return audit
