"""Bind local model patches to the native effective contracts and shared schema."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, NoReturn

from jsonschema import Draft202012Validator

from ..config import default_skill_root
from .plans import canonical_hash
from .revision_protocol import RevisionScopeError

PRESERVE = "RESTORE_OR_PRESERVE_APPROVED"
OVERRIDE = "OVERRIDE_LOCAL_FIELDS"


def _invalid(message: str) -> NoReturn:
    raise RevisionScopeError("NATIVE_DETAIL_REVISION_SCOPE_INVALID", message)


def shared_override_schema() -> dict[str, Any]:
    root = default_skill_root("aurora-domestic-ecommerce-detail-page")
    return json.loads(
        (root / "schemas/local-typography-overrides.schema.json").read_bytes()
    )


def _branches() -> dict[str, Any]:
    return {
        row["properties"]["path"]["const"]: row
        for row in shared_override_schema()["items"]["oneOf"]
    }


def _parent(contract: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
    parts = path.strip("/").split("/")
    value = contract
    for part in parts[:-1]:
        if not isinstance(value.get(part), dict):
            _invalid("A local contract parent is absent from the effective source")
        value = value[part]
    if parts[-1] not in value:
        _invalid("A local contract leaf is absent from the effective source")
    return value, parts[-1]


def _bindings(copy: Any) -> list[dict[str, str]]:
    return [{"text": row["text"], "bound_to_visual": row["anchor"]} for row in copy]


def _validate_replacement(
    path: str, old: Any, new: Any, effect: str | None = None
) -> None:
    if isinstance(new, str) and not new.strip():
        _invalid("A local contract replacement cannot be blank")
    if path == "/visible_copy":
        rephrase = len(old) == len(new) and all(
            {k: v for k, v in a.items() if k != "text"}
            == {k: v for k, v in b.items() if k != "text"}
            for a, b in zip(old, new, strict=True)
        )
        remaining = iter(old)
        removal = all(any(row == prior for prior in remaining) for row in new)
        if not (
            (rephrase and effect in {None, "REPHRASE_CONFIRMED"})
            or (removal and effect in {None, "REMOVE"})
        ):
            _invalid("Local copy must preserve anchors or remove an exact subset")
    elif path == "/module_spatial_structure_contract/zones":
        if (
            len(old) != len(new)
            or any(
                {
                    k: v
                    for k, v in a.items()
                    if k not in {"geometry", "visual_weight_percent"}
                }
                != {
                    k: v
                    for k, v in b.items()
                    if k not in {"geometry", "visual_weight_percent"}
                }
                for a, b in zip(old, new, strict=True)
            )
            or abs(sum(row["visual_weight_percent"] for row in new) - 100) > 0.01
        ):
            _invalid("Local geometry must preserve content and total weight 100")


def _kind_allows(path: str, kind: str) -> bool:
    if path.startswith("/text_system/"):
        return kind == "TYPOGRAPHY_LAYOUT"
    if path == "/visible_copy":
        return kind == "COPY_CONTENT"
    return kind in {"VISUAL_DETAIL", "OTHER_LOCAL"}


def effective_contracts(
    plan: Mapping[str, Any],
    module_ids: Sequence[str],
    projection: object = None,
) -> list[dict[str, Any]]:
    """Validate a native projection; it may differ only in supported local leaves."""
    if len(module_ids) != 2 or len(set(module_ids)) != 2:
        _invalid("One ordered adjacent pair is required")
    originals = {m["id"]: m for m in plan["modules"]}
    if any(key not in originals for key in module_ids):
        _invalid("Effective contracts must belong to the approved pair")
    if projection is None:
        projection = [
            {
                "module_id": key,
                "contract": deepcopy(originals[key]),
                "contract_sha256": canonical_hash(originals[key]),
            }
            for key in module_ids
        ]
    if not isinstance(projection, list) or len(projection) != 2:
        _invalid("The actual native effective pair projection is required")
    branches = _branches()
    result = []
    for key, row in zip(module_ids, projection, strict=True):
        if (
            not isinstance(row, Mapping)
            or set(row) != {"module_id", "contract", "contract_sha256"}
            or row["module_id"] != key
            or not isinstance(row["contract"], Mapping)
            or row["contract_sha256"] != canonical_hash(row["contract"])
        ):
            _invalid("Effective contract order, bytes or digest changed")
        original = deepcopy(originals[key])
        contract = deepcopy(dict(row["contract"]))
        for path, branch in branches.items():
            try:
                a, leaf = _parent(original, path)
                b, _ = _parent(contract, path)
            except RevisionScopeError:
                continue
            old, current = a[leaf], b[leaf]
            if canonical_hash(old) != canonical_hash(current):
                if not Draft202012Validator(
                    branch["properties"]["replacement_value"]
                ).is_valid(current):
                    _invalid("An effective local value violates the native schema")
                _validate_replacement(path, old, current)
                if path == "/visible_copy":
                    for owner, copies in ((original, old), (contract, current)):
                        if owner.get("copy_intent", {}).get(
                            "text_to_visual_bindings"
                        ) != _bindings(copies):
                            _invalid(
                                "Effective copy must keep its original visual bindings"
                            )
                        owner["copy_intent"].pop("text_to_visual_bindings")
            a.pop(leaf)
            b.pop(leaf)
        if canonical_hash(original) != canonical_hash(contract):
            _invalid("Effective contracts changed facts or unsupported fields")
        result.append(deepcopy(dict(row)))
    return result


def bound_override_schema(context: Mapping[str, Any]) -> dict[str, Any]:
    schema = shared_override_schema()
    selected = next(
        row["contract"]
        for row in context["effective_module_contracts"]
        if row["module_id"] == context["selected_module_id"]
    )
    choices = []
    for path, branch in _branches().items():
        try:
            parent, leaf = _parent(deepcopy(dict(selected)), path)
        except RevisionScopeError:
            continue
        if not _kind_allows(path, context["adjustment"]["change_kind"]):
            continue
        branch = deepcopy(branch)
        properties = branch["properties"]
        properties["module_id"] = {"const": context["selected_module_id"]}
        properties["original_value"] = {"const": deepcopy(parent[leaf])}
        properties["change_index"]["maximum"] = (
            len(context["adjustment"]["change_only"]) - 1
        )
        choices.append(branch)
    if choices:
        schema["items"] = {"oneOf": choices}
    else:
        schema["maxItems"] = 0
    return schema


def validate_contract_overrides(
    observation: Mapping[str, Any], context: Mapping[str, Any]
) -> None:
    patches = observation["contract_overrides"]
    selected = next(
        row["contract"]
        for row in context["effective_module_contracts"]
        if row["module_id"] == context["selected_module_id"]
    )
    seen = set()
    by_change: dict[int, list[Any]] = {}
    for patch in patches:
        parent, leaf = _parent(deepcopy(dict(selected)), patch["path"])
        if canonical_hash(patch["original_value"]) != canonical_hash(parent[leaf]):
            _invalid("The exact effective original value and JSON type must match")
        key = patch["module_id"], patch["path"]
        if key in seen:
            _invalid("A local contract leaf cannot be patched twice")
        seen.add(key)
        if canonical_hash(patch["original_value"]) == canonical_hash(
            patch["replacement_value"]
        ):
            _invalid("A local contract replacement must change the effective value")
        if (
            isinstance(patch["replacement_value"], str)
            and not patch["replacement_value"].strip()
        ):
            _invalid("A local contract replacement cannot be blank")
        by_change.setdefault(patch["change_index"], []).append(patch)
    for row in observation["changes"]:
        has_patch = bool(by_change.get(row["index"]))
        if (row["contract_effect"] == OVERRIDE) != has_patch:
            _invalid(
                "Every contract effect must match its exact original change patches"
            )
        for patch in by_change.get(row["index"], []):
            copy_change = patch["path"] == "/visible_copy"
            if (
                not _kind_allows(patch["path"], context["adjustment"]["change_kind"])
                or row["scope"] != "LOCAL"
                or (not copy_change and row["claim_effect"] != "PRESERVE")
                or (
                    copy_change
                    and row["claim_effect"] not in {"REMOVE", "REPHRASE_CONFIRMED"}
                )
            ):
                _invalid("Local overrides cannot authorize new facts or global edits")
            _validate_replacement(
                patch["path"],
                patch["original_value"],
                patch["replacement_value"],
                row["claim_effect"],
            )
            if copy_change and selected.get("copy_intent", {}).get(
                "text_to_visual_bindings"
            ) != _bindings(patch["original_value"]):
                _invalid("Local copy requires the exact original visual bindings")
