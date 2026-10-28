"""Domestic detail model intake contracts and immutable local receipts."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import automatic_brand
from .facts import (
    INTAKE_VERSION,
    DetailIntakeError,
    _native_schemas,
    _schema,
    text_tokens,
    user_fact_ledger,
)
from .planner_dispatch import require_route, validate_dispatch_result
from .plans import canonical_hash

INTAKE_PROFILE = "aurora_domestic_intake"
IDENTITY_PROJECTION = "ORDERED_SOURCE_OBSERVATIONS_V1"


def intake_response_schema(
    product_asset_ids: Sequence[str] = (),
    user_fact_ids: Sequence[str] | None = None,
    *,
    wire_settings: Mapping[str, Any] | None = None,
    style_asset_ids: Sequence[str] | None = None,
    source_conflicts_resolved: bool = False,
    force_no_logo: bool = False,
) -> dict[str, Any]:
    native_input, identity = _native_schemas()
    identity = deepcopy(identity)
    logo_schema = identity["properties"]["brand_logo_source"]
    logo_schema["properties"].update(
        observed_logo_text={"type": "string", "minLength": 1, "pattern": r"\S"},
        observed_region={"type": "string", "minLength": 1, "pattern": r"\S"},
    )
    logo_schema["allOf"][0]["then"]["required"] = [
        "observed_logo_text",
        "observed_region",
    ]
    if automatic_brand.enabled(wire_settings or {}):
        logo_schema["allOf"][0]["else"]["properties"]["brandless_evidence"] = {
            "const": automatic_brand.fallback_basis(wire_settings or {})
        }
    observed_fact = deepcopy(native_input["properties"]["verified_facts"]["items"])
    observed_fact["properties"]["source_class"] = {"const": "OBSERVED_PRODUCT_EVIDENCE"}
    source_ref: dict[str, Any] = {"type": "string"}
    if product_asset_ids:
        source_ref["enum"] = list(product_asset_ids)
        identity["properties"]["reference_image_ids"]["items"] = deepcopy(source_ref)
    observed_fact["properties"]["evidence_ref"] = deepcopy(source_ref)
    observed_fact["required"] = list(
        dict.fromkeys(
            [
                *observed_fact["required"],
                "observed_region",
            ]
        )
    )
    observed_fact["properties"]["observed_region"] = {"type": "string", "minLength": 1}
    observed_fact["properties"]["text"]["minLength"] = 4
    scan = deepcopy(native_input["properties"]["conflict_scan"])
    scan.setdefault("required", []).append("scanned_source_refs")
    refs: dict[str, Any] = {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string"},
    }
    if user_fact_ids is not None:
        expected = sorted(set(product_asset_ids) | set(user_fact_ids))
        refs.update(minItems=len(expected), maxItems=len(expected))
        if expected:
            refs["items"]["enum"] = expected
    scan.setdefault("properties", {})["scanned_source_refs"] = refs
    result: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "version",
            "conflict_scan",
            "source_conflicts",
            "observed_facts",
            "brand_confirmation_required",
        ],
        "properties": {
            "version": {"const": INTAKE_VERSION},
            "conflict_scan": scan,
            "source_conflicts": native_input["properties"]["source_conflicts"],
            "observed_facts": {
                "type": "array",
                "items": observed_fact,
            },
            "identity": {"anyOf": [identity, {"type": "null"}]},
            "identity_observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["kind", "source_asset_id", "region", "text"],
                    "properties": {
                        "kind": {
                            "enum": [
                                "silhouette",
                                "proportions",
                                "parts",
                                "color_material",
                            ]
                        },
                        **{
                            key: {
                                "type": "string",
                                "minLength": 1 if key == "region" else 4,
                            }
                            for key in ("region", "text")
                        },
                        "source_asset_id": deepcopy(source_ref),
                    },
                },
            },
            "reference_materials": native_input["properties"]["reference_materials"],
            "operating_analysis": native_input["properties"]["operating_analysis"],
            "brand_confirmation_required": {"type": "boolean"},
            "brand_observation": {"type": "object"},
            "target_buyer": {"type": "string"},
            "primary_scenario": {"type": "string"},
        },
    }

    result["properties"]["brand_observation"] = {
        "type": "object",
        "properties": {
            "source_asset_id": deepcopy(source_ref),
            "text": {"type": "string", "minLength": 4},
        },
    }
    clear_scan_requirements: dict[str, Any] = {
        "if": {"properties": {"brand_confirmation_required": {"const": True}}},
        "then": {
            "required": ["brand_observation"],
            "properties": {
                "brand_observation": {"required": ["source_asset_id", "text"]}
            },
        },
        "else": {"required": ["identity", "identity_observations"]},
    }
    # An unresolved source conflict must return before identity or story work.
    # validate_intake separately requires the full identity after resolution.
    result["allOf"] = [
        {
            "if": {
                "properties": {
                    "conflict_scan": {"properties": {"status": {"const": "CLEAR"}}}
                }
            },
            "then": clear_scan_requirements,
        }
    ]
    if source_conflicts_resolved:
        # Original conflict rows remain UNRESOLVED as evidence. The explicit
        # human receipt, not that historical status, unlocks identity authoring.
        result["allOf"] = [clear_scan_requirements]
        if (wire_settings or {}).get("brandless_confirmation"):
            result["required"].extend(["identity", "identity_observations"])
            result["properties"]["identity"] = identity
            result["properties"]["brand_confirmation_required"] = {"const": False}
    reference = result["properties"]["reference_materials"]["items"]["properties"]
    for field in (
        "visual_carrier",
        "composition_logic",
        "typography_logic",
        "observed_expression_method",
    ):
        reference["analyzed_units"]["items"]["properties"][field]["minLength"] = 4
    reference["observed_expression_methods"]["items"]["minLength"] = 4
    if wire_settings is not None:
        result["required"].extend(
            [
                "identity_features_source",
                "reference_materials",
            ]
        )
        if style_asset_ids is not None and not style_asset_ids:
            result["properties"]["reference_materials"] = {"const": []}
        observed_fact["required"] = list(
            dict.fromkeys(
                [
                    *observed_fact["required"],
                    "atomic_key",
                    "atomicity",
                ]
            )
        )
        if not text_tokens(wire_settings.get("selling_points"), field="selling_points"):
            clear_scan_requirements["else"]["required"].append("operating_analysis")
        result["properties"]["identity_features_source"] = {
            "const": IDENTITY_PROJECTION
        }
        identity["required"].remove("invariant_features")
        identity["properties"].pop("invariant_features")
        identity["additionalProperties"] = False
        for source, target in (
            ("product_name", "canonical_name"),
            ("category", "category"),
        ):
            if wire_settings.get(source):
                identity["properties"][target] = {"const": wire_settings[source]}
        confirmation = wire_settings.get("brandless_confirmation") or (
            automatic_brand.fallback_basis(wire_settings) if force_no_logo else None
        )
        if confirmation:
            logo = identity["properties"]["brand_logo_source"]["properties"]
            for key, value in {
                "mode": "BRANDLESS",
                "image_id": "NONE",
                "authorization_basis": "NOT_APPLICABLE",
                "readability": "NOT_APPLICABLE",
                "usage": "FORBIDDEN_ALL_SCREENS",
                "brandless_evidence": confirmation,
            }.items():
                logo[key] = {"const": value}
        observations = result["properties"]["identity_observations"]
        common = observations["items"]
        observations.update(minItems=4, maxItems=4, items=False)
        observations["prefixItems"] = []
        for kind in ("silhouette", "proportions", "parts", "color_material"):
            item = deepcopy(common)
            item["properties"]["kind"] = {"const": kind}
            observations["prefixItems"].append(item)
    if automatic_brand.enabled(wire_settings or {}):
        result["properties"]["brand_confirmation_required"] = {"const": False}
    return result


def canonical_intake_wire(
    payload: Mapping[str, Any], *, has_style_sources: bool
) -> dict[str, Any]:
    """No uploaded style sources means exactly zero reference analyses.

    This default is input inventory, not an authored observation or an analysis.
    Original replies remain unchanged; nonempty or required analyses are never
    synthesized, deleted or repaired by this adapter.
    """
    value = deepcopy(dict(payload))
    if (
        value.get("identity_features_source") == IDENTITY_PROJECTION
        and not has_style_sources
        and value.get("reference_materials") is None
    ):
        value["reference_materials"] = []
    return value


def preserve_confirmed_history(
    payload: Mapping[str, Any],
    settings: Mapping[str, Any],
    resolution: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Keep confirmed history server-owned while checking every fact and choice.

    The direct website path may reuse an original reply that paraphrases the
    conflict explanation or repeats the already recorded human choice. Neither
    field is new product evidence. Changed facts, alternatives or choices still
    fail; the untouched response remains in IntakeStore.
    """
    value = deepcopy(dict(payload))
    prior = (resolution or {}).get("source_intake")
    if settings.get(
        "website_contract_version"
    ) != "WEBSITE_DIRECT_V2" or not isinstance(prior, Mapping):
        return value, None
    assert resolution is not None
    validate_resolution(prior, resolution)
    original = prior.get("source_conflicts", [])
    incoming = value.get("source_conflicts", [])
    choices = {row["conflict_id"]: row for row in resolution["resolutions"]}
    if (
        value.get("observed_facts") != prior.get("observed_facts")
        or not isinstance(incoming, list)
        or len(incoming) != len(original)
    ):
        raise DetailIntakeError(
            "INTAKE_RESOLVED_SOURCE_CHANGED",
            "Confirmed product observations or conflicts changed",
        )
    for before, after in zip(original, incoming, strict=True):
        if not isinstance(after, Mapping) or any(
            before.get(key) != after.get(key)
            for key in ("id", "topic", "alternative_fact_ids", "status")
        ):
            raise DetailIntakeError(
                "INTAKE_RESOLVED_SOURCE_CHANGED",
                "Confirmed conflict identity or alternatives changed",
            )
        repeated = after.get("resolution")
        if repeated is not None:
            choice = choices.get(before["id"], {})
            expected = {
                key: choice.get(key)
                for key in ("retained_fact_ids", "rejected_fact_ids")
            }
            expected["confirmation_text"] = resolution["confirmation_text"]
            if repeated != expected:
                raise DetailIntakeError(
                    "INTAKE_RESOLVED_SOURCE_CHANGED",
                    "The model changed the human resolution",
                )
        if set(after) - set(before) - {"resolution"}:
            raise DetailIntakeError(
                "INTAKE_RESOLVED_SOURCE_CHANGED", "Unexpected confirmed conflict fields"
            )
    value["source_conflicts"] = deepcopy(original)
    return value, {
        "version": "website-confirmed-history-v1",
        "original_history_sha256": canonical_hash(original),
        "human_resolution_sha256": canonical_hash(resolution),
        "model_history_sha256": canonical_hash(incoming),
    }


