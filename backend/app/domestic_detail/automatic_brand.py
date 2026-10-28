"""Website logo decisions, separate from product facts and human declarations."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .plans import canonical_hash

VERSION = "website-automatic-brand-v1"
NO_LOGO_BASIS = (
    "网站后台默认品牌策略：缺少可核实的独立标志来源时不新增独立品牌标志；"
    "保留商品本体已有文字和标识。此记录不代表用户声明商品无品牌。"
)
PRESERVE_MARKS = (
    "Preserve the product's existing packaging text and marks exactly. "
    "BRANDLESS is only the native compatibility flag for omitting independent "
    "logo graphics; never erase on-product marks or claim the product has no brand."
)


def enabled(settings: Mapping[str, Any]) -> bool:
    return (
        settings.get("website_contract_version")
        in {"WEBSITE_DIRECT_V2", "WEBSITE_LEAN_V1"}
        and settings.get("market_region") == "domestic"
    )


def fallback_basis(settings: Mapping[str, Any]) -> str | None:
    explicit = settings.get("brandless_confirmation")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    return NO_LOGO_BASIS if enabled(settings) else None


def visible_observation(value: object) -> bool:
    from .facts import DetailIntakeError, _specific_observation

    try:
        _specific_observation(value, "/brand", minimum_length=1)
    except DetailIntakeError:
        return False
    return True


def brand_conflict(
    row: Mapping[str, Any], facts: Mapping[str, Mapping[str, Any]]
) -> bool:
    """Only brand observations can be omitted; specifications never qualify."""
    topic = str(row.get("topic", ""))
    if not re.search(r"品牌|标识|标志|\bbrand\b|\blogo\b", topic, re.I):
        return False
    refs = row.get("alternative_fact_ids")
    if not isinstance(refs, list) or not refs:
        return False
    for ref in refs:
        fact = facts.get(str(ref), {})
        if fact.get("source_class") != "OBSERVED_PRODUCT_EVIDENCE":
            return False
        key = str(fact.get("atomic_key", ""))
        text = str(fact.get("text", ""))
        if not (
            key.startswith(("brand.", "branding.", "logo."))
            or re.search(r"品牌|标识|标志|字样|\bbrand\b|\blogo\b", text, re.I)
        ):
            return False
        if re.search(
            r"净含量|容量|营养|成分|配方|尺寸|材质|重量|\b(?:weight|capacity|ingredient)\b",
            text,
            re.I,
        ):
            return False
    return True


def project(
    payload: Mapping[str, Any],
    settings: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    *,
    force_no_logo: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
    """Project redundant brand fields from original evidence, or omit a logo.

    No geometry is invented. Missing identity needs one separately receipted
    pixel analysis. Original model bytes and omitted observations stay intact.
    """
    value = deepcopy(dict(payload))
    if not enabled(settings):
        return value, None, False
    force_no_logo = force_no_logo or bool(settings.get("brandless_confirmation"))
    original_sha = canonical_hash(payload)
    facts = {str(row.get("id")): row for row in value.get("observed_facts", [])}
    conflicts = value.get("source_conflicts") or []
    omitted = [row for row in conflicts if brand_conflict(row, facts)]
    omitted_ids = {ref for row in omitted for ref in row["alternative_fact_ids"]}
    # A brand observation used in a genuine specification conflict must remain.
    other_refs = {
        ref
        for row in conflicts
        if row not in omitted
        for ref in row.get("alternative_fact_ids", [])
    }
    if omitted_ids & other_refs:
        omitted, omitted_ids = [], set()
    if omitted:
        value["source_conflicts"] = [row for row in conflicts if row not in omitted]
        value["observed_facts"] = [
            row for row in value["observed_facts"] if row["id"] not in omitted_ids
        ]
        scan = value.get("conflict_scan") or {}
        remaining = [row["id"] for row in value["source_conflicts"]]
        scan.update(
            conflict_ids=remaining, status="CONFLICTS_FOUND" if remaining else "CLEAR"
        )
        value["conflict_scan"] = scan
        force_no_logo = True
    identity = value.get("identity")
    receipt: dict[str, Any] = {
        "version": VERSION,
        "decision_source": "website_default",
        "source_payload_sha256": original_sha,
        "omitted_brand_conflicts": deepcopy(omitted),
        "omitted_brand_observations": [
            deepcopy(facts[key]) for key in sorted(omitted_ids)
        ],
        "product_brandless_claim": False,
    }
    # Material facts still wait for their own resolution, never a brand choice.
    value["brand_confirmation_required"] = False
    value.pop("brand_observation", None)
    if not isinstance(identity, dict):
        receipt["decision"] = "reanalyze_identity_without_brand_question"
        return value, receipt, not value.get("source_conflicts")
    brand_value = identity.get("brand_logo_source")
    brand = dict(brand_value) if isinstance(brand_value, Mapping) else {}
    product_ids = {
        row["asset_id"] for row in sources if row.get("role") == "product_reference"
    }
    branded = brand.get("mode", "BRANDED") == "BRANDED"
    logo_text, region = brand.get("observed_logo_text"), brand.get("observed_region")
    if branded and not force_no_logo and brand.get("image_id") in product_ids:
        if not (visible_observation(logo_text) and visible_observation(region)):
            candidates = [
                row
                for row in value.get("observed_facts", [])
                if row.get("atomic_key") == "brand.logo_text"
                and row.get("evidence_ref") == brand["image_id"]
            ]
            if len(candidates) == 1:
                fact = candidates[0]
                phrases = (fact.get("claim_risk_contract") or {}).get(
                    "allowed_phrases"
                ) or []
                names = fact.get("named_elements") or []
                exact = [
                    phrase
                    for phrase in phrases
                    if isinstance(phrase, str)
                    and phrase.strip()
                    and names
                    and all(isinstance(name, str) and name in phrase for name in names)
                ]
                if (
                    exact
                    and isinstance(fact.get("observed_region"), str)
                    and fact["observed_region"].strip()
                ):
                    logo_text, region = min(exact, key=len), fact["observed_region"]
                    brand.update(observed_logo_text=logo_text, observed_region=region)
                    receipt["projected_fact_id"] = fact["id"]
        if visible_observation(logo_text) and visible_observation(region):
            brand.update(
                mode="BRANDED",
                authorization_basis="USER_SUPPLIED_CURRENT_PRODUCT",
                readability="READABLE",
                usage="FIRST_SCREEN_INDEPENDENT_LOGO",
            )
            identity["brand_logo_source"] = brand
            receipt["decision"] = "verified_product_logo"
            receipt["brand_source_sha256"] = canonical_hash(brand)
            return value, receipt, False
    identity["brand_logo_source"] = {
        "mode": "BRANDLESS",
        "image_id": "NONE",
        "authorization_basis": "NOT_APPLICABLE",
        "readability": "NOT_APPLICABLE",
        "usage": "FORBIDDEN_ALL_SCREENS",
        "brandless_evidence": fallback_basis(settings),
    }
    receipt.update(decision="omit_independent_logo", basis=fallback_basis(settings))
    return value, receipt, False


def prompt_policy(settings: Mapping[str, Any]) -> str:
    if not enabled(settings):
        return ""
    return (
        "WEBSITE AUTOMATIC BRAND POLICY: Never ask the user to confirm a brand, "
        "declare the product brandless, or upload a readable logo. "
        "Only the target product's own readable marks are brand evidence. "
        "Background objects, other packages, watermarks and style references "
        "are not the target brand and do not create brand/SKU conflicts. "
        "If an independent logo is not verifiable, continue pixel-based identity "
        "analysis with BRANDLESS/FORBIDDEN_ALL_SCREENS and the exact server "
        "brandless_evidence basis; set brand_confirmation_required=false. "
        "This default is a rendering decision, never a user declaration. "
        + PRESERVE_MARKS
        + " Genuine non-brand specification conflicts still stop. "
    )
