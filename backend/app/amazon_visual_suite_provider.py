"""Independent manifest-gated adapter for the Amazon 21-image suite."""

# The authoritative 21-row task table intentionally keeps buyer-facing copy
# visible beside each stable item key; wrapping those values would obscure review.
# ruff: noqa: E501

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import gcd
from typing import Any

from app.amazon_visual_suite_contract import (
    AMAZON_DEFAULT_MARKETPLACE,
    AMAZON_MARKETPLACES,
)
from app.new_detail_skill_provider import (
    SKILL_ADAPTER_CONFIGURATION_KEY,
    AdapterValidationError,
    BundledSkillManifestValidator,
    ManifestValidator,
    job_configuration_from,
    rejected_result,
)
from app.new_image_skill_provider import (
    SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION,
    NewImageSkillContractError,
    compact_prompt_input,
    optional_string,
    optional_string_list,
    require_asset_ids,
    role_labelled_assets,
    validate_optional_asset_ids,
)
from app.provider import Provider, ProviderOutcome, ProviderRequest, ProviderResult
from app.skill_runtime import SkillRuntimeError

AMAZON_VISUAL_SUITE_OPERATION = "amazon_visual_suite"
AMAZON_VISUAL_SUITE_SKILL_ID = "xinghe-amazon-visual-suite"
AMAZON_ADAPTER_CONTRACT_VERSION = SINGLE_IMAGE_ADAPTER_CONTRACT_VERSION
AMAZON_EXPECTED_ITEM_COUNT = 21
AMAZON_AUDIT_CONFIGURATION_KEY = "amazon_visual_suite_audit"
AMAZON_PLAN_CONFIGURATION_KEY = "amazon_visual_suite_plan"
AMAZON_PLANNING_POLICY_VERSION = "amazon-visual-suite-planning-v4"
_AMAZON_PROMPT_GUIDANCE_MAX_LENGTH = 1_800
_GLOBAL_RISK_GUARD = (
    "Preserve product identity and physical logic. Do not invent parts, SKUs, "
    "accessories, packaging facts, certifications, ratings, rankings, test "
    "results, endorsements, or absolute performance claims."
)


@dataclass(frozen=True)
class AmazonItemSpec:
    item_key: str
    group: str
    group_ordinal: int
    width: int
    height: int
    asset_type: str
    buyer_question: str
    visual_proof: str


def _item_spec(
    item_key: str,
    group: str,
    group_ordinal: int,
    width: int,
    height: int,
    *,
    asset_type: str,
    buyer_question: str,
    visual_proof: str,
) -> AmazonItemSpec:
    return AmazonItemSpec(
        item_key=item_key,
        group=group,
        group_ordinal=group_ordinal,
        width=width,
        height=height,
        asset_type=asset_type,
        buyer_question=buyer_question,
        visual_proof=visual_proof,
    )


