"""Atomic review context and explicitly synthetic offline observations."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .plans import canonical_hash
from .review import DetailReviewError


def atomic_context(
    request: Mapping[str, Any], canvas_sha256: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        ids = request["module_ids"]
        contracts = request["approved_module_contracts"]
        lineage = request["pixel_lineage"]
        slices = lineage["slices"]
        if (
            len(ids) != 2
            or len(set(ids)) != 2
            or [row["module_id"] for row in contracts] != ids
            or [row["module_id"] for row in slices] != ids
            or lineage["canvas"]["sha256"] != canvas_sha256
        ):
            raise ValueError("ordered module/slice/canvas context differs")
        for row in contracts:
            if (
                row["contract_sha256"] != canonical_hash(row["contract"])
                or not isinstance(row["spatial_contract"], dict)
                or not isinstance(row["performance_contract"], dict)
            ):
                raise ValueError("compiled module contract context differs")
        profile = request.get("review_profile")
        expected = {
            row["contract"].get("website_contract_version") for row in contracts
        }
        if expected != {profile}:
            raise ValueError("pixel profile differs from approved module contracts")
        return list(contracts), list(slices)
    except (KeyError, TypeError, ValueError) as exc:
        raise DetailReviewError(
            "AURORA_NATIVE_ATOMIC_CONTEXT_INVALID",
            "The two exact slices and their approved native contracts are required",
        ) from exc


def synthetic_module_judgments(
    request: Mapping[str, Any], canvas_sha256: str, audited_at: str
) -> list[dict[str, Any]]:
    """Mock-only data; never used as a fallback for a real vision response."""
    contracts, slices = atomic_context(request, canvas_sha256)
    result = []
    for row, sliced in zip(contracts, slices, strict=True):
        plan, spatial, performance = (
            row["contract"],
            row["spatial_contract"],
            row["performance_contract"],
        )
        evidence = (
            "SYNTHETIC_OFFLINE_FIXTURE; no actual semantic pixel acceptance: "
            + row["module_id"]
        )
        nodes = [
            f"SYNTHETIC visual node {index + 1}"
            for index in range(plan["visual_proof_contract"]["minimum_visual_payload"])
        ]
        texts = [copy["text"] for copy in plan["visible_copy"]]
        logo = plan["independent_logo_policy"] == "REQUIRED_IF_AUTHORIZED"
        expression = plan["module_visual_expression_contract"]
        observed: dict[str, Any] = {
            "ocr_text": texts,
            "unauthorized_ocr_text": [],
            "forbidden_text_hits": [],
            "effective_visual_payloads": nodes,
            "visible_proof_relations": [evidence],
            "hidden_copy_observation": evidence,
            "product_specificity_observation": evidence,
            "planned_elements_visible": [plan["duty"]],
            "planned_elements_missing": [],
            "reference_pattern_observation": evidence,
            "copy_binding_observation": evidence,
            "typography_primary_mode_observed": plan["text_system"]["primary_mode"],
            "typography_anchor_zone_observed": plan["text_system"]["anchor_zone"],
            "typography_anchor_target_observation": evidence,
            "typography_hierarchy_observation": evidence,
            "typography_neighbor_difference_observation": evidence,
            "product_prominence_percent": performance["product_prominence_min_percent"],
            "headline_visible": bool(texts),
            "headline_rank": 1 if texts else None,
            "evidence_is_primary_visual": True,
            "depth_layers": performance["depth_layers_min"],
            "type_roles_seen": min(performance["type_role_target"], max(1, len(texts))),
            "unequal_information_groups_seen": min(
                performance["unequal_information_groups_min"], max(1, len(texts) + 1)
            ),
            "visual_tension_seen": True,
            "mobile_scan_order": [
                "SYNTHETIC first",
                "SYNTHETIC second",
                "SYNTHETIC third",
            ],
            "commercial_closure_visible": True,
            "background_competition": "LOW",
            "independent_brand_logo_count": 1 if logo else 0,
            "independent_brand_logo_source_fidelity": "MATCHES_AUTHORIZED_SOURCE"
            if logo
            else "NOT_APPLICABLE",
            "independent_brand_logo_observation": evidence,
            "visual_carrier_observed": expression["primary_visual_carrier"],
            "claim_visible_without_copy": "FULL",
            "copy_dependency": "LOW",
            "pain_state_visible": True,
            "pain_consequence_visible": True,
            "product_solution_bridge_visible": True,
            "action_or_state_change_visible": True,
            "icon_only_dependency": False,
            "static_packshot_only": False,
            "generic_product_swap_possible": False,
            "visual_semantic_observation": evidence,
            "product_presence_observed": plan["complete_product_role"],
            "unplanned_full_product_seen": False,
            "identity_binding_without_product_observed": evidence,
            "observed_primary_subject_class": plan["primary_subject_class"],
            "observed_presentation_form": plan["presentation_form"],
            "observed_camera_scale": plan["camera_scale"],
            "observed_complete_product_role": plan["complete_product_role"],
            "observed_expression_signature": plan["expression_signature"],
            "observed_visual_payload_nodes": nodes,
            "ingredient_visual_present": plan["primary_subject_class"]
            == "INGREDIENT_OR_MATERIAL",
            "content_units_observed": [
                unit["text"] for unit in plan["content_unit_contract"]["units"]
            ],
            "planned_expression_signature_match": True,
            "conceptual_visualization_risk": "NOT_PRESENT",
            "conceptual_visualization_observation": evidence,
            "structure_family_observed": spatial["structure_family"],
            "structural_zone_count": spatial["zone_count"],
            "structural_relationship_observed": spatial["structural_relationship"],
            "full_bleed_single_scene": spatial["structural_relationship"]
            == "FULL_BLEED_SINGLE_SCENE",
            "dominant_zone_count_observed": spatial["dominant_zone_count"],
            "secondary_panel_count_observed": spatial["secondary_panel_count"],
            "mini_scene_count_observed": spatial["mini_scene_count"],
            "previous_structure_similarity": "NOT_APPLICABLE"
            if plan["order"] == 1
            else "LOW",
            "same_full_bleed_template_as_previous": False,
            "spatial_structure_observation": evidence,
        }
        result.append(
            {
                "module_id": row["module_id"],
                "status": "PASS",
                "audit_complete": True,
                "approved_contract_sha256": row["contract_sha256"],
                "slice_binding": {
                    "canvas_sha256": canvas_sha256,
                    "crop_box": sliced["crop_box"],
                },
                "artifact_binding": {
                    "image_sha256": sliced["sha256"],
                    "auditor": "polaris-offline-fixture",
                    "audited_at": audited_at,
                    "ocr_text": texts,
                    "visual_inspection_output": {"mode": "SYNTHETIC_OFFLINE_FIXTURE"},
                },
                "checks": {
                    name: {"pass": True, "visible_evidence": evidence + ": " + name}
                    for name in atomic_check_names()
                },
                "observations": observed,
                "material_failures": [],
                "passed_layers": list(atomic_check_names()),
                "residual_issues": [],
                "identity_silhouette": synthetic_identity(
                    plan["complete_product_role"], evidence
                ),
            }
        )
    if request.get("review_profile") == "WEBSITE_LEAN_V1":
        from .website_review import synthetic_reports
        return synthetic_reports(result)
    return deepcopy(result)


def atomic_check_names() -> tuple[str, ...]:
    # The vendored schema is the only list authority; no app copy of the 44 gates.
    import json

    from ..config import default_skill_root

    schema = json.loads(
        (
            default_skill_root("aurora-domestic-ecommerce-detail-page")
            / "schemas/module-judgment.schema.json"
        ).read_bytes()
    )
    return tuple(schema["properties"]["checks"]["required"])


def synthetic_identity(observed_presence: str, evidence: str) -> dict[str, Any]:
    from .identity_review import identity_schema

    scope = identity_schema()["x-presence-to-scope"][observed_presence]
    values: dict[str, Any] = {
        "shape_consistent": True,
        "colorway_consistent": True,
        "texture_consistent": True,
    }
    if scope == "NO_VISIBLE_PRODUCT":
        values = {key: "NOT_APPLICABLE" for key in values}
    elif scope == "VISIBLE_IDENTITY_TOKEN":
        values["shape_consistent"] = "NOT_APPLICABLE"
        values["texture_consistent"] = "NOT_APPLICABLE"
    return {
        **values,
        "comparison_scope": scope,
        "visible_features": []
        if scope == "NO_VISIBLE_PRODUCT"
        else [evidence + ": synthetic visible feature"],
        "visible_evidence": evidence,
    }
