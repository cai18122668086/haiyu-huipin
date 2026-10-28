"""Domestic detail fact provenance, intake validation and semantic text tokens."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def text_tokens(value: object, *, field: str) -> list[str]:
    """Keep a scalar phrase intact; reject values that cannot carry text facts."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        result = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise ValueError(f"{field}/{index} must be a text token")
            if item.strip():
                result.append(item.strip())
        return result
    raise ValueError(f"{field} must be a text token or a list of text tokens")


INTAKE_VERSION = "domestic-detail-intake-v1"


class DetailIntakeError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "/") -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.call_results: tuple[Any, ...] = ()


def user_fact_ledger(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Assign stable provenance without paraphrasing the user's original text."""
    facts: list[dict[str, Any]] = []
    for field in ("selling_points", "verified_facts"):
        raw = settings.get(field, [])
        if not isinstance(raw, list) or any(
            not isinstance(value, str) for value in raw
        ):
            raise DetailIntakeError(
                "INTAKE_USER_FACT_INVALID",
                "User facts must be text arrays",
                path=f"/settings/{field}",
            )
        for index, text in enumerate(raw):
            if not text.strip():
                continue
            core = field == "selling_points"
            facts.append(
                {
                    "id": f"F{len(facts) + 1:02d}",
                    "text": text,
                    "source_class": "USER_FACT",
                    "evidence_ref": f"settings/{field}/{index}",
                    "claim_kind": "OTHER",
                    "display_intent": "MUST_SHOW" if core else "SHOW_IF_RELEVANT",
                    "user_prominence": "CORE_SELLING_POINT"
                    if core
                    else "SUPPORTING_POINT",
                    "named_elements": [text],
                    "risk_route": "USER_CONFIRMED_FACT",
                }
            )
    return facts


def _native_schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    source = (
        Path(__file__).resolve().parents[2]
        / "skill_sources/aurora-domestic-ecommerce-detail-page/schemas"
    )
    return (
        json.loads((source / "input.schema.json").read_text(encoding="utf-8")),
        json.loads((source / "identity.schema.json").read_text(encoding="utf-8")),
    )


def _schema(value: object, schema: Mapping[str, Any], path: str) -> None:
    errors = sorted(
        Draft202012Validator(dict(schema)).iter_errors(value),
        key=lambda error: str(error.path),
    )
    if errors:
        error = errors[0]
        pointer = path + "/" + "/".join(str(part) for part in error.path)
        raise DetailIntakeError("INTAKE_SCHEMA_INVALID", error.message, path=pointer)


def _specific_observation(value: object, path: str, *, minimum_length: int = 4) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum_length:
        raise DetailIntakeError(
            "INTAKE_OBSERVATION_MISSING",
            "A direct visible observation is required",
            path=path,
        )
    text = value.strip()
    generic = (
        "以用户",
        "以参考图",
        "保持商品",
        "严格遵循",
        "match the reference",
        "same as source",
        "as approved",
    )
    if any(token in text.lower() for token in generic):
        raise DetailIntakeError(
            "INTAKE_POLICY_AS_OBSERVATION",
            "A preservation instruction is not a pixel observation",
            path=path,
        )
    return text


def validate_intake(
    payload: object,
    *,
    settings: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    brandless_confirmation: str | None = None,
    resolution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate model observations before identity lock or storyboard authoring.

    This boundary never infers facts from style references, supplies a CLEAR
    scan or substitutes generic geometry. Website rendering defaults can omit
    independent logos without declaring that the product itself is brandless.
    The caller must preserve the raw response and its source-byte binding.
    """
    if not isinstance(payload, Mapping) or payload.get("version") != INTAKE_VERSION:
        raise DetailIntakeError(
            "INTAKE_VERSION_INVALID", "A versioned model intake is required"
        )
    value = deepcopy(dict(payload))
    if any(
        not all(
            isinstance(source.get(key), str) and source[key]
            for key in ("asset_id", "role", "sha256")
        )
        for source in sources
    ):
        raise DetailIntakeError(
            "INTAKE_SOURCE_AUTHORITY_INVALID",
            "Every source requires its identity, role and byte hash",
        )
    product_ids = {
        source["asset_id"]
        for source in sources
        if source.get("role") == "product_reference"
    }
    style_ids = {
        source["asset_id"]
        for source in sources
        if source.get("role") == "style_reference"
    }
    if (
        not product_ids
        or product_ids & style_ids
        or any(
            source.get("role") not in {"product_reference", "style_reference"}
            for source in sources
        )
    ):
        raise DetailIntakeError(
            "INTAKE_SOURCE_AUTHORITY_INVALID",
            "Only product and style source roles are supported",
        )
    user_facts = user_fact_ledger(settings)
    user_ids = {fact["id"] for fact in user_facts}
    input_schema, identity_schema = _native_schemas()
    observed = value.get("observed_facts")
    if not isinstance(observed, list):
        raise DetailIntakeError(
            "INTAKE_OBSERVATION_MISSING",
            "Observed product facts must be explicit",
            path="/observed_facts",
        )
    seen = set(user_ids)
    for index, fact in enumerate(observed):
        path = f"/observed_facts/{index}"
        _schema(fact, input_schema["properties"]["verified_facts"]["items"], path)
        if fact["id"] in seen:
            raise DetailIntakeError(
                "INTAKE_DUPLICATE_FACT", "Fact identifiers must be unique", path=path
            )
        seen.add(fact["id"])
        if (
            fact["source_class"] != "OBSERVED_PRODUCT_EVIDENCE"
            or fact["evidence_ref"] not in product_ids
        ):
            raise DetailIntakeError(
                "INTAKE_FACT_SOURCE_INVALID",
                (
                    "Observed facts require a product source, never a style or "
                    "generated image"
                ),
                path=path,
            )
        _specific_observation(fact["text"], path + "/text")
        _specific_observation(
            fact.get("observed_region"), path + "/observed_region", minimum_length=1
        )
    scan = value.get("conflict_scan")
    conflicts = value.get("source_conflicts")
    _schema(scan, input_schema["properties"]["conflict_scan"], "/conflict_scan")
    _schema(
        conflicts, input_schema["properties"]["source_conflicts"], "/source_conflicts"
    )
    if not isinstance(scan, dict) or not isinstance(conflicts, list):
        raise DetailIntakeError(
            "INTAKE_SCHEMA_INVALID", "Conflict scan and alternatives have invalid types"
        )
    expected_sources = product_ids | user_ids
    scanned = scan.get("scanned_source_refs")
    if (
        not isinstance(scanned, list)
        or any(not isinstance(ref, str) for ref in scanned)
        or set(scanned) != expected_sources
    ):
        raise DetailIntakeError(
            "INTAKE_SCAN_INCOMPLETE",
            "The model scan must cover every product source and original user fact",
            path="/conflict_scan/scanned_source_refs",
        )
    if len({conflict["id"] for conflict in conflicts}) != len(conflicts):
        raise DetailIntakeError(
            "INTAKE_CONFLICT_BINDING_INVALID", "Conflict identifiers must be unique"
        )
    if set(scan["conflict_ids"]) != {conflict["id"] for conflict in conflicts}:
        raise DetailIntakeError(
            "INTAKE_CONFLICT_BINDING_INVALID", "Conflict IDs disagree with the scan"
        )
    for conflict in conflicts:
        if any(fact_id not in seen for fact_id in conflict["alternative_fact_ids"]):
            raise DetailIntakeError(
                "INTAKE_CONFLICT_BINDING_INVALID",
                "Every conflicting value needs a known fact source",
            )
    if scan["status"] == "CONFLICTS_FOUND":
        if not conflicts or any(
            conflict["status"] != "UNRESOLVED" for conflict in conflicts
        ):
            raise DetailIntakeError(
                "INTAKE_CONFLICT_BINDING_INVALID",
                "An unresolved conflict scan needs its complete alternatives",
            )
        if resolution is not None:
            from .intake import validate_resolution

            value["resolution"] = validate_resolution(value, resolution)
        elif value.get("identity") or value.get("modules") or value.get("plan"):
            raise DetailIntakeError(
                "INTAKE_CONFLICT_LOCK_FORBIDDEN",
                "Identity and storyboard must wait for source conflict resolution",
            )
        if resolution is None:
            value["user_facts"] = user_facts
            return value
    if scan["status"] == "CLEAR" and conflicts:
        raise DetailIntakeError(
            "INTAKE_CONFLICT_BINDING_INVALID",
            "A CLEAR scan cannot silently resolve conflicting values",
        )
    if value.get("brand_confirmation_required") is True:
        if value.get("identity") or value.get("modules") or value.get("plan"):
            raise DetailIntakeError(
                "INTAKE_BRAND_LOCK_FORBIDDEN",
                "Brand confirmation must precede identity lock",
            )
        observation = value.get("brand_observation")
        if (
            not isinstance(observation, Mapping)
            or observation.get("source_asset_id") not in product_ids
        ):
            raise DetailIntakeError(
                "INTAKE_BRAND_OBSERVATION_MISSING",
                "Brand clarification requires the actual product source",
            )
        _specific_observation(observation.get("text"), "/brand_observation/text")
        value["user_facts"] = user_facts
        return value
    if not user_facts and not observed:
        raise DetailIntakeError(
            "INTAKE_FACTS_MISSING", "Photo-led intake requires observed product facts"
        )
    identity = value.get("identity")
    _schema(identity, identity_schema, "/identity")
    if not isinstance(identity, dict):
        raise DetailIntakeError("INTAKE_SCHEMA_INVALID", "Identity must be an object")
    for setting_key, identity_key in (
        ("product_name", "canonical_name"),
        ("category", "category"),
    ):
        if (
            settings.get(setting_key)
            and identity[identity_key] != settings[setting_key]
        ):
            raise DetailIntakeError(
                "INTAKE_USER_IDENTITY_CHANGED",
                "User-supplied identity cannot be silently renamed",
            )
    if not set(identity["reference_image_ids"]).issubset(product_ids):
        raise DetailIntakeError(
            "INTAKE_IDENTITY_SOURCE_INVALID",
            "Identity references must be original product images",
        )
    observations = value.get("identity_observations")
    required = {"silhouette", "proportions", "parts", "color_material"}
    if (
        not isinstance(observations, list)
        or len(observations) != 4
        or not all(isinstance(entry, Mapping) for entry in observations)
        or {entry.get("kind") for entry in observations} != required
    ):
        raise DetailIntakeError(
            "INTAKE_GEOMETRY_MISSING",
            "Four concrete identity observations are required",
        )
    features = []
    for index, observation in enumerate(observations):
        path = f"/identity_observations/{index}"
        if observation.get("source_asset_id") not in product_ids:
            raise DetailIntakeError(
                "INTAKE_IDENTITY_SOURCE_INVALID",
                "Identity observations require a product source",
                path=path,
            )
        features.append(_specific_observation(observation.get("text"), path + "/text"))
        _specific_observation(
            observation.get("region"), path + "/region", minimum_length=1
        )
    if identity["invariant_features"] != features:
        raise DetailIntakeError(
            "INTAKE_IDENTITY_OBSERVATION_MISMATCH",
            "Locked identity must preserve the actual ordered observations",
        )
    brand = identity["brand_logo_source"]
    if brand.get("mode", "BRANDED") == "BRANDLESS":
        from .automatic_brand import fallback_basis

        brandless_confirmation = brandless_confirmation or fallback_basis(settings)
        if (
            not isinstance(brandless_confirmation, str)
            or len(brandless_confirmation.strip()) < 2
        ):
            raise DetailIntakeError(
                "INTAKE_BRANDLESS_CONFIRMATION_REQUIRED",
                "Brandless mode requires the user's explicit words",
            )
        if brand["brandless_evidence"] != brandless_confirmation:
            raise DetailIntakeError(
                "INTAKE_BRAND_AUTHORITY_MISMATCH",
                "Brandless evidence must retain the original confirmation",
            )
    elif brand["image_id"] not in product_ids:
        raise DetailIntakeError(
            "INTAKE_BRAND_SOURCE_INVALID",
            "The readable logo must come from a current product source",
        )
    else:
        _specific_observation(
            brand.get("observed_logo_text"),
            "/identity/brand_logo_source/observed_logo_text",
            minimum_length=1,
        )
        _specific_observation(
            brand.get("observed_region"),
            "/identity/brand_logo_source/observed_region",
            minimum_length=1,
        )
    references = value.get("reference_materials")
    _schema(
        references,
        input_schema["properties"]["reference_materials"],
        "/reference_materials",
    )
    if not isinstance(references, list):
        raise DetailIntakeError(
            "INTAKE_SCHEMA_INVALID", "Reference analyses must be an array"
        )
    if (
        len(references) != len(style_ids)
        or {reference["id"] for reference in references} != style_ids
    ):
        raise DetailIntakeError(
            "INTAKE_REFERENCE_COVERAGE_INVALID",
            "Every supplied style source must have its own analysis",
        )
    for index, reference in enumerate(references):
        if reference["path"] != f"asset:{reference['id']}":
            raise DetailIntakeError(
                "INTAKE_REFERENCE_SOURCE_INVALID",
                "Reference analysis must cite its own asset ID",
                path=f"/reference_materials/{index}/path",
            )
        unit_ids = [unit["id"] for unit in reference["analyzed_units"]]
        if len(unit_ids) != len(set(unit_ids)):
            raise DetailIntakeError(
                "INTAKE_REFERENCE_UNIT_DUPLICATE", "Reference unit IDs must be unique"
            )
        for unit_index, unit in enumerate(reference["analyzed_units"]):
            for field in (
                "visual_carrier",
                "composition_logic",
                "typography_logic",
                "observed_expression_method",
            ):
                _specific_observation(
                    unit[field],
                    f"/reference_materials/{index}/analyzed_units/{unit_index}/{field}",
                )
            if (
                unit["observed_expression_method"]
                not in reference["observed_expression_methods"]
            ):
                raise DetailIntakeError(
                    "INTAKE_REFERENCE_METHOD_MISMATCH",
                    "Reference units must retain their own observed methods",
                )
        for method in reference["observed_expression_methods"]:
            _specific_observation(
                method, f"/reference_materials/{index}/observed_expression_methods"
            )
    value["user_facts"] = user_facts
    return value