def project_identity_observations(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Project actual observations into the native identity; keep old replies strict.

    Only the explicit new wire protocol may derive this redundant field. Legacy
    replies keep their original identity and continue to fail on disagreement.
    The original result, raw text and observation bytes stay in IntakeStore.
    """
    value = deepcopy(dict(payload))
    marker = value.pop("identity_features_source", None)
    if marker is None:
        return value, None
    if marker != IDENTITY_PROJECTION:
        raise DetailIntakeError(
            "INTAKE_PROJECTION_INVALID", "Unknown identity observation source"
        )
    identity = value.get("identity")
    if not isinstance(identity, dict):
        return value, None
    observations = value.get("identity_observations")
    if (
        "invariant_features" in identity
        or not isinstance(observations, list)
        or len(observations) != 4
    ):
        raise DetailIntakeError(
            "INTAKE_PROJECTION_INVALID",
            "The wire protocol requires one original observation list",
        )
    features = []
    for row in observations:
        if not isinstance(row, Mapping) or not isinstance(row.get("text"), str):
            raise DetailIntakeError(
                "INTAKE_PROJECTION_INVALID",
                "Original identity observations are missing",
            )
        features.append(row["text"].strip())
    identity["invariant_features"] = features
    return value, {
        "version": "source-observation-identity-projection-v1",
        "source_payload_sha256": canonical_hash(payload),
        "observations_sha256": canonical_hash(observations),
        "identity_features_sha256": canonical_hash(features),
    }


def intake_prompt(
    settings: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    resolution: Mapping[str, Any] | None = None,
    brand_repair: Mapping[str, Any] | None = None,
) -> str:
    return (
        "Perform source intake BEFORE identity lock or any storyboard. "
        "Return JSON only, with version=domestic-detail-intake-v1. The "
        "attached product_reference images and original user facts are "
        "the only product truth. style_reference images teach visual "
        "grammar only. Never turn generated images or references into "
        "facts. Scan every product asset ID and every original Fxx user "
        "fact ID: list them in conflict_scan.scanned_source_refs. Report "
        "only real material SKU/specification/performance conflicts and "
        "all alternative fact IDs, with source-specific observations. "
        + (
            "HUMAN_RESOLUTION already resolves the recorded conflict. Preserve "
            "its original UNRESOLVED rows as evidence and author identity plus "
            "all four identity_observations using the retained facts. Do not "
            "stop at the old conflict again. "
            + (
                ""
                if automatic_brand.enabled(settings)
                else "Only a still-unconfirmed brand may require brand "
                "clarification instead of identity. "
            )
            if resolution is not None
            else "A material conflict MUST stop before identity/storyboard; "
            "omit identity and modules. "
        )
        + "Use O01.. for observed facts and cite "
        "source_class=OBSERVED_PRODUCT_EVIDENCE on every observed fact. "
        "Leave USER_FACTS in the server-owned Fxx ledger; never copy them into "
        "observed_facts or relabel user instructions as pixel observations. "
        "Use evidence_ref as an original product asset ID, with "
        "observed_region. Preserve the Fxx IDs and original user text "
        "without rewriting. USER_FACTS is input authority only: do not "
        "emit a user_facts key or re-emit that ledger. The server retains "
        "the original Fxx ledger independently. Return only top-level "
        "properties allowed by RESPONSE_CONTRACT_JSON. Do not repeat "
        "preservation policies as "
        "observations. When clear, provide exactly four "
        "identity_observations (silhouette, proportions, parts, "
        "color_material) describing actual visible geometry, region and "
        "source_asset_id. Do not return identity.invariant_features: the server "
        "copies the exact ordered observation texts into the native identity. "
        "Set identity_features_source=ORDERED_SOURCE_OBSERVATIONS_V1. "
        "Proportions must describe visible ratio/shape and "
        "parts must state actual count and relation. Retain the user's "
        "exact product_name/category when supplied. "
        + automatic_brand.prompt_policy(settings)
        + "BRANDED requires a "
        "readable current-product logo, its exact observed_logo_text and "
        "observed_region. "
        + (
            "BRANDLESS requires the exact server fallback basis in "
            "brandless_evidence. Never set brand_confirmation_required=true. "
            if automatic_brand.enabled(settings)
            else "BRANDLESS requires the user's explicit brandless_confirmation, "
            "retained verbatim in brandless_evidence. "
        )
        + "In BRANDLESS mode, "
        "identity.brand_logo_source.image_id must be the literal NONE, "
        "never a product asset ID: product references are not logo sources. "
        "Follow RESPONSE_CONTRACT_JSON, including its conditional "
        "brand fields. "
        + (
            ""
            if automatic_brand.enabled(settings)
            else "If neither is available, set brand_confirmation_required=true, "
            "describe actual brand_observation, and omit identity; ask no other "
            "unnecessary questions. "
        )
        + "Analyze every style asset as reference_materials "
        "using its asset ID, path=asset:<id>, concrete analyzed_units "
        "and observed_expression_methods. Do not fabricate ANALYZED "
        "references. No full plan/modules/copy is permitted in this "
        "response. Observed facts are atomic: include atomic_key and "
        "atomicity=ONE_INDEPENDENT_PROPOSITION. When the user supplied "
        "no selling points, include complete operating_analysis with "
        "fact-bound value bridges; disclose buyer/pain hypotheses as "
        "hypotheses, never product facts. If HUMAN_RESOLUTION is "
        "supplied, honor its exact "
        "retained/rejected fact IDs and confirmation text. Preserve "
        "original conflict IDs, alternatives and observed facts; do not "
        "invent a new silent resolution. Return source_conflicts as "
        "UNRESOLVED (the native Harness will apply the exact user "
        "receipt before identity lock); an approved resolution permits "
        "the identity analysis.\n"
    ) + json.dumps(
        {
            "USER_FACTS": user_fact_ledger(settings),
            "SETTINGS": dict(settings),
            "SOURCES": list(sources),
            "HUMAN_RESOLUTION": resolution,
            "AUTOMATIC_BRAND_REANALYSIS": brand_repair,
            "AUTOMATIC_NO_INDEPENDENT_LOGO_BASIS": automatic_brand.fallback_basis(
                settings
            ),
            "RESPONSE_CONTRACT_JSON": intake_response_schema(
                [
                    source["asset_id"]
                    for source in sources
                    if source.get("role") == "product_reference"
                ],
                [fact["id"] for fact in user_fact_ledger(settings)],
                wire_settings=settings,
                source_conflicts_resolved=resolution is not None,
                style_asset_ids=[
                    source["asset_id"]
                    for source in sources
                    if source.get("role") == "style_reference"
                ],
                force_no_logo=brand_repair is not None,
            ),
        },
        ensure_ascii=False,
    )


def validate_resolution(
    raw_intake: Mapping[str, Any], resolution: Mapping[str, Any]
) -> dict[str, Any]:
    source = Path(__file__).resolve().parents[2] / (
        "skill_sources/aurora-domestic-ecommerce-detail-page/schemas/conf"
        "lict-resolution.schema.json"
    )
    if (
        not isinstance(resolution.get("confirmation_text"), str)
        or not resolution["confirmation_text"].strip()
    ):
        raise DetailIntakeError(
            "INTAKE_RESOLUTION_INVALID",
            "Preserve the user's explicit confirmation words",
        )
    conflicts = {item["id"]: item for item in raw_intake.get("source_conflicts", [])}
    if conflicts:
        _schema(
            resolution,
            json.loads(source.read_text(encoding="utf-8")),
            "/intake_resolution",
        )
    rows = resolution.get("resolutions")
    if (
        not isinstance(rows, list)
        or len(rows) != len(conflicts)
        or {item.get("conflict_id") for item in rows} != set(conflicts)
    ):
        raise DetailIntakeError(
            "INTAKE_RESOLUTION_INCOMPLETE",
            "One response must account for every material conflict exactly once",
        )
    all_retained: set[str] = set()
    all_rejected: set[str] = set()
    for row in rows:
        retained, rejected = (
            set(row["retained_fact_ids"]),
            set(row["rejected_fact_ids"]),
        )
        alternatives = set(conflicts[row["conflict_id"]]["alternative_fact_ids"])
        if not retained or retained & rejected or retained | rejected != alternatives:
            raise DetailIntakeError(
                "INTAKE_RESOLUTION_INVALID",
                (
                    "Retain at least one known alternative and explicitly reject "
                    "every other value"
                ),
            )
        all_retained.update(retained)
        all_rejected.update(rejected)
    if all_retained & all_rejected:
        raise DetailIntakeError(
            "INTAKE_RESOLUTION_CONTRADICTORY",
            "A fact cannot be both retained and rejected across conflicts",
        )
    return deepcopy(dict(resolution))


def active_fact_ids(intake: Mapping[str, Any]) -> list[str]:
    rejected = {
        fact_id
        for row in (intake.get("resolution") or {}).get("resolutions", [])
        for fact_id in row["rejected_fact_ids"]
    }
    return [
        fact["id"]
        for fact in [*intake.get("user_facts", []), *intake.get("observed_facts", [])]
        if fact["id"] not in rejected
    ]


def intake_binding(
    *,
    settings: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    skill_hash: str,
    model: str,
    resolution: Mapping[str, Any] | None,
    dispatch_route: Mapping[str, Any] | None = None,
    brand_repair: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_root = Path(__file__).parent
    value = {
        "version": INTAKE_VERSION,
        "settings": dict(settings),
        "sources": list(sources),
        "skill_content_hash": skill_hash,
        "model": model,
        "dispatch_route": deepcopy(dict(dispatch_route)) if dispatch_route else None,
        "resolution": resolution,
        "intake_runtime_sha256": canonical_hash(
            {
                "adapter": hashlib.sha256(
                    (source_root.parent / "xinghe_agent.py").read_bytes()
                ).hexdigest(),
                **{
                    name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
                    for name in (
                        "facts.py",
                        "intake.py",
                        "planner_dispatch.py",
                        "automatic_brand.py",
                    )
                },
            }
        ),
    }
    if brand_repair is not None:
        value["automatic_brand_repair"] = deepcopy(dict(brand_repair))
    return {**value, "sha256": canonical_hash(value)}


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class IntakeStore:
    root: Path
    binding: Mapping[str, Any]
    job_id: str | None = None

    @property
    def path(self) -> Path:
        return self.root / (str(self.binding["sha256"]) + ".json")

    def completed(self) -> dict[str, Any] | None:
        if self.binding.get("sha256") != canonical_hash(
            {k: v for k, v in self.binding.items() if k != "sha256"}
        ):
            raise DetailIntakeError(
                "INTAKE_BINDING_CHANGED", "The original intake binding hash changed"
            )
        if not self.path.exists():
            for previous in self.root.glob("*.json"):
                if previous.name.endswith(".response.json"):
                    continue
                old = json.loads(previous.read_text(encoding="utf-8")).get(
                    "binding", {}
                )
                if all(
                    old.get(key) == self.binding.get(key)
                    for key in ("settings", "sources", "resolution")
                ):
                    raise DetailIntakeError(
                        "INTAKE_BINDING_CHANGED",
                        "An existing intake has a different code, Skill, or model "
                        "binding; reconcile it before dispatch",
                    )
            return None
        request = json.loads(self.path.read_text(encoding="utf-8"))
        if self.job_id is not None and request.get("job_id") != self.job_id:
            raise DetailIntakeError(
                "INTAKE_RECEIPT_INVALID", "The original intake belongs to another job"
            )
        if canonical_hash(request.get("binding")) != canonical_hash(self.binding):
            raise DetailIntakeError(
                "INTAKE_BINDING_CHANGED", "Saved intake inputs or Skill changed"
            )
        completion = self.path.with_suffix(".response.json")
        if not completion.exists():
            raise DetailIntakeError(
                "INTAKE_OUTCOME_UNKNOWN",
                "Reconcile the original intake before another paid request",
            )
        value = json.loads(completion.read_text(encoding="utf-8"))
        if value.get("sha256") != canonical_hash(value.get("result")):
            raise DetailIntakeError(
                "INTAKE_RESPONSE_CHANGED", "The original model intake receipt changed"
            )
        result = value["result"]
        if self.binding.get("dispatch_route") is not None:
            route = require_route(self.binding["dispatch_route"])
            validate_dispatch_result(result, route)
            expected = {
                "version": "native-intake-call-v1",
                "binding_sha256": self.binding["sha256"],
                "request_file": "intake/" + self.path.name,
                "response_file": "intake/" + completion.name,
                "source_job_id": request["job_id"],
            }
            envelope = result.get("provider_envelope") or {}
            if canonical_hash(envelope.get("native_intake_receipt")) != canonical_hash(
                expected
            ):
                raise DetailIntakeError(
                    "INTAKE_RECEIPT_INVALID",
                    "The original intake event identity changed",
                )
            if route["kind"] == "http" and result.get("error_code") is None:
                from ..xinghe_agent import _planner_content_payload

                try:
                    parsed = _planner_content_payload(result["audit_raw_content"])
                except Exception as exc:
                    raise DetailIntakeError(
                        "INTAKE_RAW_REPLY_CHANGED",
                        "Original intake model text cannot be parsed",
                    ) from exc
                if canonical_hash(parsed) != canonical_hash(result.get("payload")):
                    raise DetailIntakeError(
                        "INTAKE_RAW_REPLY_CHANGED",
                        "Original intake model text differs from its parsed result",
                    )
        return result

    def recover_for_revalidation(self) -> dict[str, Any] | None:
        """Read old immutable evidence; current intake schema must revalidate it."""
        if self.path.exists():
            return self.completed()
        matches = []
        for path in self.root.glob("*.json"):
            if path.name.endswith(".response.json"):
                continue
            old = json.loads(path.read_bytes()).get("binding", {})
            keys = set(self.binding) - {"sha256", "intake_runtime_sha256"}
            settings = self.binding.get("settings")
            if (
                isinstance(settings, Mapping)
                and settings.get("website_contract_version") == "WEBSITE_DIRECT_V2"
            ):
                # An explicit failed retry revalidates original evidence under
                # the current website contract; facts, sources, choice and
                # supplier/model bindings must still match exactly.
                keys.discard("skill_content_hash")
            if set(old) != set(self.binding) or any(
                old.get(k) != self.binding.get(k) for k in keys
            ):
                continue
            original = IntakeStore(self.root, old, self.job_id)
            if original.path != path:
                raise DetailIntakeError(
                    "INTAKE_RECEIPT_INVALID", "Original intake filename changed"
                )
            result = original.completed()
            if result is not None:
                matches.append(result)
        if len(matches) > 1:
            raise DetailIntakeError(
                "INTAKE_RECEIPT_INVALID", "Ambiguous prior intake evidence"
            )
        return matches[0] if matches else None

    def begin(self, job_id: str) -> None:
        if self.job_id is not None and job_id != self.job_id:
            raise DetailIntakeError(
                "INTAKE_RECEIPT_INVALID", "Intake job identity changed"
            )
        try:
            _write_once(self.path, {"binding": dict(self.binding), "job_id": job_id})
        except FileExistsError as exc:
            raise DetailIntakeError(
                "INTAKE_ALREADY_DISPATCHED",
                "This exact intake request was already dispatched",
            ) from exc

    def finish(self, result: Mapping[str, Any]) -> None:
        _write_once(
            self.path.with_suffix(".response.json"),
            {"result": dict(result), "sha256": canonical_hash(result)},
        )
        self.completed()
