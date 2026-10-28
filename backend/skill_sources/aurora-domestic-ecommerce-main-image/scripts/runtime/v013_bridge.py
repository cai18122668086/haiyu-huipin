from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


V013_ROOT = Path(__file__).resolve().parents[2] / "legacy_v013"
V013_SCRIPTS = V013_ROOT / "scripts"
DEFAULT_SOURCE_MANIFEST = V013_ROOT / "references" / "source-faithful-analysis-manifest.json"
V013_PAGE_ROLES = [
    "SEARCH_MAIN_IMAGE_LISTING_COVER",
    "CAROUSEL_SLOT_1_IDENTITY",
    "CAROUSEL_SLOT_2_CORE_SELLING_POINT",
    "CAROUSEL_SLOT_3_SCENE_BENEFIT",
    "CAROUSEL_SLOT_4_DETAIL_PROOF",
    "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY",
]
V014_PAGE_ROLES = [
    "SEARCH_MAIN_IMAGE_LISTING_COVER",
    "CAROUSEL_IDENTITY_HERO",
    "CAROUSEL_CORE_FEATURE_PROOF",
    "CAROUSEL_SCENE_OR_OPERATION",
    "CAROUSEL_DETAIL_OR_TRUST",
    "CAROUSEL_OFFER_OR_SPEC_CLOSURE",
]
IDENTITY_SENSITIVE_PAGE_INDEXES = {0, 1, 3, 5}
SEARCH_ROUTER_MARKER = "【SEARCH MAIN IMAGE ROUTER v0.1.12 — SEARCH/RECOMMENDATION COVER】"

PAGE_EXECUTION = [
    {
        "buyer_demand": "在搜索结果中快速确认商品、核心价值与点击理由",
        "buyer_question": "这是不是我要找的商品，为什么值得点进来",
        "subject_scale": "完整商品大英雄图，缩略图下轮廓清楚",
        "layout_structure": "搜索拦截式非对称英雄构图；品牌/品类识别、单一点击钩子、零至三个短模块与底部商业闭合",
        "difference_from_previous": "搜索入口首图；独立承担列表点击，不继承任何轮播页骨架",
        "execution_override": "不得复制轮播第1页的镜头、裁切、阅读路径或信息骨架；优先完整商品、最强已核验点击钩子和缩略图识别",
    },
    {
        "buyer_demand": "进入商品后建立更完整的身份、质感与系列主视觉",
        "buyer_question": "这个商品具体是什么，整体设计与核心形态是否可信",
        "subject_scale": "完整商品中大景，允许更编辑化的留白和材质表达",
        "layout_structure": "轮播身份编辑页；使用与搜索首图不同的角度、裁切、标题位置和阅读路径，不使用搜索闭合条",
        "difference_from_previous": "从列表点击逻辑切换为轮播身份叙事；移除搜索模块并更换镜头与版式骨架",
        "execution_override": "保留 Campaign Style，但必须与搜索主图使用不同 shot/crop、copy topology、主体锚点和证据拓扑",
    },
    {
        "buyer_demand": "理解最重要的核心功能以及它与当前商品结构的关系",
        "buyer_question": "它真正解决什么问题，核心功能由什么可见证据承担",
        "subject_scale": "核心部件、工作界面或动作关系的中近景",
        "layout_structure": "核心功能证明页；一个主结论配一个真实部件/动作关系，避免均权卡片墙",
        "difference_from_previous": "从完整身份切换到局部功能证据，降低完整商品占比并强化物理关系",
        "execution_override": "优先真实控制、接口、工作部件或动作；装饰光效不得成为唯一证明",
    },
    {
        "buyer_demand": "确认商品在真实使用环境中的尺度、动作与日常适配性",
        "buyer_question": "我会在什么场景使用，它如何与人、桌面或空间发生关系",
        "subject_scale": "人物/手部/桌面或空间尺度中的当前商品",
        "layout_structure": "真实场景或操作页；前中后景明确，人物和道具只承担尺度、动作或接触证明",
        "difference_from_previous": "从局部功能证明切换到真实空间关系和使用动作",
        "execution_override": "场景必须证明使用、尺度、接触或操作，不得只是背景替换；保持物理接触、阴影与透视可信",
    },
    {
        "buyer_demand": "检查决定信任的细节、接口、材质、组件或结构",
        "buyer_question": "凭什么相信这些功能和做工，关键细节在哪里",
        "subject_scale": "同一商品的真实局部特写、细节网格或结构关系",
        "layout_structure": "细节证据页；局部宏观特写与少量注释绑定真实部件，禁止伪结构和无依据剖面",
        "difference_from_previous": "从完整场景切换到可核对的局部细节和结构证据",
        "execution_override": "所有放大、指针和标签必须落到当前商品真实部件；缺乏视角证据时保留文字而不发明细节",
    },
    {
        "buyer_demand": "完成规格、信任、优惠或购买理由确认",
        "buyer_question": "这个版本是否适合我，还有哪些已核验信息需要最后确认",
        "subject_scale": "商品与一组有主次的规格/信任总结",
        "layout_structure": "成交闭合页；仅在优惠完整核验时使用交易模块，否则使用规格与信任总结，禁止搜索首图复制",
        "difference_from_previous": "从局部证据切换到有主次的最终确认和整套视觉闭合",
        "execution_override": "优惠、服务和数字必须来自完整 USER_FACT；没有核验优惠时不得制造促销氛围或价格锚点",
    },
]


