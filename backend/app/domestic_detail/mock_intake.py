"""Synthetic contracts used exclusively by FakeXingheGateway, never real mode."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .automatic_brand import fallback_basis
from .facts import INTAKE_VERSION, user_fact_ledger
from .intake import active_fact_ids


def mock_intake(
    settings: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    resolution: Mapping[str, Any] | None,
) -> dict[str, Any]:
    products = [
        source["asset_id"]
        for source in sources
        if source["role"] == "product_reference"
    ]
    styles = [
        source["asset_id"] for source in sources if source["role"] == "style_reference"
    ]
    facts = user_fact_ledger(settings)
    primary = products[0]
    features = [
        "合成测试主体外轮廓为圆角矩形截面",
        "合成测试主体正面宽:总高约为1:1",
        "合成测试主体为单段结构且底座与上沿同宽",
        "合成测试主体表面呈连续黄色分区",
    ]
    references = []
    for asset_id in styles:
        method = "主体居右并让左侧短标题与下方信息形成不对称阅读关系"
        references.append(
            {
                "id": asset_id,
                "path": f"asset:{asset_id}",
                "category": "style_reference",
                "analysis_status": "ANALYZED",
                "observed_expression_methods": [method],
                "analyzed_units": [
                    {
                        "id": f"{asset_id}-U01",
                        "source_region": "画面右侧主体与左侧标题",
                        "page_duty": "区分主体和文字主次",
                        "visual_carrier": "单一主体与左侧留白",
                        "composition_logic": "右侧主体与左侧信息形成非对称构图",
                        "typography_logic": "标题与下方短标签形成两级字重",
                        "observed_expression_method": method,
                    }
                ],
            }
        )
    observed = (
        []
        if facts
        else [
            {
                "id": "F01",
                "atomic_key": "visible-yellow-front",
                "atomicity": "ONE_INDEPENDENT_PROPOSITION",
                "text": "合成测试主体可见黄色正面",
                "source_class": "OBSERVED_PRODUCT_EVIDENCE",
                "evidence_ref": primary,
                "observed_region": "主体正面",
                "claim_kind": "PRODUCT_IDENTITY",
                "display_intent": "MUST_SHOW",
                "user_prominence": "SUPPORTING_POINT",
                "named_elements": ["黄色主体"],
            }
        ]
    )
    value: dict[str, Any] = {
        "version": INTAKE_VERSION,
        "conflict_scan": {
            "status": "CLEAR",
            "scanned_topics": ["identity", "specification", "performance"],
            "conflict_ids": [],
            "scanned_source_refs": [*products, *[fact["id"] for fact in facts]],
        },
        "source_conflicts": [],
        "observed_facts": observed,
        "reference_materials": references,
        "brand_confirmation_required": False,
    }
    confirmation = (
        settings.get("brandless_confirmation")
        or (resolution or {}).get("brandless_confirmation")
        or fallback_basis(settings)
    )
    if not confirmation:
        value.update(
            brand_confirmation_required=True,
            brand_observation={
                "source_asset_id": primary,
                "text": "合成素材未提供可独立使用的可读品牌文字",
            },
        )
        return value
    value["identity"] = {
        "canonical_name": settings.get("product_name") or "合成测试商品",
        "category": settings.get("category") or "合成测试商品",
        "reference_image_ids": products,
        "invariant_features": features,
        "allowed_states": ["正面直立展示"],
        "uncertain_features": [],
        "forbidden_mutations": ["不得添加第二个主体或改变矩形轮廓"],
        "brand_logo_source": {
            "mode": "BRANDLESS",
            "image_id": "NONE",
            "authorization_basis": "NOT_APPLICABLE",
            "readability": "NOT_APPLICABLE",
            "usage": "FORBIDDEN_ALL_SCREENS",
            "brandless_evidence": confirmation,
        },
    }
    value["identity_observations"] = [
        {"kind": kind, "source_asset_id": primary, "region": "正面", "text": text}
        for kind, text in zip(
            ("silhouette", "proportions", "parts", "color_material"),
            features,
            strict=True,
        )
    ]
    if not settings.get("selling_points"):
        value["operating_analysis"] = {
            "route_reason": "以合成素材中可见的主体外观建立测试故事",
            "confirmed_fact_ids": ["F01"],
            "buyer_hypotheses": [
                {
                    "id": "B01",
                    "buyer": "关注外观的购买者",
                    "scenario": "选购对照",
                    "reasoning": "外观事实可用于辨识主体与空间关系",
                    "confidence": "LOW",
                }
            ],
            "pain_hypotheses": [
                {
                    "id": "P01",
                    "pain": "难以辨别主体外形",
                    "kind": "DECISION",
                    "reasoning": "购买前通常需要辨别外形",
                    "claim_boundary": "只作选择场景假设，不增加功能性能事实",
                }
            ],
            "value_bridges": [
                {
                    "id": "V01",
                    "fact_ids": ["F01"],
                    "pain_id": "P01",
                    "feature": "黄色正面",
                    "buyer_benefit": "辨别外观",
                    "purchase_reason": "确认所见外形",
                    "visual_route": "展示外轮廓与近景",
                    "claim_permission": "SCENE_HYPOTHESIS_ONLY",
                }
            ],
            "positioning_candidates": [
                {
                    "id": "POS01",
                    "thesis": "展示外观供选择",
                    "score_rationale": "以单一已知外观事实避免新增功能承诺",
                    "decision": "SELECTED",
                }
            ],
            "selected_positioning_id": "POS01",
            "evidence_gaps": ["未提供性能或材质证明"],
            "assumption_disclosure": "买家与痛点是测试场景假设，不是商品事实",
        }
    return value


def mock_plan_with_intake(
    plan: Mapping[str, Any], intake: Mapping[str, Any]
) -> dict[str, Any]:
    value = deepcopy(dict(plan))
    active = active_fact_ids(intake)
    first = active[0]
    modules = value["modules"]
    ids = [module["id"] for module in modules]
    strategy = value["output_strategy"]
    first_coverage = deepcopy(strategy["claim_coverage"][0])
    first_coverage["claim_id"] = first
    strategy["claim_coverage"] = [
        first_coverage,
        *[
            {
                "claim_id": fact_id,
                "disposition": "SUPPORT",
                "module_ids": ids,
                "rationale": "合成用例将该原始事实作为各屏可见的辅助信息",
                "defer_reason_code": "NOT_DEFERRED",
            }
            for fact_id in active[1:]
        ],
    ]
    strategy["selling_point_clusters"][0]["fact_ids"] = active
    for module in modules:
        module["lead_claim_id"] = first
        module["supporting_claim_ids"] = active[1:]
    value["user_selling_point_coverage_ledger"] = [
        {
            "selling_point_id": f"USP{index:02d}",
            "status": "ACTIVE" if fact["id"] in active else "CONFLICT_BLOCKED",
            "atomic_claim_ids": [fact["id"]] if fact["id"] in active else [],
            "planned_module_ids": ids if fact["id"] in active else [],
            "reason": (
                "按原文和来源逐一覆盖该用户卖点；被用户明确排除的冲突事实不进入销售表达"
            ),
        }
        for index, fact in enumerate(
            (
                fact
                for fact in intake["user_facts"]
                if fact["evidence_ref"].startswith("settings/selling_points/")
            ),
            start=1,
        )
    ]
    brand = intake["identity"]["brand_logo_source"]
    if brand.get("mode", "BRANDED") != "BRANDLESS":
        modules[0]["independent_logo_policy"] = "REQUIRED_IF_AUTHORIZED"
        modules[0]["independent_logo_source_id"] = brand["image_id"]
    analysis = intake.get("operating_analysis")
    if analysis is not None:
        pain_id = analysis["pain_hypotheses"][0]["id"]
        value["operating_strategy"] = {
            "intake_route": "PHOTO_TO_PRODUCT_STRATEGY",
            "target_buyer": analysis["buyer_hypotheses"][0]["buyer"],
            "primary_pain_id": pain_id,
            "selected_positioning_id": analysis["selected_positioning_id"],
            "core_promise": "用合成商品的可见外观回答选择问题",
            "priority_claim_ids": [first],
            "supporting_claim_ids": active[1:],
            "assumption_boundary": analysis["assumption_disclosure"],
        }
        pain_contract = value["campaign_pain_solution_contract"]
        pain_contract["primary_pain_id"] = pain_id
        pain_contract["value_bridge_ids"] = [
            row["id"] for row in analysis["value_bridges"]
        ]
    refs = intake["reference_materials"]
    patterns = [
        {
            "id": f"PAT-{index:02d}",
            "source_reference_ids": [reference["id"]],
            "source_unit_ids": [reference["analyzed_units"][0]["id"]],
            "observed_method": reference["analyzed_units"][0][
                "observed_expression_method"
            ],
            "adaptation_for_product": "在该合成商品上重建非对称的信息关系",
            "scheduled_module_ids": ids,
            "do_not_copy": "不继承参考的商品、品牌、参数或文案",
            "fact_dependencies": [first],
            "copy_or_claims_not_inherited": ["源参考的品牌与规格不继承"],
        }
        for index, reference in enumerate(refs, start=1)
    ]
    value["reference_expression_system"] = {
        "coverage": [
            {
                "reference_id": reference["id"],
                "disposition": "ADOPTED_GRAMMAR",
                "pattern_ids": [patterns[index]["id"]],
                "reason": "采用该已观察的视觉语法，仅作为合成用例参考",
            }
            for index, reference in enumerate(refs)
        ],
        "adopted_patterns": patterns,
        "anti_copy_rule": "ADAPT_VISUAL_GRAMMAR_ONLY_NEVER_COPY_BRAND_COPY_OR_ASSET",
    }
    for module in modules:
        module["reference_pattern_ids"] = [pattern["id"] for pattern in patterns]
    return value


def bounded_mock_campaign(
    plan: Mapping[str, Any], intake: Mapping[str, Any]
) -> dict[str, Any]:
    """Explicit synthetic data for the stricter staged authoring wire contract."""
    value = deepcopy(dict(plan))
    modules = value["modules"]
    first = modules[0]["lead_claim_id"]
    ids = [m["id"] for m in modules]
    families = [
        "hero_anchor",
        "asymmetric_editorial",
        "macro_material",
        "comparison_split",
        "process_sequence",
        "structure_diagram",
        "data_evidence",
        "origin_story",
        "sku_selection",
        "specification_closure",
        "immersive_scene",
        "macro_material",
    ]
    for m, family in zip(modules, families, strict=False):
        m["composition_family"] = family
        policy = (
            "REQUIRED_DOMINANT"
            if m["complete_product_role"] == "DOMINANT"
            else "REQUIRED_SUPPORTING"
        )
        m["expression"]["product_presence_policy"] = policy
        m["product_presence_decision"] = {
            "policy": policy,
            "unique_job_if_present": "合成商品可见身份",
            "primary_subject_without_product": "局部结构",
            "identity_binding_without_product": ["合成商品轮廓"],
            "absence_reason": "",
        }
        m["proof"].update(
            {
                "supporting_subjects": ["合成商品的局部边缘"],
                "required_relations": ["外观轮廓对应局部结构"],
                "visual_specificity_anchor": ["合成商品外观与结构"],
            }
        )
        m["visual_evidence"] = {
            "evidence_class": "DIRECT_VERIFIED",
            "proof_device": "CONCRETE_PRODUCT_EVIDENCE",
            "visible_content": ["合成商品的外观与结构"],
            "forbidden_inventions": ["不得增加产品性能承诺"],
        }
        m["typography"]["primary_mode"] = (
            "offset_editorial_headline" if int(m["id"][1:]) % 2 else "object_label"
        )
        m["typography"]["anchor_zone"] = (
            "top_left" if int(m["id"][1:]) % 2 else "object_local"
        )
    value["operating_strategy"].update(
        {
            "intake_route": "USER_SELLING_POINT_LED",
            "target_buyer": "合成商品测试用户",
            "primary_pain_id": "P01",
            "selected_positioning_id": "POS01",
            "core_promise": "以合成商品可见外观说明选择依据",
            "priority_claim_ids": [first],
            "supporting_claim_ids": [],
            "assumption_boundary": "所有人群及痛点均为测试假设，不增加任何产品事实。",
        }
        if not intake.get("operating_analysis")
        else {}
    )
    value["campaign_recall_contract"] = {
        "must_remember_claim_ids": [first],
        "first_screen_value_claim_ids": [first],
        "proof_module_ids_by_claim": {first: ids[1:3]},
        "closure_reinforcement_claim_ids": [first],
        "buyer_recall_test": "看完整页能区分合成商品的真实外观与局部结构。",
    }
    jobs = ["VALUE_OVERVIEW", "DEDICATED_EXPRESSION", "USE_SCOPE", "RECALL"]
    value["selling_point_depth_contract"] = {
        "selection_policy": (
            "SMALLEST_CANDIDATE_THAT_SATISFIES_SALES_DEPTH_NOT_MINIMUM_FACT_LISTING"
        ),
        "primary_claim_ids": [first],
        "compact_selection_exception": "",
        "claim_depth": [
            {
                "claim_id": first,
                "depth_tier": "DEEP",
                "evidence_ceiling": "DIRECT_PROOF_AVAILABLE",
                "required_decision_jobs": jobs,
                "scheduled_module_ids": ids,
                "dedicated_module_ids": ids[1:3],
                "priority": "PRIMARY",
                "module_jobs": [
                    {
                        "module_id": mid,
                        "job": jobs[0]
                        if i == 0
                        else jobs[3]
                        if i == len(ids) - 1
                        else jobs[1]
                        if i == 1
                        else jobs[2],
                    }
                    for i, mid in enumerate(ids)
                ],
                "non_duplicate_rationale": "总览、证明、选择与回忆各有独立职责。",
                "forbidden_expansion": ["禁止合成测试增加材质和性能事实"],
            }
        ],
    }
    pain = value["campaign_pain_solution_contract"]
    pain.setdefault("primary_pain_id", "P01")
    pain.update(
        {
            "coverage_mode": "COMBINED_PAIN_SOLUTION_MODULE",
            "pain_module_ids": ["M02"],
            "solution_module_ids": ["M02", "M03"],
            "visible_problem_boundary": "只描述合成测试的选择疑问，不增加性能承诺。",
            "pain_to_solution_transition": "从外观疑问递进到真实结构证据。",
            "exemption_basis": "NOT_APPLICABLE",
            "exemption_reason": "",
        }
    )
    pain.setdefault("value_bridge_ids", [])
    for key in ("product_tokens", "spatial_tokens"):
        tokens = value["series_visual_dna"].get(key)
        if isinstance(tokens, list):
            value["series_visual_dna"][key] = "；".join(tokens)
    return value