def _build_item_specs() -> tuple[AmazonItemSpec, ...]:
    """Map only the stable fields explicitly defined by the original Skill."""

    return (
        _item_spec(
            "MAIN-01",
            "main",
            1,
            2000,
            2000,
            asset_type="Main image",
            buyer_question="Recognize the exact product quickly",
            visual_proof=(
                "Real product on pure white background, clear edges, centered, "
                "high subject occupancy"
            ),
        ),
        _item_spec(
            "SECONDARY-01",
            "secondary",
            1,
            1200,
            1600,
            asset_type="Secondary image",
            buyer_question="What is the main reason to buy?",
            visual_proof=(
                "Strongest product benefit in one scene or clean studio composition"
            ),
        ),
        _item_spec(
            "SECONDARY-02",
            "secondary",
            2,
            1200,
            1600,
            asset_type="Secondary image",
            buyer_question="What problem does it solve?",
            visual_proof="Before/after, user frustration, or real context contrast",
        ),
        _item_spec(
            "SECONDARY-03",
            "secondary",
            3,
            1200,
            1600,
            asset_type="Secondary image",
            buyer_question="How does it work?",
            visual_proof=(
                "Structural callouts, mechanism, material, or feature close-up"
            ),
        ),
        _item_spec(
            "SECONDARY-04",
            "secondary",
            4,
            1200,
            1600,
            asset_type="Secondary image",
            buyer_question="Will it fit my use case?",
            visual_proof=(
                "Scale reference, dimensions, model choices, or compatibility caveat"
            ),
        ),
        _item_spec(
            "SECONDARY-05",
            "secondary",
            5,
            1200,
            1600,
            asset_type="Secondary image",
            buyer_question="Is it easy to use?",
            visual_proof=(
                "3-step operation, hand interaction, installation, opening, "
                "cleaning, or daily flow"
            ),
        ),
        _item_spec(
            "SECONDARY-06",
            "secondary",
            6,
            1200,
            1600,
            asset_type="Secondary image",
            buyer_question="What exactly do I get and why trust it?",
            visual_proof=(
                "Included items, packaging, care, conservative proof, or "
                "selling-point summary"
            ),
        ),
        _item_spec(
            "A-DESKTOP-01",
            "a_desktop",
            1,
            1464,
            600,
            asset_type="Desktop A+ module",
            buyer_question="What is this product's promise?",
            visual_proof="Wide hero scene with product and campaign headline",
        ),
        _item_spec(
            "A-DESKTOP-02",
            "a_desktop",
            2,
            1464,
            600,
            asset_type="Desktop A+ module",
            buyer_question="Why do I need it?",
            visual_proof="High-priority pain point or desired result",
        ),
        _item_spec(
            "A-DESKTOP-03",
            "a_desktop",
            3,
            1464,
            600,
            asset_type="Desktop A+ module",
            buyer_question="What makes it different?",
            visual_proof=(
                "Mechanism, structure, technology, craft, or detail evidence"
            ),
        ),
        _item_spec(
            "A-DESKTOP-04",
            "a_desktop",
            4,
            1464,
            600,
            asset_type="Desktop A+ module",
            buyer_question="Where does it fit in real life?",
            visual_proof="Lifestyle/use environment with product scale",
        ),
        _item_spec(
            "A-DESKTOP-05",
            "a_desktop",
            5,
            1464,
            600,
            asset_type="Desktop A+ module",
            buyer_question="Can I trust the material/build?",
            visual_proof="Macro, texture, parts, finish, or visible construction",
        ),
        _item_spec(
            "A-DESKTOP-06",
            "a_desktop",
            6,
            1464,
            600,
            asset_type="Desktop A+ module",
            buyer_question="Why choose this version?",
            visual_proof=(
                "Conservative comparison, size/version guide, or feature matrix "
                "without fake competitor claims"
            ),
        ),
        _item_spec(
            "A-DESKTOP-07",
            "a_desktop",
            7,
            1464,
            600,
            asset_type="Desktop A+ module",
            buyer_question="What should I remember before purchase?",
            visual_proof=("Cohesive recap with product and 3-4 confirmed reasons"),
        ),
        _item_spec(
            "A-MOBILE-01",
            "a_mobile",
            1,
            1600,
            1200,
            asset_type="Mobile A+ module",
            buyer_question="What is this product's promise?",
            visual_proof=(
                "Larger product, shorter headline, single dominant focal point"
            ),
        ),
        _item_spec(
            "A-MOBILE-02",
            "a_mobile",
            2,
            1600,
            1200,
            asset_type="Mobile A+ module",
            buyer_question="Why do I need it?",
            visual_proof="One scene/action, minimal labels",
        ),
        _item_spec(
            "A-MOBILE-03",
            "a_mobile",
            3,
            1600,
            1200,
            asset_type="Mobile A+ module",
            buyer_question="What makes it different?",
            visual_proof="Macro or structure detail must remain readable",
        ),
        _item_spec(
            "A-MOBILE-04",
            "a_mobile",
            4,
            1600,
            1200,
            asset_type="Mobile A+ module",
            buyer_question="Where does it fit in real life?",
            visual_proof=("Product scale and context must be obvious in one second"),
        ),
        _item_spec(
            "A-MOBILE-05",
            "a_mobile",
            5,
            1600,
            1200,
            asset_type="Mobile A+ module",
            buyer_question="Can I trust the material/build?",
            visual_proof="Detail crop or material proof over dense text",
        ),
        _item_spec(
            "A-MOBILE-06",
            "a_mobile",
            6,
            1600,
            1200,
            asset_type="Mobile A+ module",
            buyer_question="Why choose this version?",
            visual_proof="Simplify comparison to the fewest useful points",
        ),
        _item_spec(
            "A-MOBILE-07",
            "a_mobile",
            7,
            1600,
            1200,
            asset_type="Mobile A+ module",
            buyer_question="What should I remember before purchase?",
            visual_proof="Clear product recap, no crowded checklist",
        ),
    )


AMAZON_ITEM_SPECS = _build_item_specs()
AMAZON_ITEM_KEYS = tuple(spec.item_key for spec in AMAZON_ITEM_SPECS)
AMAZON_ITEM_ROLES = (
    "search_main",
    "core_benefit",
    "pain_point_scene",
    "function_structure",
    "size_fit",
    "use_steps",
    "trust_recap",
    "brand_opening",
    "buyer_demand",
    "core_feature",
    "scenario_proof",
    "detail_trust",
    "comparison_choice",
    "closing_summary",
    "mobile_opening",
    "mobile_buyer_demand",
    "mobile_core_feature",
    "mobile_scenario_proof",
    "mobile_detail_trust",
    "mobile_comparison_choice",
    "closing_summary",
)
AMAZON_ROLE_BY_ITEM_KEY = dict(zip(AMAZON_ITEM_KEYS, AMAZON_ITEM_ROLES, strict=True))
AMAZON_SKILL_ROW_IDS = (
    "main-01",
    *(f"secondary-{index:02d}" for index in range(1, 7)),
    *(f"aplus-desktop-{index:02d}" for index in range(1, 8)),
    *(f"aplus-mobile-{index:02d}" for index in range(1, 8)),
)
AMAZON_SKILL_ROW_ID_BY_ITEM_KEY = dict(
    zip(AMAZON_ITEM_KEYS, AMAZON_SKILL_ROW_IDS, strict=True)
)
AMAZON_ITEM_KEY_BY_SKILL_ROW_ID = {
    row_id: item_key for item_key, row_id in AMAZON_SKILL_ROW_ID_BY_ITEM_KEY.items()
}
_AMAZON_ITEM_SPEC_BY_KEY = {spec.item_key: spec for spec in AMAZON_ITEM_SPECS}


def amazon_item_spec(item_key: str) -> AmazonItemSpec:
    try:
        return _AMAZON_ITEM_SPEC_BY_KEY[item_key]
    except KeyError as exc:
        raise AdapterValidationError(
            "AMAZON_ITEM_KEY_INVALID",
            f"Unknown Amazon deliverable item key: {item_key}.",
        ) from exc


def validate_amazon_item_keys(item_keys: Sequence[str]) -> tuple[str, ...]:
    """Validate the exact stable 21-key inventory and ordering."""

    normalized = tuple(item_keys)
    if normalized != AMAZON_ITEM_KEYS:
        missing = [key for key in AMAZON_ITEM_KEYS if key not in normalized]
        extra = [key for key in normalized if key not in AMAZON_ITEM_KEYS]
        raise AdapterValidationError(
            "AMAZON_ITEM_INVENTORY_INVALID",
            (
                "Amazon visual suite requires the canonical 21-item inventory; "
                f"missing={missing}, extra={extra}."
            ),
        )
    return normalized