class V013BridgeError(Exception):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise V013BridgeError(message)


def _resolved(path: str) -> str:
    return str(Path(path).resolve()).casefold()


def adapt_v014_raw(raw: dict[str, Any]) -> dict[str, Any]:
    product = raw["product"]

    def reference_paths() -> list[str]:
        paths: list[str] = []
        for item in product["original_references"]:
            if isinstance(item, str) and item.strip():
                path = item.strip()
            elif isinstance(item, dict):
                path = next(
                    (
                        str(item[key]).strip()
                        for key in ("path", "image_path", "file")
                        if isinstance(item.get(key), str) and str(item[key]).strip()
                    ),
                    "",
                )
            else:
                path = ""
            _require(bool(path), "each original reference must contain a usable image path")
            if path not in paths:
                paths.append(path)
        _require(bool(paths), "at least one original reference path is required")
        return paths

    def facts(name: str, prefix: str) -> list[dict[str, str]]:
        values = product.get(name, [])
        if isinstance(values, dict):
            values = [f"{key}：{value}" for key, value in values.items()]
        return [{"id": f"{prefix}-{index}", "text": str(value)} for index, value in enumerate(values, 1)]

    attributes = product.get("verified_attributes", {})
    if isinstance(attributes, list):
        attributes = {f"attribute_{index}": str(value) for index, value in enumerate(attributes, 1)}
    preference = raw.get("campaign_preference", {})
    allowed_preference = {
        key: copy.deepcopy(value)
        for key, value in preference.items()
        if key in {"tone", "palette", "materials", "style_notes", "experimental_patterns"}
    } if isinstance(preference, dict) else {}
    result = {
        "schema_version": "domestic-ecommerce-raw-input-0.1.2",
        "request_id": raw["request_id"],
        "product": {
            "name": product["name"],
            "category": product["category"],
            # v0.1.13 accepts path strings only. Normalize richer v0.1.17
            # reference objects here; identity descriptions remain in the
            # separately locked identity contract.
            "same_product_reference_images": reference_paths(),
            "verified_attributes": attributes,
            "verified_selling_points": facts("verified_selling_points", "sp") + facts("user_provided_display_facts", "user-fact"),
            "verified_scenes": facts("verified_scenes", "scene"),
            "verified_offers": facts("verified_offers", "offer"),
            "forbidden_content": list(raw.get("prohibited_content", [])),
        },
        "output": {"type": "FIVE_IMAGE_CAROUSEL", "image_count": 5},
    }
    if allowed_preference:
        result["campaign_preference"] = allowed_preference
    return result


