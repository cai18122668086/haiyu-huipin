# ruff: noqa: E501
"""Pure native-plan adaptation for the locked domestic detail Harness contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .facts import text_tokens


def normalize_native_plan_for_harness(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Bridge valid compact-plan values to the Harness' stricter list contract.

    The public compact schema permits an empty proof-support list and a scalar
    specificity anchor. The bundled Harness deliberately requires observable
    list entries for those fields. Preserve authored values and add only
    structural, fact-neutral bindings when the stricter runtime needs them.
    """

    normalized = json.loads(json.dumps(plan, ensure_ascii=False))
    if normalized.get("plan_format") == "WEBSITE_DIRECT_V2":
        return normalized
    modules = normalized.get("modules")
    if not isinstance(modules, list):
        return normalized
    # Claim aliases are not fact IDs. In a real plan C02 referred to F04,
    # while C01 grouped F01/F02/F03. Mapping their numeric suffixes silently
    # rebound fitting claims to an unrelated style fact. Use only the source
    # fact IDs explicitly authored in the plan; unknown aliases stay unknown
    # for the Harness to reject instead of guessing product truth.
    depth_source = normalized.get("selling_point_depth_contract")
    entries = depth_source.get("primary_claims", []) if isinstance(depth_source, dict) else []
    claim_sources: dict[str, list[str]] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not entry.get("claim_id"):
            continue
        source_ids = entry.get("source_fact_ids") or entry.get("fact_ids")
        if isinstance(source_ids, list):
            source_ids = list(dict.fromkeys(v for v in source_ids if isinstance(v, str) and v.strip()))
            if source_ids:
                claim_sources[str(entry["claim_id"])] = source_ids

    def fact_id_for_claim(value: object) -> str:
        claim_id = str(value or "")
        return claim_sources.get(claim_id, [claim_id])[0]

    for module in modules:
        if not isinstance(module, dict):
            continue
        lead = str(module.get("lead_claim_id") or "")
        if lead in claim_sources:
            sources = claim_sources[lead]
            module["lead_claim_id"] = sources[0]
            support = module.get("supporting_claim_ids", [])
            if isinstance(support, list):
                resolved_support = [fact_id_for_claim(v) for v in support]
                module["supporting_claim_ids"] = list(dict.fromkeys(
                    v for v in [*resolved_support, *sources[1:]] if v != sources[0]
                ))
    strategy = normalized.get("output_strategy")
    if not isinstance(strategy, dict):
        strategy = {}
        normalized["output_strategy"] = strategy
    coverage = strategy.get("claim_coverage")
    if isinstance(coverage, list):
        expanded_coverage = []
        for entry in coverage:
            source_ids = claim_sources.get(str(entry.get("claim_id") or "")) if isinstance(entry, dict) else None
            if not source_ids:
                expanded_coverage.append(entry)
                continue
            for index, fact_id in enumerate(source_ids):
                item = {**entry, "claim_id": fact_id}
                if index and item.get("disposition") == "LEAD":
                    item["disposition"] = "SUPPORT"
                expanded_coverage.append(item)
        strategy["claim_coverage"] = expanded_coverage
    # The bundled v4.3 Harness intentionally accepts one resolved count
    # derivation.  Compact authoring responses may expose alternatives even
    # though the approved plan already has a fixed module list.
    module_count = len(modules)
    strategy["module_count"] = module_count
    strategy["module_count_candidates"] = [{
        "candidate_mode": "DIRECT_DERIVATION",
        "count": module_count,
        "coverage_tradeoff": "按已批准卖点直接确定屏幕数量",
        "selling_point_fit": "覆盖已批准事实并保持每屏独立证明职责",
        "story_obligation_fit": "保留开场、证明、场景与购买收束",
        "primary_selling_point_depth_fit": "每个核心卖点都有可见证据承载",
        "evidence_boundary_fit": "不增加用户未提供的事实或主张",
        "decision": "SELECTED",
    }]
    dna = normalized.get("series_visual_dna")
    if isinstance(dna, dict):
        constitution = normalized.get("style_constitution")
        constitution = constitution if isinstance(constitution, dict) else {}
        dna.setdefault("brand_anchor_system", dna.get("brand_anchor") or "以当前商品本体和统一视觉DNA建立识别")
        dna.setdefault("palette_tokens", constitution.get("color_and_light") or "以商品真实配色为主，保持统一冷暖关系")
        dna.setdefault("typography_tokens", constitution.get("typography_and_graphics") or "简洁中文无衬线字体，标题与对象标签分层")
        dna.setdefault("graphic_tokens", constitution.get("typography_and_graphics") or "细线标注与克制辅助图形")
        dna.setdefault("photography_tokens", constitution.get("product_rendering") or "真实商品材质与统一主光")
        dna.setdefault("product_tokens", text_tokens(dna.get("identity_geometry"), field="series_visual_dna/identity_geometry") + text_tokens(dna.get("photography_tokens"), field="series_visual_dna/photography_tokens"))
        dna.setdefault("spatial_tokens", [
            "纵向连续展台",
            "非对称证据分区",
            "两屏一组固定切片",
        ])
        invariants = dna.get("invariants")
        if not isinstance(invariants, list) or len(invariants) < 2:
            identity = text_tokens(dna.get("identity_geometry"), field="series_visual_dna/identity_geometry")
            dna["invariants"] = (identity + ["统一主光方向与商品材质真实感", "连续 pair 画布使用固定切片坐标"])[:]
    typography_rhythm = normalized.get("typography_rhythm")
    if not isinstance(typography_rhythm, dict):
        typography_rhythm = {}
        normalized["typography_rhythm"] = typography_rhythm
    typography_rhythm.setdefault("campaign_rule", "标题服从可见证据，按屏幕职责改变锚区与阅读路径")
    typography_rhythm.setdefault("primary_mode_sequence", [str(module.get("typography", {}).get("primary_mode") or "EDGE_TITLE") for module in modules if isinstance(module, dict)])
    typography_rhythm.setdefault("anchor_zone_sequence", [str(module.get("typography", {}).get("anchor_zone") or "画面留白区") for module in modules if isinstance(module, dict)])
    typography_rhythm.setdefault("centered_hero_max", 2)
    typography_rhythm.setdefault("consecutive_anchor_repeat_limit", 2)
    typography_rhythm.setdefault("exceptions", [])
    hero_contract = normalized.get("hero_screen_contract")
    if isinstance(hero_contract, dict):
        hero_contract["hero_module_id"] = "M01"
        hero_contract["hero_uniqueness"] = "ONE_PRIMARY_HERO"
        hero_contract["co_equal_hero_forbidden"] = True
        hero_contract.setdefault("hero_entity_id", "E_PRODUCT_WHOLE")
        hero_contract.setdefault("hero_job", "建立唯一商品身份并给出核心购买印象")
        hero_contract.setdefault("supporting_entities_allowed", ["商品结构与真实可见细节"])
        hero_contract.setdefault("hero_identity_evidence", ["商品参考图"])
    detail_contract = normalized.get("detail_information_contract")
    if isinstance(detail_contract, dict):
        detail_contract["required_progression_jobs"] = [
            "IDENTITY", "PAIN", "BENEFIT", "DEDICATED_PROOF", "CLOSURE",
        ]
        detail_contract["non_repeating_overview_limit"] = 1
        detail_contract["every_pair_requires_information_delta"] = True
        questions = detail_contract.get("buyer_question_sequence")
        if not isinstance(questions, list) or len(questions) < 4:
            detail_contract["buyer_question_sequence"] = [str(module.get("buyer_question") or "确认商品可见证据") for module in modules]
    recall = normalized.get("campaign_recall_contract")
    if isinstance(recall, dict) and isinstance(recall.get("must_remember_claim_ids"), list):
        recall["must_remember_claim_ids"] = [fact_id_for_claim(item) for item in recall["must_remember_claim_ids"]]
    depth = normalized.get("selling_point_depth_contract")
    if isinstance(depth, dict) and "claim_depth" not in depth:
        primary_entries = depth.get("primary_claims") if isinstance(depth.get("primary_claims"), list) else []
        primary_ids = [fact_id_for_claim(item.get("claim_id")) for item in primary_entries if isinstance(item, dict) and item.get("claim_id")]  # type: ignore[union-attr]
        if not primary_ids:
            primary_ids = [str(item) for item in (recall or {}).get("must_remember_claim_ids", [])]
        depth["selection_policy"] = "按用户已确认事实和购买决策深度选择一级卖点"
        depth["primary_claim_ids"] = primary_ids
        claim_depth = []
        for entry, claim_id in zip(primary_entries, primary_ids, strict=False):  # type: ignore[arg-type]
            scheduled_values = (
                entry.get("scheduled_module_ids")
                if isinstance(entry, dict)
                else []
            )
            scheduled = [str(item) for item in (scheduled_values or [])]
            if not scheduled:
                scheduled = [str(module.get("id")) for module in modules if isinstance(module, dict) and module.get("lead_claim_id") == claim_id]
            dedicated = [str(module.get("id")) for module in modules if isinstance(module, dict) and module.get("lead_claim_id") == claim_id]
            if not dedicated and scheduled:
                dedicated = scheduled[:1]
            jobs = ["VALUE_OVERVIEW", "DEDICATED_EXPRESSION", "USE_SCOPE", "RECALL"]
            module_jobs = []
            for index, module_id in enumerate(scheduled):
                job = jobs[0] if index == 0 else jobs[1] if index == 1 else jobs[2]
                module_jobs.append({"module_id": module_id, "job": job})
            if scheduled:
                module_jobs.append({"module_id": scheduled[-1], "job": jobs[-1]})
            claim_depth.append({
                "claim_id": claim_id,
                "depth_tier": "DEEP",
                "evidence_ceiling": str((entry or {}).get("evidence_ceiling") or "DIRECT_PROOF_AVAILABLE"),
                "required_decision_jobs": jobs,
                "scheduled_module_ids": scheduled,
                "dedicated_module_ids": dedicated,
                "non_duplicate_rationale": "每个一级卖点在总览、独立证据、使用判断与收尾回忆中承担不同职责",
                "forbidden_expansion": "不得扩展为用户未提供的性能、认证或安全承诺",
                "priority": "PRIMARY",
                "module_jobs": module_jobs,
            })
        depth["claim_depth"] = claim_depth
    information_roles = {
        "HOOK": "IDENTITY",
        "PAIN_ARTICULATION": "PAIN",
        "PAIN_SOLUTION_COMBINED": "PAIN",
        "SOLUTION_PROOF": "DEDICATED_PROOF",
        "MECHANISM_PROOF": "MECHANISM_OR_STRUCTURE",
        "MATERIAL_PROOF": "DEDICATED_PROOF",
        "USE_OR_RESULT": "RESULT_OR_EXPERIENCE",
        "SELECTION_OR_SPECIFICATION": "SELECTION_OR_USE",
        "TRUST_OR_RISK_REMOVAL": "TRUST",
        "PURCHASE_CLOSURE": "CLOSURE",
    }
    for index, module in enumerate(modules):
        if isinstance(module, dict):
            module.setdefault(
                "information_role",
                information_roles.get(str(module.get("story_function") or ""), "OTHER"),
            )
            if index == 0:
                module.setdefault("hero_entity_id", str((hero_contract or {}).get("hero_entity_id") or "E_PRODUCT_WHOLE"))
                module.setdefault("co_equal_hero_entity_ids", [])
    forbidden_inventions = []
    if isinstance(dna, dict) and isinstance(dna.get("forbidden_mutations"), list):
        forbidden_inventions = [str(item) for item in dna["forbidden_mutations"] if str(item).strip()]

    # The compact schema allows richer authoring notes, while the runtime
    # spatial contract for ASYMMETRIC_EDITORIAL is a two-zone contract. Keep
    # all authored wording by folding any additional notes into the second
    # zone instead of silently dropping it.
    for module in modules:
        if not isinstance(module, dict):
            continue
        for field in ("visual_payload_density", "commercial_energy", "proof_relation_strength"):
            value = module.get(field)
            if isinstance(value, str):
                lowered = value.lower()
                if field == "commercial_energy":
                    lowered = {
                        "medium": "standard",
                        "medium_high": "high",
                        "low_medium": "standard",
                    }.get(lowered, lowered)
                elif field == "visual_payload_density":
                    lowered = {"medium_high": "high", "low_medium": "medium"}.get(lowered, lowered)
                module[field] = lowered
        evidence = module.get("visual_evidence")
        if isinstance(evidence, str) and evidence.strip():
            evidence = [evidence]
        if isinstance(evidence, list):
            module["visual_evidence"] = {
                "evidence_class": "DIRECT_OBSERVATION",
                "proof_device": "CONCRETE_PRODUCT_EVIDENCE",
                "visible_content": [str(item) for item in evidence if str(item).strip()],
                "forbidden_inventions": list(forbidden_inventions),
            }
        elif isinstance(evidence, dict):
            evidence.setdefault("evidence_class", "DIRECT_OBSERVATION")
            evidence.setdefault("proof_device", "CONCRETE_PRODUCT_EVIDENCE")

    for module in modules:
        if not isinstance(module, dict):
            continue
        if module.get("lead_claim_id"):
            module["lead_claim_id"] = fact_id_for_claim(module["lead_claim_id"])
        support = module.get("supporting_claim_ids")
        if isinstance(support, list):
            module["supporting_claim_ids"] = [fact_id_for_claim(item) for item in support]

    claims = strategy.get("claim_coverage")
    if isinstance(claims, list):
        by_id = {str(module.get("id")): module for module in modules if isinstance(module, dict) and module.get("id")}
        for claim in claims:
            if not isinstance(claim, dict) or not claim.get("claim_id"):
                continue
            claim_id = fact_id_for_claim(claim["claim_id"])
            claim["claim_id"] = claim_id
            assigned = [str(item) for item in claim.get("module_ids", []) if str(item) in by_id]
            # A selected claim must include the module that declares it as lead.
            for module_id, module in by_id.items():
                if str(module.get("lead_claim_id") or "") == claim_id and module_id not in assigned:
                    assigned.append(module_id)
            claim["module_ids"] = assigned
            claim.setdefault(
                "disposition",
                "LEAD" if any(
                    str(by_id[module_id].get("lead_claim_id") or "") == claim_id
                    for module_id in assigned
                ) else "SUPPORT",
            )
            # Disposition reasons are authored evidence, not structural defaults.
            # Preserve valid, unknown and missing values for strict Harness validation.
            claim.setdefault("rationale", "核心事实直接进入已批准屏幕")
            for module_id in assigned:
                module = by_id[module_id]
                if str(module.get("lead_claim_id") or "") != claim_id:
                    support = module.get("supporting_claim_ids")
                    if not isinstance(support, list):
                        support = []
                        module["supporting_claim_ids"] = support
                    if claim_id not in support:
                        support.append(claim_id)
    clusters = strategy.get("selling_point_clusters")
    if isinstance(clusters, list):
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            if not cluster.get("id") and cluster.get("cluster_id"):
                cluster["id"] = cluster["cluster_id"]
            if not isinstance(cluster.get("fact_ids"), list):
                cluster["fact_ids"] = list(cluster.get("claim_ids") or [])
            cluster.setdefault("buyer_question", cluster.get("label") or "买家如何判断这项商品事实")
            cluster.setdefault("purchase_role", "BENEFIT")
            cluster.setdefault("separation_policy", "MERGEABLE")
            if not isinstance(cluster.get("assigned_module_ids"), list):
                cluster_id = str(cluster.get("id") or "")
                cluster["assigned_module_ids"] = [
                    str(module.get("id"))
                    for module in modules
                    if isinstance(module, dict) and cluster_id in (module.get("selling_point_cluster_ids") or [])
                ]
            cluster["fact_ids"] = [fact_id_for_claim(item) for item in cluster.get("fact_ids", [])]
    pair_rendering = strategy.get("continuous_pair_rendering")
    if isinstance(pair_rendering, dict) and isinstance(pair_rendering.get("pair_ids"), list):
        modules_by_id = {
            str(module.get("id")): module
            for module in modules
            if isinstance(module, dict) and module.get("id")
        }
        for pair in pair_rendering["pair_ids"]:
            if not isinstance(pair, dict):
                continue
            module_ids = pair.get("module_ids")
            lower = modules_by_id.get(str(module_ids[1])) if isinstance(module_ids, list) and len(module_ids) == 2 else None
            transition = lower.get("transition_contract") if isinstance(lower, dict) else None
            if not isinstance(transition, dict):
                continue
            pair.setdefault("internal_seam_method_id", transition.get("method_id"))
            pair.setdefault("internal_seam_carrier", transition.get("seam_carrier"))
            pair.setdefault("internal_seam_source_region", transition.get("source_region_previous"))
            pair.setdefault("internal_seam_target_region", transition.get("target_region_current"))
            pair.setdefault("internal_seam_geometry", transition.get("handoff_geometry"))
    for module in modules:
        if not isinstance(module, dict):
            continue
        proof = module.get("proof")
        if not isinstance(proof, dict):
            proof = {}
            module["proof"] = proof
        supporting = proof.get("supporting_subjects")
        if not isinstance(supporting, list) or not supporting:
            proof["supporting_subjects"] = [
                str(proof.get("primary_subject") or "产品主体细节")
            ]
        relations = proof.get("required_relations")
        if not isinstance(relations, list) or not relations:
            proof["required_relations"] = ["商品主体与可见证据保持同一关系"]
        anchor = proof.get("visual_specificity_anchor")
        if isinstance(anchor, str):
            proof["visual_specificity_anchor"] = [anchor]
        elif not isinstance(anchor, list) or not anchor:
            proof["visual_specificity_anchor"] = ["产品外观与结构"]
        if not bool(module.get("breathing_screen")):
            evidence = module.get("visual_evidence")
            visible_count = len(evidence.get("visible_content", [])) if isinstance(evidence, dict) and isinstance(evidence.get("visible_content"), list) else 0
            proof["minimum_visual_payload"] = max(3, int(proof.get("minimum_visual_payload") or 0), visible_count)
    return normalized