def validate_amazon_planner_payload(
    payload: object,
    *,
    sources: Sequence[object] = (),
) -> dict[str, Any]:
    """Validate the original Amazon Skill contract and attach runtime item keys."""

    from app.xinghe_agent import XingheAgentError

    if not isinstance(payload, Mapping):
        raise XingheAgentError(
            "PLANNER_SCHEMA_INVALID",
            "Amazon planner payload must be an object.",
            json_pointer="/",
        )
    analysis = payload.get("analysis")
    plan = payload.get("plan")
    if not isinstance(analysis, Mapping):
        raise XingheAgentError(
            "PLANNER_SCHEMA_INVALID",
            "Amazon plan requires one analysis object.",
            json_pointer="/analysis",
        )
    if not isinstance(plan, Mapping):
        raise XingheAgentError(
            "PLANNER_SCHEMA_INVALID",
            "Amazon plan requires one plan object.",
            json_pointer="/plan",
        )

    def require_mapping(
        container: Mapping[str, Any], key: str, path: str
    ) -> Mapping[str, Any]:
        value = container.get(key)
        if not isinstance(value, Mapping) or not value:
            raise XingheAgentError(
                "PLANNER_SCHEMA_INVALID",
                f"Amazon Skill plan requires {key}.",
                json_pointer=path,
            )
        return value

    def require_string(container: Mapping[str, Any], key: str, path: str) -> str:
        value = container.get(key)
        if not isinstance(value, str) or not value.strip():
            raise XingheAgentError(
                "PLANNER_SCHEMA_INVALID",
                f"Amazon Skill plan requires a non-empty {key}.",
                json_pointer=path,
            )
        return value.strip()

    image_analysis = require_mapping(
        analysis, "product_image_analysis", "/analysis/product_image_analysis"
    )
    visible_facts = image_analysis.get("visible_facts")
    if (
        not isinstance(visible_facts, list)
        or not visible_facts
        or any(
            not isinstance(value, str) or not value.strip() for value in visible_facts
        )
    ):
        raise XingheAgentError(
            "PLANNER_SCHEMA_INVALID",
            "Product Image Analysis requires visible facts from supplied images.",
            json_pointer="/analysis/product_image_analysis/visible_facts",
        )
    confidence = require_mapping(
        analysis, "information_confidence", "/analysis/information_confidence"
    )
    for key in ("confirmed", "reasonable_inference", "needs_confirmation"):
        values = confidence.get(key)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise XingheAgentError(
                "PLANNER_SCHEMA_INVALID",
                f"Information Confidence requires an array for {key}.",
                json_pointer=f"/analysis/information_confidence/{key}",
            )
    for key in ("buyer_demand_map", "demand_to_selling_point_match"):
        values = analysis.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise XingheAgentError(
                "PLANNER_SCHEMA_INVALID",
                f"Amazon Skill plan requires {key}.",
                json_pointer=f"/analysis/{key}",
            )
    product_master = require_mapping(
        analysis,
        "product_master_description",
        "/analysis/product_master_description",
    )
    for key in (
        "category_and_identity",
        "silhouette_and_proportions",
        "color_material_and_texture",
        "structure_parts_and_accessories",
        "logos_labels_patterns_and_packaging",
        "scale_and_physics",
    ):
        require_string(
            product_master, key, f"/analysis/product_master_description/{key}"
        )
    unknowns = product_master.get("unknowns")
    if not isinstance(unknowns, list) or any(
        not isinstance(value, str) or not value.strip() for value in unknowns
    ):
        raise XingheAgentError(
            "AMAZON_PRODUCT_MASTER_INVALID",
            "Product Master Description unknowns must be an array.",
            json_pointer="/analysis/product_master_description/unknowns",
        )

    campaign_lock = require_mapping(
        plan, "campaign_style_lock", "/plan/campaign_style_lock"
    )
    for key in (
        "platform",
        "marketplace_language",
        "design_direction",
        "palette",
        "lighting",
        "typography_mood",
        "label_style",
        "density",
    ):
        require_string(campaign_lock, key, f"/plan/campaign_style_lock/{key}")
    consistency_lock = require_mapping(
        plan, "product_consistency_lock", "/plan/product_consistency_lock"
    )
    for key in (
        "product_identity",
        "structure",
        "color_area_ratio",
        "material_look",
        "logo_and_label_placement",
        "physical_scale",
    ):
        require_string(consistency_lock, key, f"/plan/product_consistency_lock/{key}")
    layout_lock = require_mapping(
        plan, "layout_diversity_lock", "/plan/layout_diversity_lock"
    )
    require_string(
        layout_lock,
        "structure_strategy",
        "/plan/layout_diversity_lock/structure_strategy",
    )
    formula_limit = layout_lock.get("large_product_top_title_limit")
    if (
        not isinstance(formula_limit, int)
        or isinstance(formula_limit, bool)
        or not 1 <= formula_limit <= 3
    ):
        raise XingheAgentError(
            "AMAZON_LAYOUT_DIVERSITY_INVALID",
            "The original Skill permits no more than three large-product-plus-top-title layouts.",
            json_pointer="/plan/layout_diversity_lock/large_product_top_title_limit",
        )

    screens = plan.get("screens")
    if not isinstance(screens, list) or len(screens) != AMAZON_EXPECTED_ITEM_COUNT:
        raise XingheAgentError(
            "AMAZON_ITEM_INVENTORY_INVALID",
            "Amazon Skill planning requires exactly 21 image rows.",
            json_pointer="/plan/screens",
        )
    expected_ids = list(AMAZON_SKILL_ROW_IDS)
    actual_ids = [
        screen.get("id") if isinstance(screen, Mapping) else None for screen in screens
    ]
    if actual_ids != expected_ids:
        raise XingheAgentError(
            "AMAZON_ITEM_INVENTORY_INVALID",
            "Amazon planner must return the canonical 21 Skill ids in order.",
            json_pointer="/plan/screens",
        )
    required = (
        "asset_type",
        "final_size",
        "buyer_question",
        "selling_point",
        "information_confidence",
        "visual_proof",
        "headline",
        "subcopy",
        "layout_direction",
        "source_material",
        "risk_note",
    )
    allowed_confidence = {
        "confirmed",
        "reasonable inference",
        "needs confirmation",
    }
    normalized_screens: list[dict[str, Any]] = []
    for index, (raw_screen, spec) in enumerate(
        zip(screens, AMAZON_ITEM_SPECS, strict=True)
    ):
        if not isinstance(raw_screen, Mapping):
            raise XingheAgentError(
                "AMAZON_PLAN_SCREEN_INVALID",
                "Amazon plan screen must be an object.",
                json_pointer=f"/plan/screens/{index}",
            )
        screen = dict(raw_screen)
        for key in required:
            value = screen.get(key)
            allows_blank = key == "subcopy" or (
                spec.item_key == "MAIN-01" and key == "headline"
            )
            if not isinstance(value, str) or (not allows_blank and not value.strip()):
                raise XingheAgentError(
                    "AMAZON_PLAN_SCREEN_INVALID",
                    f"Amazon Skill row requires {key}.",
                    json_pointer=f"/plan/screens/{index}/{key}",
                )
        if screen["information_confidence"] not in allowed_confidence:
            raise XingheAgentError(
                "AMAZON_PLAN_SCREEN_INVALID",
                "Information confidence must use the original Skill vocabulary.",
                json_pointer=f"/plan/screens/{index}/information_confidence",
            )
        image_copy = f"{screen['headline']} {screen['subcopy']}".casefold()
        unsupported_claims = [
            str(claim).strip()
            for claim in confidence["needs_confirmation"]
            if str(claim).strip()
        ]
        if any(claim.casefold() in image_copy for claim in unsupported_claims):
            raise XingheAgentError(
                "AMAZON_UNSUPPORTED_CLAIM_INVALID",
                "A needs-confirmation claim cannot appear in image copy.",
                json_pointer=f"/plan/screens/{index}/headline",
            )
        if screen["final_size"] != f"{spec.width}x{spec.height}":
            raise XingheAgentError(
                "AMAZON_TARGET_SIZE_MISMATCH",
                f"{screen['id']} has the wrong final_size.",
                json_pointer=f"/plan/screens/{index}/final_size",
            )
        screen["item_key"] = spec.item_key
        screen["role"] = AMAZON_ROLE_BY_ITEM_KEY[spec.item_key]
        normalized_screens.append(screen)

    if sources and not any(
        str(getattr(source, "source_role", "")) == "white_background_product"
        for source in sources
    ):
        raise XingheAgentError(
            "AMAZON_WHITE_BACKGROUND_SOURCE_REQUIRED",
            "Amazon MAIN-01 requires a real product source in the primary product role.",
            json_pointer="/analysis/product_image_analysis",
        )

    return {
        "analysis": dict(analysis),
        "plan": {
            "campaign_style_lock": dict(campaign_lock),
            "product_consistency_lock": dict(consistency_lock),
            "layout_diversity_lock": dict(layout_lock),
            "screens": normalized_screens,
        },
    }