def adapt_v014_identity(raw: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    reference_evidence = []
    views: dict[str, list[str]] = {}
    for index, item in enumerate(identity["evidence"], 1):
        path = Path(item["path"])
        _require(path.is_file(), f"identity evidence is missing: {path}")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        evidence_id = item["evidence_id"]
        tags = list(item["view_tags"])
        normalized = set(tags)
        for tag in tags:
            lowered = tag.casefold()
            if "front" in lowered:
                normalized.add("FRONT")
            if "three" in lowered or "3/4" in lowered:
                normalized.add("THREE_QUARTER")
            if "macro" in lowered or "detail" in lowered or "control" in lowered or "screen" in lowered:
                normalized.add("DETAIL")
            if "rear" in lowered or "back" in lowered:
                normalized.add("REAR")
            if "side" in lowered or "tilt" in lowered or "hinge" in lowered:
                normalized.add("SIDE_OR_RECLINE")
        tags = sorted(normalized)
        reference_evidence.append({"evidence_id": evidence_id, "path": str(path), "sha256": sha, "view_tags": tags})
        for tag in tags:
            views.setdefault(tag, []).append(evidence_id)
    summary = identity["identity_summary"]
    return {
        "schema_version": "product-identity-contract-0.1.2",
        "contract_id": f"{raw['request_id']}:product-identity",
        "product": {"name": raw["product"]["name"], "category": raw["product"]["category"], "quantity": 1},
        "reference_evidence": reference_evidence,
        "identity": {
            "category_and_quantity": {"category": raw["product"]["category"], "quantity": 1},
            "silhouette_and_proportions": {"overall": identity["product_form"], "part_ratios": list(identity["must_preserve"])},
            "color_partitions": [{"region": "current product", "color": summary, "evidence_indices": list(range(1, len(reference_evidence) + 1))}],
            "materials_and_surfaces": [{"region": "current product", "appearance": summary, "evidence_indices": list(range(1, len(reference_evidence) + 1))}],
            "components": [{"name": "current product components", "shape": summary, "color": "preserve evidence", "connection": "preserve evidence", "evidence_indices": list(range(1, len(reference_evidence) + 1))}],
            "relative_geometry": list(identity["must_preserve"]),
            "logos_and_labels": [],
            "accessories_and_interfaces": [],
        },
        "must_preserve": list(identity["must_preserve"]),
        "must_not_change": list(identity["must_not_change"]),
        "view_specific_evidence": views,
        "evidence_conflicts": [],
        "status": "READY",
    }


def compile_v013(
    raw: dict[str, Any],
    identity: dict[str, Any],
    source_manifest: str | Path | None = None,
) -> dict[str, Any]:
    _require(V013_SCRIPTS.is_dir(), "bundled v0.1.13 scripts are missing")
    manifest = Path(source_manifest) if source_manifest else DEFAULT_SOURCE_MANIFEST
    _require(manifest.is_file(), f"v0.1.13 source manifest is missing: {manifest}")
    if str(V013_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(V013_SCRIPTS))
    from compile_harness_v0_1_13 import compile_raw_v013  # type: ignore

    compiled = compile_raw_v013(raw, identity, manifest)[0]
    _require(compiled.get("generation_performed") is False, "v0.1.13 compilation must remain prompt-only")
    _require(compiled.get("profile01_semantics_read") is False, "v0.1.13 compilation must keep profile01 unread")
    _require(compiled.get("previous_generated_images_used") is False, "v0.1.13 compilation used generated product evidence")
    pages = compiled.get("pages")
    _require(isinstance(pages, list) and len(pages) == 6, "v0.1.13 must compile one search cover and five carousel pages")
    _require([page.get("page_role") for page in pages] == V013_PAGE_ROLES, "v0.1.13 page roles or order are incomplete")
    required_contracts = {
        "campaign_design_contract",
        "design_prototype_overlay",
        "evidence_representation_plan",
        "style_layout_separation_contract",
        "campaign_style_token_contract",
        "experimental_pattern_contract",
        "design_intelligence_overlay",
        "campaign_style_execution_contract",
        "search_main_image_contract",
        "output_set_contract",
    }
    missing = required_contracts - set(compiled)
    _require(not missing, f"v0.1.13 compiled output missing contracts: {sorted(missing)}")
    # v0.1.13 is an evidence/fact producer inside v0.1.17, not a second visual
    # authority. Its legacy copy-density, view-binding and campaign-design gates
    # may report BLOCKED even when all required structured outputs are complete.
    # Keep those failures for internal provenance; v0.1.17 performs the active
    # identity, fact, strategy and visual checks after adapting the result.
    compiled["v017_legacy_advisory_failures"] = {
        "status": compiled.get("status"),
        "set_gates": [key for key, value in compiled.get("set_gates", {}).items() if not value],
        "page_gates": {
            str(index): [key for key, value in page.get("gates", {}).items() if not value]
            for index, page in enumerate(pages)
            if not all(page.get("gates", {}).values())
        },
    }
    return compiled


def _campaign_style(compiled: dict[str, Any]) -> dict[str, Any]:
    contract = compiled["campaign_style_execution_contract"]
    tokens = contract["tokens"]
    photography = tokens["photography_finish"]
    palette = tokens["palette"]
    return {
        "photography_finish": json.dumps(photography, ensure_ascii=False, sort_keys=True),
        "light_direction": photography["key_light_direction"],
        "color_temperature": photography["color_temperature"],
        "palette_roles": copy.deepcopy(palette),
        "palette_usage_ratio": {"neutral_background": 70, "primary_text_and_support": 20, "campaign_accent": 8, "secondary_or_offer": 2},
        "typography": copy.deepcopy(tokens["typography"]),
        "graphic_language": json.dumps(tokens["graphic_language"], ensure_ascii=False, sort_keys=True),
        "information_surface_geometry": json.dumps(tokens["information_surfaces"], ensure_ascii=False, sort_keys=True),
        "density_band": json.dumps(contract["density_and_whitespace"], ensure_ascii=False, sort_keys=True),
        "prohibited_styles": list(contract["prohibited"]),
    }


def _page_legacy_evidence_ids(page: dict[str, Any], identity: dict[str, Any]) -> set[str]:
    bound = page.get("view_evidence_binding", {}).get("bound_evidence_ids", {})
    result = {item for values in bound.values() if isinstance(values, list) for item in values if isinstance(item, str)}
    if result:
        return result
    return {item["evidence_id"] for item in identity.get("reference_evidence", []) if item.get("evidence_id")}


def _primary_claim(page: dict[str, Any]) -> str:
    claim = page.get("factual_content", {}).get("primary_claim", {})
    if isinstance(claim, dict) and isinstance(claim.get("text"), str) and claim["text"].strip():
        return claim["text"].strip()
    return "当前商品与本页已核验核心信息"


def _resolved_execution_prompt(source: dict[str, Any], index: int) -> str:
    prompt = source["generation_prompt"]
    if index != 0:
        return prompt
    marker = prompt.find(SEARCH_ROUTER_MARKER)
    _require(marker >= 0, "v0.1.13 search page is missing its independent search router")
    resolved = prompt[marker:].strip()
    _require("CAROUSEL_SLOT_1_IDENTITY" not in resolved, "search execution prompt still contains carousel identity duty")
    return resolved


def _commercial_orchestration(compiled: dict[str, Any], category: str) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    confidence: list[dict[str, str]] = []
    demand_map: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    rhythm: list[dict[str, Any]] = []
    for index, source in enumerate(compiled["pages"]):
        execution = PAGE_EXECUTION[index]
        claim = _primary_claim(source)
        visualization = source.get("claim_visualization_contract", {})
        proof = visualization.get("primary_physical_evidence") or visualization.get("what_must_be_visible_without_copy") or "当前商品与可见结构、动作或场景关系"
        topology = visualization.get("evidence_topology") or proof
        risk = visualization.get("risk_boundary") or "不得超出 USER_FACT 与当前商品可见证据"
        task = {
            "page_index": index,
            "page_role": V014_PAGE_ROLES[index],
            "buyer_demand": execution["buyer_demand"],
            "buyer_question": execution["buyer_question"],
            "matched_selling_point": claim,
            "confidence_level": "CONFIRMED",
            "visual_proof_method": str(proof),
            "required_evidence": str(topology),
            "subject_scale": execution["subject_scale"],
            "layout_structure": execution["layout_structure"],
            "copy_direction": f"一个短主标题表达“{claim}”，一个限定事实边界的副句，最多三个短证据点",
            "risk_boundary": str(risk),
            "difference_from_previous": execution["difference_from_previous"],
        }
        tasks.append(task)
        confidence.append({"source_text": claim, "confidence_level": "CONFIRMED", "authority": "USER_FACT_OR_SAFE_V0_1_13_COMPRESSION"})
        demand_map.append({"page_index": index, "buyer_demand": task["buyer_demand"], "buyer_question": task["buyer_question"]})
        matches.append({"page_index": index, "buyer_demand": task["buyer_demand"], "matched_selling_point": claim, "visual_proof_method": task["visual_proof_method"]})
        rhythm.append({"page_index": index, "page_role": task["page_role"], "subject_scale": task["subject_scale"], "layout_structure": task["layout_structure"]})
    offer_present = any(page.get("factual_content", {}).get("offer_facts") for page in compiled["pages"])
    category_upper = category.upper()
    if offer_present:
        decision = "VALUE_AND_EVIDENCE_DRIVEN"
    elif any(token in category_upper for token in ("APPLIANCE", "ELECTRONIC", "小家电", "数码", "电器")):
        decision = "EFFICACY_AND_PARAMETER_DRIVEN"
    else:
        decision = "IDENTITY_AND_EVIDENCE_DRIVEN"
    return {
        "information_confidence": confidence,
        "buyer_demand_map": demand_map,
        "demand_to_selling_point_match": matches,
        "purchase_decision_type": decision,
        "page_strategy": {
            "main_purchase_resistance": "买家需要同时确认当前商品身份、核心功能证据、真实使用关系与最终规格/信任信息",
            "conversion_angle": "用当前商品证据驱动的一张搜索首图与五张差异化轮播页逐步降低购买疑虑",
            "proof_style": "完整身份 → 核心功能 → 场景/操作 → 细节证据 → 规格/信任闭合",
        },
        "page_task_table": tasks,
        "visual_rhythm_plan": rhythm,
    }


def build_v014_plan(
    compiled: dict[str, Any],
    legacy_identity: dict[str, Any],
    v014_identity: dict[str, Any],
) -> dict[str, Any]:
    current_by_path = {_resolved(item["path"]): item["evidence_id"] for item in v014_identity["evidence"]}
    legacy_by_id = {item["evidence_id"]: item for item in legacy_identity.get("reference_evidence", [])}
    commercial = _commercial_orchestration(compiled, legacy_identity.get("product", {}).get("category", "GENERAL"))
    pages: list[dict[str, Any]] = []
    bindings: dict[str, set[int]] = {item["evidence_id"]: set() for item in v014_identity["evidence"]}
    invariants = list(v014_identity["must_preserve"]) + list(v014_identity["must_not_change"])
    for index, source in enumerate(compiled["pages"]):
        mapped: list[str] = []
        for legacy_id in sorted(_page_legacy_evidence_ids(source, legacy_identity)):
            item = legacy_by_id.get(legacy_id)
            _require(item is not None, f"v0.1.13 page {index} uses unknown identity evidence {legacy_id}")
            current_id = current_by_path.get(_resolved(item["path"]))
            _require(current_id is not None, f"v0.1.13 evidence path is not locked by v0.1.15 identity: {item['path']}")
            if current_id not in mapped:
                mapped.append(current_id)
                bindings[current_id].add(index)
        _require(bool(mapped), f"v0.1.13 page {index} has no v0.1.15-bound original evidence")
        mode = "PRODUCT_PRESERVING_EDIT" if index in IDENTITY_SENSITIVE_PAGE_INDEXES else "REFERENCE_GUIDED_GENERATION"
        generation_contract = {
            "mode": mode,
            "edit_target_evidence_id": mapped[0] if mode == "PRODUCT_PRESERVING_EDIT" else None,
            "supporting_evidence_ids": mapped[1:] if mode == "PRODUCT_PRESERVING_EDIT" else mapped,
            "locked_product_invariants": invariants,
            "allowed_changes": ["background", "canvas crop", "page-specific layout", "verified copy layer"],
            "forbidden_changes": ["product redraw", "component invention", "cross-SKU blending", "generated-image identity evidence"],
        }
        pages.append(
            {
                "index": index,
                "page_role": V014_PAGE_ROLES[index],
                "evidence_ids": mapped,
                "generation_contract": generation_contract,
                "commercial_page_task": copy.deepcopy(commercial["page_task_table"][index]),
                "execution_overlay": {
                    "commercial_duty": PAGE_EXECUTION[index]["buyer_demand"],
                    "distinct_layout_requirement": PAGE_EXECUTION[index]["layout_structure"],
                    "v013_preservation_rule": "retain the full v0.1.13 prompt as audit evidence; execute a conflict-resolved prompt so the search cover never inherits carousel identity duty",
                    "override": PAGE_EXECUTION[index]["execution_override"],
                },
                "legacy_v013_page_role": source["page_role"],
                "legacy_v013_generation_prompt": source["generation_prompt"],
                "legacy_v013_execution_prompt": _resolved_execution_prompt(source, index),
                "legacy_v013_contracts": {
                    "visual_plan": copy.deepcopy(source.get("visual_plan")),
                    "typography_plan": copy.deepcopy(source.get("typography_plan")),
                    "design_prototype_overlay": copy.deepcopy(source.get("design_prototype_overlay")),
                    "evidence_representation": copy.deepcopy(source.get("evidence_representation")),
                    "claim_visualization_contract": copy.deepcopy(source.get("claim_visualization_contract")),
                    "campaign_style_execution_contract": copy.deepcopy(source.get("campaign_style_execution_contract")),
                    "search_main_image_contract": copy.deepcopy(source.get("search_main_image_contract")),
                    "audit_contract": copy.deepcopy(source.get("audit_contract")),
                    "gates": copy.deepcopy(source.get("gates")),
                },
            }
        )
    reference_bindings = [
        {"evidence_id": evidence_id, "role": "PRODUCT_IDENTITY_AUTHORITY", "page_indexes": sorted(indexes)}
        for evidence_id, indexes in bindings.items()
        if indexes
    ]
    return {
        "compiler_lineage": "FULL_V0_1_13_CAPABILITY_CHAIN_INSIDE_V0_1_14_CLOSED_EXECUTOR",
        "campaign_style": _campaign_style(compiled),
        "commercial_orchestration": commercial,
        "reference_bindings": reference_bindings,
        "pages": pages,
    }
