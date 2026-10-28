"""Bind model schemas to existing source regions; never rewrite model proposals."""

from collections import Counter
from copy import deepcopy
from typing import Any


def scope_errors(raw: Any, data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw, dict) or not isinstance(raw.get("modules"), list):
        return errors
    for module in raw["modules"]:
        if not isinstance(module, dict):
            continue
        uid = module.get("reference_unit_id")
        if not isinstance(uid, str) or uid not in data["reference_layouts"]:
            continue
        expected = {r["id"] for r in data["reference_layouts"][uid]["text_regions"]}
        for field in ("text_bindings", "fact_adaptations", "content_groups"):
            rows = module.get(field)
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                continue
            ids = (
                [rid for row in rows for rid in row.get("region_ids", [])]
                if field == "content_groups"
                and all(isinstance(r.get("region_ids"), list) for r in rows)
                else [row.get("region_id") for row in rows]
            )
            if not all(isinstance(rid, str) for rid in ids):
                continue
            counts = Counter(ids)
            missing, extra = (
                sorted(expected - counts.keys()),
                sorted(counts.keys() - expected),
            )
            duplicates = sorted(rid for rid, n in counts.items() if n > 1)
            if missing or extra or duplicates:
                errors.append(
                    f"{uid}.{field}: missing existing IDs={missing}; "
                    f"unexpected IDs={extra}; duplicate IDs={duplicates}. "
                    "Return every original region once, without renaming or splitting."
                )
    return errors


def _complete_ids(base: dict[str, Any], key: str, ids: list[Any]) -> dict[str, Any]:
    value = deepcopy(base)
    value.update(minItems=len(ids), maxItems=len(ids))
    if not ids:
        value["items"] = False
        return value
    value["items"].setdefault("allOf", []).append(
        {"properties": {key: {"enum": ids}}, "required": [key]}
    )
    value["allOf"] = [
        {
            "contains": {"properties": {key: {"const": identity}}, "required": [key]},
            "minContains": 1,
            "maxContains": 1,
        }
        for identity in ids
    ]
    return value


def bind_plan_schema(schema: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(schema)
    modules = schema["properties"]["modules"]
    units = data["_proposal_units"]
    modules.update(minItems=len(units), maxItems=len(units))
    modules["items"]["properties"]["reference_unit_id"]["enum"] = [
        u["id"] for u in units
    ]
    base = modules["items"]["properties"]
    rules = []
    for unit in units:
        layout = data["reference_layouts"][unit["id"]]
        ids = [r["id"] for r in layout["text_regions"]]
        if len(ids) != len(set(ids)):
            raise ValueError("Original reference text-region IDs must be unique")
        groups = deepcopy(base["content_groups"])
        if ids:
            regions = groups["items"]["properties"]["region_ids"]
            regions.update(uniqueItems=True, maxItems=len(ids))
            regions["items"]["enum"] = ids
            groups["allOf"] = [
                {
                    "contains": {
                        "properties": {"region_ids": {"contains": {"const": rid}}},
                        "required": ["region_ids"],
                    },
                    "minContains": 1,
                    "maxContains": 1,
                }
                for rid in ids
            ]
        else:
            groups.update(minItems=0, maxItems=0, items=False)
        properties = {
            key: _complete_ids(base[key], "region_id", ids)
            for key in ("text_bindings", "fact_adaptations")
        }
        properties["content_groups"] = groups
        properties["render_anchors"] = _complete_ids(
            base["render_anchors"],
            "source_anchor_index",
            list(range(len(layout["layout_anchors"]))),
        )
        rules.append(
            {
                "if": {"properties": {"reference_unit_id": {"const": unit["id"]}}},
                "then": {"properties": properties},
            }
        )
    modules["items"]["allOf"] = rules
    return schema