def editable_amazon_structured_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct the planner object from the durable editable plan payload."""

    groups = payload.get("groups")
    pages = payload.get("pages")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(pages, list):
        raise AdapterValidationError(
            "AMAZON_PLAN_INVALID",
            "Amazon plan requires exactly one product group and 21 pages.",
        )
    group = groups[0]
    if not isinstance(group, Mapping):
        raise AdapterValidationError(
            "AMAZON_PLAN_INVALID",
            "Amazon product-group plan must be an object.",
        )
    return {
        "analysis": group.get("analysis"),
        "plan": {
            "campaign_style_lock": group.get("campaign_style_lock"),
            "product_consistency_lock": group.get("product_consistency_lock"),
            "layout_diversity_lock": group.get("layout_diversity_lock"),
            "screens": pages,
        },
    }


def validate_editable_amazon_plan_payload(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    from app.xinghe_agent import XingheAgentError

    try:
        from .amazon_suite_fast import POLICY_VERSION, validate_expanded_payload
        structured = editable_amazon_structured_plan(payload)
        if payload.get("workflow_version") == POLICY_VERSION:
            validate_expanded_payload(structured)
        else:
            validate_amazon_planner_payload(structured)
    except (AdapterValidationError, XingheAgentError) as exc:
        return [
            {
                "field": getattr(exc, "json_pointer", None) or "plan",
                "message": str(exc),
                "code": getattr(exc, "code", "AMAZON_PLAN_INVALID"),
            }
        ]
    return []


def synchronize_confirmed_amazon_plan(
    session: Any,
    job: Any,
    plan: Any,
) -> None:
    """Apply the confirmed Product Master and 21-row plan to every JobItem."""

    from sqlalchemy import select

    from app.models import JobItemRecord
    from app.services import canonical_hash
    from app.xinghe_agent import XingheAgentError

    try:
        from types import SimpleNamespace

        from .amazon_suite_fast import selected, validate_expanded_payload
        if selected(job.configuration):
            sources = tuple(SimpleNamespace(asset_id=asset_id, source_role="white_background_product")
                for asset_id in job.configuration.get("input", {}).get("white_background_product_asset_ids", []))
            structured = validate_expanded_payload(editable_amazon_structured_plan(plan.payload), sources=sources)
        else:
            structured = validate_amazon_planner_payload(
                editable_amazon_structured_plan(plan.payload)
            )
    except (AdapterValidationError, XingheAgentError):
        raise
    items = session.scalars(
        select(JobItemRecord)
        .where(JobItemRecord.job_id == job.id)
        .order_by(JobItemRecord.ordinal)
        .with_for_update()
    ).all()
    if [item.item_key for item in items] != list(AMAZON_ITEM_KEYS):
        raise AdapterValidationError(
            "AMAZON_ITEM_INVENTORY_INVALID",
            "Durable Amazon items do not match the confirmed plan.",
        )
    from .amazon_suite_fast import POLICY_VERSION, selected
    version = POLICY_VERSION if selected(job.configuration) else AMAZON_PLANNING_POLICY_VERSION
    payload = dict(plan.payload)
    resume_signature = canonical_hash(
        {
            "version": version,
            "input_signature": payload.get("input_signature"),
            "structured_plan": structured,
        }
    )
    context = {
        "schema_version": version,
        "analysis": structured["analysis"],
        "campaign_style_lock": structured["plan"]["campaign_style_lock"],
        "product_consistency_lock": structured["plan"]["product_consistency_lock"],
        "layout_diversity_lock": structured["plan"]["layout_diversity_lock"],
        "input_signature": payload.get("input_signature"),
        "resume_signature": resume_signature,
    }
    for item, screen in zip(items, structured["plan"]["screens"], strict=True):
        configuration = dict(item.configuration)
        configuration.update(
            {
                "workflow_version": version,
                "planning_policy_version": version,
                "resume_signature": resume_signature,
                "quality": "high",
                AMAZON_PLAN_CONFIGURATION_KEY: {**context, "screen": screen},
            }
        )
        item.configuration = configuration
        item.quality = "high"
        item.stage = "queued"
        item.state_version += 1
    payload["resume_signature"] = resume_signature
    payload["state"] = "confirmed"
    plan.payload = payload
    plan.payload_hash = canonical_hash(payload)


class AmazonVisualSuiteProvider:
    """Validate one Amazon JobItem and delegate real or fake generation.

    Contact sheets are intentionally absent from ``AMAZON_ITEM_KEYS`` and can
    never pass this adapter as a counted deliverable.
    """

    def __init__(
        self,
        delegate: Provider,
        *,
        manifest_validator: ManifestValidator | None = None,
    ) -> None:
        self.delegate = delegate
        self.manifest_validator = manifest_validator or BundledSkillManifestValidator()
        self.name = delegate.name
        self.model = delegate.model
        self.pricing_version = delegate.pricing_version

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        try:
            content_hash = self.manifest_validator.validate(
                AMAZON_VISUAL_SUITE_SKILL_ID
            )
            if not isinstance(content_hash, str) or not content_hash.strip():
                raise AdapterValidationError(
                    "SKILL_MANIFEST_VALIDATION_FAILED",
                    "The Skill manifest validator did not return a content hash.",
                )
            routed_request, spec = self.prepare_request(
                request,
                content_hash=content_hash.strip(),
            )
        except SkillRuntimeError as exc:
            return rejected_result(self, exc.code, str(exc))
        except (AdapterValidationError, NewImageSkillContractError) as exc:
            return rejected_result(self, exc.code, str(exc))
        except Exception as exc:
            return rejected_result(
                self,
                "SKILL_MANIFEST_VALIDATION_FAILED",
                f"Skill manifest validation failed ({type(exc).__name__}).",
            )

        result = await self.delegate.execute(routed_request)
        if result.outcome != ProviderOutcome.SUCCEEDED:
            return result
        return self._validate_output_contract(result, spec)

    def prepare_request(
        self,
        request: ProviderRequest,
        *,
        content_hash: str,
    ) -> tuple[ProviderRequest, AmazonItemSpec]:
        if request.operation != AMAZON_VISUAL_SUITE_OPERATION:
            raise AdapterValidationError(
                "OPERATION_NOT_SUPPORTED",
                (
                    f"{AMAZON_VISUAL_SUITE_SKILL_ID} only accepts "
                    f"{AMAZON_VISUAL_SUITE_OPERATION}."
                ),
            )
        job_configuration = job_configuration_from(request.configuration)
        marketplace = job_configuration.get("marketplace", AMAZON_DEFAULT_MARKETPLACE)
        if not isinstance(marketplace, str) or marketplace not in AMAZON_MARKETPLACES:
            raise AdapterValidationError(
                "AMAZON_MARKETPLACE_UNSUPPORTED",
                f"Unsupported Amazon marketplace: {marketplace!r}.",
            )
        item_configuration = _item_configuration(request.configuration)
        item_key = _required_item_key(item_configuration)
        spec = amazon_item_spec(item_key)
        _validate_contact_sheet_semantics(item_configuration)
        _validate_requested_dimensions(item_configuration, spec)
        _validate_group_semantics(item_configuration, spec)

        input_configuration = _job_input_configuration(request.configuration)
        plan_context = _amazon_plan_context(item_configuration, spec)
        source_assets = _amazon_source_assets(
            input_configuration,
            spec=spec,
            plan_context=plan_context,
        )
        item_ordinal = AMAZON_ITEM_KEYS.index(spec.item_key)
        prompt_input = _amazon_prompt_input(
            job_configuration,
            spec,
            plan_context,
            qa_redraw=item_configuration.get("qa_redraw"),
        )
        aspect_ratio = _aspect_ratio(spec.width, spec.height)
        adapter_configuration = {
            "schema_version": AMAZON_ADAPTER_CONTRACT_VERSION,
            "skill_id": AMAZON_VISUAL_SUITE_SKILL_ID,
            "skill_content_hash": content_hash,
            "operation": AMAZON_VISUAL_SUITE_OPERATION,
            "item_key": spec.item_key,
            "item_ordinal": item_ordinal,
            "source_assets": source_assets,
            "target": {
                "aspect_ratio": aspect_ratio,
                "width": spec.width,
                "height": spec.height,
                "quality": "high",
                "output_format": "jpeg",
                "fit": _safe_fit(spec),
            },
            "prompt_input": prompt_input,
        }
        audit_configuration = {
            "expected_item_count": AMAZON_EXPECTED_ITEM_COUNT,
            "canonical_item_keys": AMAZON_ITEM_KEYS,
            "contact_sheet": False,
            "item": {
                "item_key": spec.item_key,
                "delegate_item_key": spec.item_key,
                "group": spec.group,
                "group_ordinal": spec.group_ordinal,
                "target_width": spec.width,
                "target_height": spec.height,
                "deliverable_counted": True,
                "contact_sheet": False,
                "task": _amazon_task_contract(spec, plan_context["screen"]),
                "plan_resume_signature": plan_context["resume_signature"],
            },
        }
        return (
            replace(
                request,
                configuration={
                    **request.configuration,
                    SKILL_ADAPTER_CONFIGURATION_KEY: adapter_configuration,
                    AMAZON_AUDIT_CONFIGURATION_KEY: audit_configuration,
                },
            ),
            spec,
        )

    def _validate_output_contract(
        self,
        result: ProviderResult,
        spec: AmazonItemSpec,
    ) -> ProviderResult:
        artifact = result.persisted_artifact or result.artifact
        if artifact is None:
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                error_code="AMAZON_OUTPUT_ARTIFACT_MISSING",
                error_message=(
                    f"{spec.item_key} succeeded without a deliverable artifact."
                ),
                retryable=False,
                artifact=None,
                persisted_artifact=None,
            )
        if (artifact.width, artifact.height) != (spec.width, spec.height):
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                error_code="AMAZON_OUTPUT_DIMENSIONS_INVALID",
                error_message=(
                    f"{spec.item_key} requires {spec.width}x{spec.height}, got "
                    f"{artifact.width}x{artifact.height}."
                ),
                retryable=False,
                artifact=None,
                persisted_artifact=None,
            )
        if artifact.content_type.lower() != "image/jpeg":
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                error_code="AMAZON_OUTPUT_FORMAT_INVALID",
                error_message=f"{spec.item_key} requires an Amazon-ready JPEG export.",
                retryable=False,
                artifact=None,
                persisted_artifact=None,
            )
        return result


def _item_configuration(configuration: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = configuration.get("item")
    if nested is None:
        return configuration
    if not isinstance(nested, Mapping):
        raise AdapterValidationError(
            "AMAZON_ITEM_CONFIGURATION_INVALID",
            "Amazon item configuration must be an object.",
        )
    return nested


def _job_input_configuration(configuration: Mapping[str, Any]) -> Mapping[str, Any]:
    job = configuration.get("job")
    if job is None:
        return {}
    if not isinstance(job, Mapping):
        raise AdapterValidationError(
            "JOB_CONFIGURATION_INVALID",
            "Provider request job configuration must be an object.",
        )
    value = job.get("input")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AdapterValidationError(
            "JOB_INPUT_INVALID",
            "Provider request job input must be an object.",
        )
    return value


def _amazon_source_assets(
    input_configuration: Mapping[str, Any],
    *,
    spec: AmazonItemSpec,
    plan_context: Mapping[str, Any],
) -> list[dict[str, str]]:
    white_background = require_asset_ids(
        input_configuration,
        "white_background_product_asset_ids",
    )
    detail = validate_optional_asset_ids(input_configuration, "detail_asset_ids")
    package = validate_optional_asset_ids(input_configuration, "package_asset_ids")
    scene = validate_optional_asset_ids(input_configuration, "scene_asset_ids")
    brand = validate_optional_asset_ids(input_configuration, "brand_asset_ids")
    reference = validate_optional_asset_ids(input_configuration, "reference_asset_ids")
    competitor = validate_optional_asset_ids(
        input_configuration,
        "competitor_reference_asset_ids",
    )
    if spec.item_key == "MAIN-01":
        return role_labelled_assets(
            ("white_background_product", white_background),
            ("product_detail", detail),
        )

    source_material = str(plan_context["screen"]["source_material"])
    optional_sources = (
        ("package_reference", package),
        ("scene_reference", scene),
        ("brand_reference", brand),
        ("style_reference", reference),
        ("competitor_reference", competitor),
    )
    selected_optional_sources = tuple(
        (role, asset_ids)
        for role, asset_ids in optional_sources
        if asset_ids and _source_material_requests_role(source_material, role)
    )
    return role_labelled_assets(
        ("white_background_product", white_background),
        ("product_detail", detail),
        *selected_optional_sources,
    )


_SOURCE_MATERIAL_ROLE_TERMS: dict[str, tuple[str, ...]] = {
    "package_reference": (
        "package_reference",
        "package reference",
        "packaging",
        "package",
        "included items",
    ),
    "scene_reference": (
        "scene_reference",
        "scene reference",
        "lifestyle",
        "use environment",
        "real context",
        "scene",
    ),
    "brand_reference": (
        "brand_reference",
        "brand reference",
        "brand",
    ),
    "style_reference": (
        "style_reference",
        "style reference",
        "visual style",
    ),
    "competitor_reference": (
        "competitor_reference",
        "competitor reference",
        "competitor",
        "category convention",
    ),
}


def _source_material_requests_role(source_material: str, role: str) -> bool:
    normalized = " ".join(source_material.casefold().replace("-", " ").split())
    return any(
        term.casefold().replace("-", " ") in normalized
        for term in _SOURCE_MATERIAL_ROLE_TERMS[role]
    )


def _serialized_guidance(label: str, value: Any) -> list[str]:
    """Serialize Planner guidance without exceeding delegate field limits."""

    payload = _stable_json(value)
    guidance = f"{label}: {payload}"
    if len(guidance) <= _AMAZON_PROMPT_GUIDANCE_MAX_LENGTH:
        return [guidance]

    part_count = 1
    while True:
        prefix = f"{label} (part {part_count}/{part_count}): "
        payload_size = _AMAZON_PROMPT_GUIDANCE_MAX_LENGTH - len(prefix)
        if payload_size <= 0:
            raise ValueError("Amazon guidance label is too long to chunk safely.")
        resolved_count = (len(payload) + payload_size - 1) // payload_size
        if resolved_count == part_count:
            break
        part_count = resolved_count

    prefix = f"{label} (part {part_count}/{part_count}): "
    payload_size = _AMAZON_PROMPT_GUIDANCE_MAX_LENGTH - len(prefix)
    return [
        f"{label} (part {ordinal}/{part_count}): {payload[start : start + payload_size]}"
        for ordinal, start in enumerate(range(0, len(payload), payload_size), start=1)
    ]


def _amazon_prompt_input(
    job_configuration: Mapping[str, Any],
    spec: AmazonItemSpec,
    plan_context: Mapping[str, Any],
    *,
    qa_redraw: object = None,
) -> dict[str, Any]:
    """Compile one standalone prompt using only the original Skill contract."""

    listing_title = optional_string(job_configuration, "listing_title")
    bullet_points = optional_string_list(job_configuration, "bullet_points")
    listing_description = optional_string(job_configuration, "listing_description")
    a_plus_copy = optional_string(job_configuration, "a_plus_copy")
    product_facts = optional_string_list(job_configuration, "product_facts")
    parameters = optional_string_list(job_configuration, "parameters")
    evidence = optional_string_list(
        job_configuration,
        "certifications_or_evidence",
    )
    forbidden_claims = optional_string_list(job_configuration, "forbidden_claims")
    style_preferences = optional_string_list(job_configuration, "style_preferences")
    instruction = optional_string(job_configuration, "instruction")
    product_information = [
        value
        for value in (
            listing_title,
            *bullet_points,
            listing_description,
            a_plus_copy,
            *product_facts,
            *parameters,
            *evidence,
        )
        if value
    ]
    screen = plan_context["screen"]
    analysis = plan_context["analysis"]
    analysis_guidance = [
        *_serialized_guidance(
            "Product Master Description",
            analysis["product_master_description"],
        ),
        *_serialized_guidance(
            "Product Image Analysis",
            analysis["product_image_analysis"],
        ),
        *_serialized_guidance(
            "Information Confidence",
            analysis["information_confidence"],
        ),
        *_serialized_guidance("Buyer Demand Map", analysis["buyer_demand_map"]),
        *_serialized_guidance(
            "Demand-to-Selling-Point Match",
            analysis["demand_to_selling_point_match"],
        ),
    ]
    main_constraints: list[str] = []
    copy_requirements = [
        f"Headline: {screen['headline']}",
        f"Subcopy: {screen['subcopy']}",
    ]
    if spec.item_key == "MAIN-01":
        copy_requirements = []
        main_constraints = [
            "Use the real user-provided white_background_product source as product truth.",
            "Preserve the exact SKU, silhouette, proportions, color, structure, authentic trademark, logo, label, pattern, and readable product text already present on the product.",
            "Clean the surrounding background to pure white, center the product, preserve clear edges, and keep high subject occupancy.",
            "Do not return an empty or pure-color canvas and do not remove the product.",
            "Do not add lifestyle context, props, badges, ratings, claims, icons, new text, fake packaging, or accessories not shown or provided.",
        ]
    if isinstance(qa_redraw, Mapping):
        retry_guidance = qa_redraw.get("retry_guidance")
        summary = qa_redraw.get("summary")
        if isinstance(retry_guidance, str) and retry_guidance.strip():
            main_constraints.append(
                "QA redraw correction (follow this targeted fix): "
                + retry_guidance.strip()[:500]
            )
        if isinstance(summary, str) and summary.strip():
            main_constraints.append(
                "QA redraw context (preserve all other approved requirements): "
                + summary.strip()[:500]
            )
    return compact_prompt_input(
        {
            "marketplace": optional_string(
                job_configuration,
                "marketplace",
                default=AMAZON_DEFAULT_MARKETPLACE,
            ),
            "product_name": optional_string(job_configuration, "product_name"),
            "product_category": optional_string(job_configuration, "category"),
            "target_buyer": optional_string(job_configuration, "target_buyer"),
            "product_information": product_information,
            "listing_description": listing_description,
            "a_plus_copy": a_plus_copy,
            "product_facts": product_facts,
            "parameters": parameters,
            "certifications_or_evidence": evidence,
            "forbidden_claims": forbidden_claims,
            "color_palette": optional_string_list(job_configuration, "brand_colors"),
            "style_preferences": style_preferences,
            "user_instruction": instruction,
            "language": optional_string(job_configuration, "language", default="en-US"),
            "composition": spec.group,
            "creative_direction": [
                f"Asset type and final role: {screen['asset_type']}",
                f"Final size: {screen['final_size']}",
                f"Layout direction: {screen['layout_direction']}",
                f"Visual proof method: {screen['visual_proof']}",
            ],
            "copy_requirements": copy_requirements,
            "reference_guidance": [
                *analysis_guidance,
                *_serialized_guidance(
                    "Campaign Style Lock",
                    plan_context["campaign_style_lock"],
                ),
                *_serialized_guidance(
                    "Product Consistency Lock",
                    plan_context["product_consistency_lock"],
                ),
                *_serialized_guidance(
                    "Layout Diversity Lock",
                    plan_context["layout_diversity_lock"],
                ),
                f"Buyer question: {screen['buyer_question']}",
                f"Selling point: {screen['selling_point']}",
                f"Information confidence: {screen['information_confidence']}",
                f"Required source material: {screen['source_material']}",
            ],
            "negative_constraints": [
                _GLOBAL_RISK_GUARD,
                str(screen["risk_note"]),
                *(f"Forbidden claim: {value}" for value in forbidden_claims),
                *main_constraints,
            ],
            "image_count": 1,
        }
    )


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _amazon_plan_context(
    item_configuration: Mapping[str, Any],
    spec: AmazonItemSpec,
) -> Mapping[str, Any]:
    value = item_configuration.get(AMAZON_PLAN_CONFIGURATION_KEY)
    if not isinstance(value, Mapping):
        raise AdapterValidationError(
            "AMAZON_PLAN_CONTEXT_MISSING",
            "Amazon generation requires a confirmed persisted plan context.",
        )
    for key in (
        "analysis",
        "campaign_style_lock",
        "product_consistency_lock",
        "layout_diversity_lock",
        "screen",
    ):
        if not isinstance(value.get(key), Mapping) or not value[key]:
            raise AdapterValidationError(
                "AMAZON_PLAN_CONTEXT_MISSING",
                f"Amazon confirmed plan context is missing {key}.",
            )
    resume_signature = value.get("resume_signature")
    if not isinstance(resume_signature, str) or len(resume_signature) != 64:
        raise AdapterValidationError(
            "AMAZON_PLAN_CONTEXT_MISSING",
            "Amazon confirmed plan context has no valid resume signature.",
        )
    screen = value["screen"]
    if screen.get("item_key") != spec.item_key:
        raise AdapterValidationError(
            "AMAZON_PLAN_ITEM_MISMATCH",
            "Amazon confirmed plan screen does not match the durable item.",
        )
    analysis = value["analysis"]
    for key in (
        "product_image_analysis",
        "information_confidence",
        "product_master_description",
    ):
        if not isinstance(analysis.get(key), Mapping) or not analysis[key]:
            raise AdapterValidationError(
                "AMAZON_PLAN_CONTEXT_MISSING",
                f"Amazon confirmed plan has no {key}.",
            )
    return value


def _amazon_task_contract(
    spec: AmazonItemSpec,
    screen: Mapping[str, Any],
) -> dict[str, str]:
    """Return the confirmed Skill task row for exactly one deliverable."""

    return {
        "id": AMAZON_SKILL_ROW_ID_BY_ITEM_KEY[spec.item_key],
        "asset_type": str(screen["asset_type"]),
        "final_size": str(screen["final_size"]),
        "buyer_question": str(screen["buyer_question"]),
        "selling_point": str(screen["selling_point"]),
        "information_confidence": str(screen["information_confidence"]),
        "visual_proof": str(screen["visual_proof"]),
        "headline": str(screen["headline"]),
        "subcopy": str(screen["subcopy"]),
        "layout_direction": str(screen["layout_direction"]),
        "source_material": str(screen["source_material"]),
        "risk_note": str(screen["risk_note"]),
    }


def _safe_fit(spec: AmazonItemSpec) -> str:
    """Choose a no-loss final-size strategy for each Amazon asset group."""

    if spec.group == "main":
        return "pad"
    return "cover"


def _aspect_ratio(width: int, height: int) -> str:
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _required_item_key(configuration: Mapping[str, Any]) -> str:
    value = configuration.get("item_key")
    if not isinstance(value, str) or not value.strip():
        raise AdapterValidationError(
            "AMAZON_ITEM_KEY_MISSING",
            "Amazon item configuration requires item_key.",
        )
    return value.strip()


def _validate_contact_sheet_semantics(configuration: Mapping[str, Any]) -> None:
    if configuration.get("contact_sheet") is True:
        raise AdapterValidationError(
            "AMAZON_CONTACT_SHEET_NOT_DELIVERABLE",
            "Contact sheets cannot be counted among the 21 Amazon deliverables.",
        )


def _validate_group_semantics(
    configuration: Mapping[str, Any],
    spec: AmazonItemSpec,
) -> None:
    value = configuration.get("group")
    if value is None:
        return
    if str(value).strip().lower() != spec.group:
        raise AdapterValidationError(
            "AMAZON_ITEM_GROUP_MISMATCH",
            f"{spec.item_key} belongs to group {spec.group}.",
        )


def _validate_requested_dimensions(
    configuration: Mapping[str, Any],
    spec: AmazonItemSpec,
) -> None:
    width, height = _requested_dimensions(configuration)
    if width is None or height is None:
        raise AdapterValidationError(
            "AMAZON_TARGET_SIZE_MISSING",
            f"{spec.item_key} requires an explicit target size.",
        )
    if (width, height) != (spec.width, spec.height):
        raise AdapterValidationError(
            "AMAZON_TARGET_SIZE_MISMATCH",
            (
                f"{spec.item_key} requires {spec.width}x{spec.height}, got "
                f"{width}x{height}."
            ),
        )


def _requested_dimensions(
    configuration: Mapping[str, Any],
) -> tuple[int | None, int | None]:
    width = configuration.get("target_width")
    height = configuration.get("target_height")
    target_size = configuration.get("target_size")
    if isinstance(target_size, Mapping):
        width = target_size.get("width", width)
        height = target_size.get("height", height)
    elif isinstance(target_size, str) and "x" in target_size.lower():
        raw_width, raw_height = target_size.lower().split("x", maxsplit=1)
        width, height = raw_width.strip(), raw_height.strip()
    return _positive_int_or_none(width), _positive_int_or_none(height)


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed > 0 else None
