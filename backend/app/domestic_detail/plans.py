"""Domestic detail plan authority and immutable approval scope."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

BINDING_VERSION = "domestic-detail-approval-v4"


class DetailPlanError(ValueError):
    pass


def canonical_hash(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def resolve_native_plan(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    candidates: list[Mapping[str, Any]] = []

    def take(container: object) -> None:
        if isinstance(container, Mapping) and "aurora_plan" in container:
            value = container["aurora_plan"]
            if not isinstance(value, Mapping) or not isinstance(
                value.get("modules"), list
            ):
                raise DetailPlanError("aurora_plan must contain a module list")
            candidates.append(value)

    take(payload)
    take(payload.get("analysis"))
    for group in (
        payload.get("groups", []) if isinstance(payload.get("groups"), list) else []
    ):
        if isinstance(group, Mapping):
            take(group.get("analysis"))
            take(group.get("plan_protocol_fields"))
    if not candidates:
        return None
    if any(value != candidates[0] for value in candidates[1:]):
        raise DetailPlanError("Root and nested Aurora plans disagree")
    return deepcopy(dict(candidates[0]))


def validate_plan_views(payload: Mapping[str, Any]) -> dict[str, Any]:
    native = resolve_native_plan(payload)
    if native is None:
        raise DetailPlanError("Full Aurora compact plan is required")
    modules = native["modules"]
    pages = payload.get("pages")
    groups = payload.get("groups")
    if not isinstance(pages, list) or not isinstance(groups, list):
        raise DetailPlanError("Derived screen views and groups are required")
    nested = []
    for group in groups:
        if not isinstance(group, Mapping) or not isinstance(group.get("pages"), list):
            raise DetailPlanError("Each group requires an ordered screen view list")
        nested.extend(group["pages"])
    if nested != pages or len(pages) != len(modules):
        raise DetailPlanError("Root and grouped screen views disagree")
    for index, (module, page) in enumerate(zip(modules, pages, strict=True), start=1):
        if not isinstance(module, Mapping) or not isinstance(page, Mapping):
            raise DetailPlanError("Module and screen views must be objects")
        if module.get("id") != f"M{index:02d}" or page.get("aurora_module") != module:
            raise DetailPlanError(
                f"M{index:02d} view differs from the authoritative module"
            )
        expression = module.get("expression") or {}
        structure = module.get("structure") or {}
        transition = module.get("transition_contract") or {}
        expected = {
            "aurora_module_id": module.get("id"),
            "role": str(module.get("story_function") or "OTHER"),
            "title": str(module.get("lead_conclusion") or ""),
            "label": str(module.get("buyer_question") or ""),
            "composition": str(
                expression.get("visual_scene_sentence")
                if isinstance(expression, Mapping)
                else module.get("visual_proof_direction") or ""
            ),
            "device": str(module.get("visual_archetype") or ""),
            "difference": str(
                transition.get("seam_carrier")
                if isinstance(transition, Mapping)
                else module.get("variation_from_previous") or ""
            ),
            "structure": str(
                structure.get("structure_family")
                if isinstance(structure, Mapping)
                else ""
            ),
            "subject_scale": str(module.get("camera_scale") or "mixed"),
            "pair_id": f"P{(index + 1) // 2:02d}",
            "pair_position": 1 if index % 2 else 2,
        }
        if any(page.get(key) != value for key, value in expected.items()):
            raise DetailPlanError(
                f"Screen M{index:02d} display differs from its module"
            )
    return native


def approval_binding(
    payload: Mapping[str, Any], settings: Mapping[str, Any]
) -> dict[str, Any]:
    native = validate_plan_views(payload)
    if len(native["modules"]) not in {8, 10, 12} or settings.get("page_count") != len(
        native["modules"]
    ):
        raise DetailPlanError("Approved module count differs from the product settings")
    source_signature = payload.get("input_signature")
    if not isinstance(source_signature, str) or len(source_signature) != 64:
        raise DetailPlanError("Source input signature is missing or malformed")
    source_manifest = payload.get("source_asset_manifest")
    source_input = payload.get("source_input_sha256")
    if (
        not isinstance(source_input, str)
        or len(source_input) != 64
        or not isinstance(source_manifest, list)
        or not source_manifest
    ):
        raise DetailPlanError("Approval requires a source input and asset manifest")
    if [
        group.get("group_key")
        for group in source_manifest
        if isinstance(group, Mapping)
    ] != [group.get("group_key") for group in payload["groups"]]:
        raise DetailPlanError(
            "Approved source groups differ from the storyboard groups"
        )
    for group in source_manifest:
        assets = group.get("assets") if isinstance(group, Mapping) else None
        if not isinstance(assets, list) or not assets:
            raise DetailPlanError("Every approved group requires its source assets")
        for asset in assets:
            if (
                not isinstance(asset, Mapping)
                or not all(
                    isinstance(asset.get(key), str) and asset[key]
                    for key in ("asset_id", "role", "sha256", "filename")
                )
                or asset["role"] not in {"product_reference", "style_reference"}
                or len(asset["sha256"]) != 64
            ):
                raise DetailPlanError(
                    "Approved asset identities, roles and byte hashes must be explicit"
                )
    manifest = (
        Path(__file__).resolve().parents[1]
        / "skill_manifests/aurora-domestic-ecommerce-detail-page.json"
    )
    content_hash = json.loads(manifest.read_text(encoding="utf-8"))["content_hash"]
    intake = payload.get("product_intake")
    if (
        not isinstance(intake, Mapping)
        or intake.get("version") != "domestic-detail-intake-v1"
    ):
        raise DetailPlanError("Approval requires the validated source intake")
    from .contracts import normalize_native_plan_for_harness
    from .native_approval import validate_native_approval

    native_approval = validate_native_approval(payload.get("native_approval"))
    if native_approval["normalized_plan_sha256"] != canonical_hash(
        normalize_native_plan_for_harness(native)
    ):
        raise DetailPlanError(
            "Native approval was compiled from a different storyboard"
        )
    binding = {
        "version": BINDING_VERSION,
        "native_approval_sha256": canonical_hash(native_approval),
        "product_intake_sha256": canonical_hash(intake),
        "native_plan_sha256": canonical_hash(native),
        "screen_views_sha256": canonical_hash(payload["pages"]),
        "settings_sha256": canonical_hash(settings),
        "source_input_signature": payload.get("input_signature"),
        "source_input_sha256": source_input,
        "source_assets_sha256": canonical_hash(source_manifest),
        "skill_content_hash": content_hash,
    }
    return {**binding, "sha256": canonical_hash(binding)}


def validate_approval_binding(
    payload: Mapping[str, Any], settings: Mapping[str, Any]
) -> None:
    expected = approval_binding(payload, settings)
    aurora = payload.get("aurora_v4_3")
    if not isinstance(aurora, Mapping) or aurora.get("contract_binding") != expected:
        raise DetailPlanError("Approved input, plan, views or Skill binding changed")
    document = aurora.get("approval_document_markdown")
    if not isinstance(document, str) or hashlib.sha256(
        document.encode("utf-8")
    ).hexdigest() != aurora.get("approval_document_sha256"):
        raise DetailPlanError("Approval document bytes differ from their fingerprint")
    if payload["native_approval"]["document_markdown"] not in document:
        raise DetailPlanError(
            "The complete native compiler document is not shown in the approval"
        )
    if expected["sha256"] not in document:
        raise DetailPlanError("Approval document does not expose its immutable scope")
