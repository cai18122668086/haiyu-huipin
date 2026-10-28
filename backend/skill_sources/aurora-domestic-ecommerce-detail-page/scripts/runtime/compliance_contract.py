from __future__ import annotations

from .state import HarnessError, require_fields


COSMETIC_CATEGORY_MARKERS = ("化妆品", "防晒", "护肤", "美白", "祛斑", "面霜", "精华", "喷雾")
HIGH_RISK_CLAIM_MARKERS = ("防晒", "SPF", "PA+", "美白", "祛斑", "防脱", "育发", "染发", "烫发", "脱毛", "除臭")
EVIDENCE_LEVELS = {"REGULATORY_RECORD", "DOCUMENTED_TEST", "PACKAGING_ONLY", "USER_ASSERTION"}
SOURCE_TYPES = {"REGULATORY", "TEST_REPORT", "PACKAGE", "USER_MATERIAL"}


def _is_cosmetic(category: object) -> bool:
    text = str(category or "")
    return any(marker in text for marker in COSMETIC_CATEGORY_MARKERS)


def validate_high_risk_cosmetic_claims(raw: dict) -> None:
    if not _is_cosmetic(raw.get("category")):
        return
    for fact in raw.get("verified_facts", []):
        fact_text = str(fact.get("text", "")).upper()
        automatically_high_risk = any(marker.upper() in fact_text for marker in HIGH_RISK_CLAIM_MARKERS)
        if fact.get("risk_route") != "HIGH_RISK_CLAIM" and not automatically_high_risk:
            continue
        fact["risk_route"] = "HIGH_RISK_CLAIM"
        contract = fact.get("claim_risk_contract")
        if not isinstance(contract, dict):
            raise HarnessError(f"High-risk cosmetic fact {fact.get('id')} requires claim_risk_contract")
        require_fields(
            contract,
            (
                "evidence_level", "allowed_phrases", "forbidden_phrases",
                "evidence_sources", "effect_implication_allowed", "qualification",
            ),
            f"claim risk contract for {fact.get('id')}",
        )
        if contract["evidence_level"] not in EVIDENCE_LEVELS:
            raise HarnessError(f"High-risk cosmetic fact {fact.get('id')} has an unsupported evidence level")
        if not isinstance(contract["allowed_phrases"], list) or not contract["allowed_phrases"]:
            raise HarnessError(f"High-risk cosmetic fact {fact.get('id')} needs at least one allowed phrase")
        if not isinstance(contract["forbidden_phrases"], list):
            raise HarnessError(f"High-risk cosmetic fact {fact.get('id')} forbidden_phrases must be a list")
        if not isinstance(contract["effect_implication_allowed"], bool):
            raise HarnessError(f"High-risk cosmetic fact {fact.get('id')} effect_implication_allowed must be boolean")
        sources = contract["evidence_sources"]
        if not isinstance(sources, list) or not sources:
            raise HarnessError(f"High-risk cosmetic fact {fact.get('id')} needs a machine-auditable evidence source")
        for source in sources:
            require_fields(source, ("type", "ref"), f"evidence source for {fact.get('id')}")
            if source["type"] not in SOURCE_TYPES:
                raise HarnessError(f"High-risk cosmetic fact {fact.get('id')} has an unsupported evidence source type")


def validate_high_risk_plan_copy(raw: dict, plan: dict) -> None:
    facts = {item["id"]: item for item in raw.get("verified_facts", [])}
    for module in plan.get("modules", []):
        claim_ids = [module.get("lead_claim_id"), *module.get("supporting_claim_ids", [])]
        copy_items = module.get("visible_copy", [])
        texts = [str(item.get("text", "") if isinstance(item, dict) else item) for item in copy_items]
        for claim_id in claim_ids:
            fact = facts.get(claim_id) or {}
            contract = fact.get("claim_risk_contract")
            if not contract:
                continue
            for text in texts:
                forbidden = [phrase for phrase in contract["forbidden_phrases"] if phrase and phrase in text]
                if forbidden:
                    raise HarnessError(
                        f"Module {module.get('id')} uses forbidden high-risk wording for {claim_id}: {forbidden}"
                    )
            bound = [
                item for item in copy_items
                if isinstance(item, dict) and claim_id in item.get("fact_ids", [])
            ]
            if not bound:
                raise HarnessError(
                    f"Module {module.get('id')} must bind high-risk fact {claim_id} to visible_copy.fact_ids"
                )
            allowed = set(contract["allowed_phrases"])
            if any(str(item.get("text", "")) not in allowed for item in bound):
                raise HarnessError(
                    f"Module {module.get('id')} uses wording outside allowed_phrases for high-risk fact {claim_id}"
                )
            expression = module.get("module_visual_expression_contract") or module.get("expression") or {}
            if contract["effect_implication_allowed"] is False and expression.get("transformation_required") is True:
                raise HarnessError(
                    f"Module {module.get('id')} implies an effect transformation forbidden for high-risk fact {claim_id}"
                )
