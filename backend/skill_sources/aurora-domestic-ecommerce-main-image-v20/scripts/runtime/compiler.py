from __future__ import annotations

import copy
import re
from typing import Any

from core import IDENTITY_SENSITIVE_PAGES, PAGE_ROLES, digest, fact_records, require, validate_schema
from style import build_style, observability, proof_method, rank_claims, select_route


BUYER_QUESTIONS = [
    "这是不是我要找的商品，为什么值得点进来",
    "这个商品具体是什么，整体形态和质感是否可信",
    "核心优势由什么真实结构、材质或工作关系承担",
    "它如何在可信场景中与人、物或空间发生关系",
    "哪些细节、结构、服务或信任信息支持购买判断",
    "这个版本是否适合我，最后还需确认什么规格、服务或优惠",
]
LAYOUTS = [
    "搜索入口：身份区、完整大商品、强钩子、物理证据和底部闭合形成两至三个扫描停顿",
    "轮播身份页：不同镜头与主体锚点，较低信息密度，不复制搜索闭合条",
    "功能证明页：完整商品与真实局部或机制形成不等权双尺度",
    "场景操作页：手部、人物、目标物或空间与商品形成可信接触关系",
    "细节信任页：自然完整视角加真实微距、结构或信任信息",
    "规格收口页：完整商品或包装加有主次的规格、服务或优惠闭合",
]
LAYOUT_CONTRACTS = [
    {
        "composition_archetype": "ASYMMETRIC_SEARCH_FRAME",
        "product_zone": "RIGHT_OR_CENTER_DOMINANT",
        "copy_zone": "LEFT_OR_INTERLOCKED",
        "dominant_view": "FULL_PRODUCT_HERO",
        "evidence_topology": "UNEVEN_MODULES_PLUS_REAL_ACTION",
        "composition_instruction": "右侧或中右完整大商品与左侧/咬合式短标题形成搜索框架；不等权图文模块、真实动作证据和底部闭合共同完成三次扫描",
        "forbidden_neighbor_pattern": "后续页面不得继续使用左侧大标题加右侧完整大商品加底部横条的同一骨架",
    },
    {
        "composition_archetype": "EDITORIAL_IDENTITY_DIAGONAL",
        "product_zone": "LEFT_OR_CENTER_DOMINANT",
        "copy_zone": "TOP_OR_RIGHT",
        "dominant_view": "THREE_QUARTER_OR_SIDE_IDENTITY",
        "evidence_topology": "ONE_REAL_RELATION_PLUS_SPARSE_COPY",
        "composition_instruction": "使用不同于搜索页的三分之四或侧向身份镜头；商品偏左或居中，标题位于顶部或右侧，以一个真实关系和少量文字建立身份",
        "forbidden_neighbor_pattern": "禁止复刻搜索页的左文右商品、同尺寸正面机身、多模块底栏结构",
    },
    {
        "composition_archetype": "DOUBLE_SCALE_PROOF",
        "product_zone": "CENTER_OR_SIDE_WITH_MACRO",
        "copy_zone": "TOP_OR_EVIDENCE_BOUND",
        "dominant_view": "FULL_PRODUCT_PLUS_MACRO",
        "evidence_topology": "FULL_AND_MACRO_DOUBLE_SCALE",
        "composition_instruction": "完整商品与真实局部形成明显大小双尺度，标题靠近证据而非单独占半屏，局部必须解释本页功能",
        "forbidden_neighbor_pattern": "禁止退化为单一完整大商品、通用家居背景和左侧功能列表",
    },
    {
        "composition_archetype": "ACTION_DIAGONAL_SCENE",
        "product_zone": "CENTER_IN_USE_RELATION",
        "copy_zone": "CORNER_OR_ACTION_BOUND",
        "dominant_view": "HUMAN_OR_OBJECT_ACTION",
        "evidence_topology": "CONTACT_ACTION_PATH",
        "composition_instruction": "用手部、人物或目标物与商品形成明确接触动作和斜向路径，文字退到角部或贴近动作节点",
        "forbidden_neighbor_pattern": "禁止静态正面摆拍、左右二分海报或没有接触关系的人物装饰",
    },
    {
        "composition_archetype": "DETAIL_CALLOUT_FIELD",
        "product_zone": "DETAIL_DOMINANT_WITH_CONTEXT",
        "copy_zone": "CALLOUTS_AROUND_DETAIL",
        "dominant_view": "MACRO_WITH_FULL_CONTEXT",
        "evidence_topology": "DETAIL_CALLOUT_RELATION",
        "composition_instruction": "真实细节或界面成为主视觉，完整商品只作定位背景；文字和引导线围绕真实细节建立不等权说明",
        "forbidden_neighbor_pattern": "禁止再用完整商品占满右侧并把多个图标竖排在左侧",
    },
    {
        "composition_archetype": "CLOSURE_SPEC_ARCH",
        "product_zone": "LEFT_OR_CENTER_CLOSURE",
        "copy_zone": "RIGHT_OR_BOTTOM_SPEC_CLUSTER",
        "dominant_view": "FULL_PRODUCT_CLOSURE",
        "evidence_topology": "NUMERIC_OR_SPEC_CLOSURE_CLUSTER",
        "composition_instruction": "完整商品与右侧或底部不等权规格/服务/优惠闭合形成收口，使用数字锚点或规格簇而非重复功能卡片",
        "forbidden_neighbor_pattern": "禁止复制细节页微距骨架或搜索页同款标题与底栏",
    },
]
ROLE_SOURCE_BONUS = [
    {"verified_selling_points": 35, "user_provided_display_facts": 25, "verified_offers": 12},
    {"verified_attributes": 30, "user_provided_display_facts": 20, "verified_selling_points": 15},
    {"verified_selling_points": 35, "verified_attributes": 22},
    {"verified_scenes": 40, "verified_selling_points": 18},
    {"verified_service_or_trust_facts": 32, "verified_attributes": 25, "verified_selling_points": 18},
    {"verified_offers": 45, "verified_service_or_trust_facts": 30, "verified_attributes": 20},
]


def _page_rankings(raw: dict[str, Any], index: int) -> list[dict[str, Any]]:
    ranked = rank_claims(raw)
    return sorted(ranked, key=lambda item: (-(item["score"] + ROLE_SOURCE_BONUS[index].get(item["source"], 0)), item["text"]))


def _assign_claims(raw: dict[str, Any]) -> list[dict[str, Any]]:
    all_ranked = rank_claims(raw)
    require(bool(all_ranked), "at least one user-provided display fact is required")
    used: set[str] = set()
    leads: list[dict[str, Any]] = []
    for index in range(6):
        ranking = _page_rankings(raw, index)
        selected = next((item for item in ranking if item["text"] not in used), ranking[index % len(ranking)])
        leads.append(selected)
        used.add(selected["text"])
    result: list[dict[str, Any]] = []
    for index, lead in enumerate(leads):
        candidates = [item for item in _page_rankings(raw, index) if item["text"] != lead["text"]]
        supports: list[str] = []
        for item in candidates:
            useful = (
                item["source"] in ROLE_SOURCE_BONUS[index]
                and (item["source"] == lead["source"] or index in {0, 5})
            )
            if useful and item["text"] not in supports:
                supports.append(item["text"])
            if len(supports) >= (2 if index in {0, 5} else 1):
                break
        result.append({"lead": lead, "supports": supports})
    return result


def _closure(raw: dict[str, Any], index: int) -> str:
    records = fact_records(raw)
    offers = [item["text"] for item in records if item["source"] == "verified_offers"]
    trust = [item["text"] for item in records if item["source"] in {"verified_service_or_trust_facts", "verified_attributes"}]
    if index == 0 and offers:
        return "使用一项完整核验优惠作为交易闭合，并保留所有条件"
    if index == 5 and offers:
        return "用完整核验优惠、规格或服务完成购买确认"
    return "使用已核验规格、适用信息、服务或信任事实完成短闭合" if trust else "用本页已核验事实完成简洁结论"


def _shot_key(index: int) -> dict[str, str]:
    return {
        "view_crop": ["完整英雄视角", "自然完整身份视角", "完整与局部双尺度", "真实操作或场景视角", "自然完整加微距", "完整商品或包装收口"][index],
        "product_scale": ["LARGE", "MEDIUM_LARGE", "VARIABLE_DOUBLE_SCALE", "SCENE_RELATIVE", "DETAIL_WEIGHTED", "MEDIUM_LARGE"][index],
        "scene_geometry": ["commercial_frame", "editorial_identity", "evidence_split", "credible_depth_scene", "detail_relation", "closure_structure"][index],
        "subject_anchor": ["hero_hook", "identity_relation", "physical_proof", "contact_action", "detail_target", "final_confirmation"][index],
        "layout_family": LAYOUT_CONTRACTS[index]["composition_archetype"],
    }


def _display_copy_contract(lead: str, supports: list[str], index: int) -> dict[str, Any]:
    clauses = [item.strip() for item in re.split(r"[，,。；;：:]", lead) if item.strip()]
    seed = clauses[0] if clauses else lead.strip()
    return {
        "source_claim": lead,
        "headline_seed_source_segment": seed,
        "support_source_segments": list(dict.fromkeys([*clauses[1:], *supports])),
        "headline_target_chars": "4–10个汉字或等效短字符",
        "headline_hard_max_chars": 12 if index == 0 else 16,
        "headline_max_lines": 2,
        "subheadline_max_lines": 1,
        "compression_rule": "忠实保留核心利益，不新增事实；长事实拆为短主标题、副标题或不等权辅助模块，不得缩小字号硬塞整句",
        "full_source_as_headline_forbidden": len(re.sub(r"\s+", "", lead)) > (12 if index == 0 else 16),
    }


def _search_cover_blueprint(raw: dict[str, Any], hook: str, display_copy: dict[str, Any]) -> dict[str, Any]:
    ranked = rank_claims(raw)
    modules = [item["text"] for item in ranked if item["text"] != hook][:3]
    offers = [item["text"] for item in fact_records(raw) if item["source"] == "verified_offers"]
    return {
        "role": "TAOBAO_LISTING_CLICK_COVER_NOT_CAROUSEL_HERO",
        "working_bands": {
            "identity_header": "6%–10%",
            "product_and_message_field": "58%–72%",
            "bottom_commercial_closure": "16%–24%" if offers else "10%–16%",
            "safe_frame": "2%–4% when supported by product-derived style",
        },
        "hero_contract": "完整当前商品通常占画高55%–75%，并与强标题共同成为第一视觉；不得成为场景摆件",
        "headline_contract": f"源事实“{hook}”必须压缩为一个短点击钩子；优先从“{display_copy['headline_seed_source_segment']}”提炼，主标题4–10字、硬上限12个有效字符、最多2行；其余事实下放副标题或辅助模块，不得整句硬塞",
        "proof_modules": modules,
        "proof_module_rule": "允许0–5个不等权辅助模块；可用图标、短句、微距或结果关系完成信息，但禁止均权卡片墙",
        "information_preservation_rule": "已核验的品牌、规格、数量、功效、服务、信任和条件信息只要层级清楚即可保留，不得为追求极简而机械删除",
        "physical_proof_object": "至少一个真实材质、部件、内容物、动作、状态、结果或使用关系进入主画面；纯图标和文字不计",
        "type_roles": "品牌身份 → 强主标题/数字锚点 → 证据短句 → 规格/交易结论 → 信任或条件说明，至少四级可见差异",
        "scan_stops": ["完整大商品或强主标题", "真实证据或不等权信息模块", "底部商业闭合"],
        "visual_strength": "高完成度淘宝商业合成；商品、标题、证据、前中后景和闭合区形成咬合、对角或三角张力",
        "forbidden_regression": "不得退化为商品加一句标题的自然系海报、普通棚拍、空洞生活方式图或详情页首屏",
    }


def _selling_point_proof_contract(lead: str, supports: list[str], route: dict[str, Any]) -> dict[str, Any]:
    kind = observability(lead)
    renderable = kind in {"DIRECTLY_VISIBLE", "ACTION_DEMONSTRABLE"}
    return {
        "source_text": lead,
        "bundle_source_texts": [lead, *supports],
        "observability_class": kind,
        "primary_physical_evidence": proof_method(lead, route),
        "supporting_evidence": [{"source_text": item, "proof": proof_method(item, route)} for item in supports],
        "renderable_claim_must_survive_copy_removal": renderable,
        "no_copy_takeaway": f"遮住文字后仍应感到商品正在强化“{lead}”的利益方向；精确数字和条件可由文字限定",
        "creative_expression_permission": "允许可信的商业夸张、感官氛围、尺度对比、动态、结果场景、前后关系和视觉隐喻；不得将表现手法写成新事实",
        "forbidden_substitutes": ["只有标题", "纯装饰植物或人物", "图标/箭头/光效替代证据", "与主张无关的微距", "大面积空背景"],
    }


def _visual_performance_contract(index: int, visual_proof: str) -> dict[str, Any]:
    if index == 0:
        return {
            "product_dominance": "完整商品是第一或并列第一视觉，通常占画高55%–75%",
            "headline_dominance": "一个最强点击钩子或数字锚点成为大标题并参与构图",
            "physical_evidence_anchor": visual_proof,
            "depth_plan": "真实台面、前景切入、接触阴影、完整商品/细节双尺度或场景纵深至少两项",
            "commercial_closure": "必须出现已核验规格、服务、信任、受众或完整优惠闭合",
            "thumbnail_scan_stops": 3,
            "visual_tension": "商品、标题、证据和闭合形成咬合、对角或三角张力，禁止平行摆成提案页",
            "background_competition": "LOW",
            "information_density": "HIGH_WITH_CLEAR_HIERARCHY",
        }
    if index == 1:
        return {
            "product_dominance": "完整商品或真实使用主体占画面主导，通常占画高55%–80%",
            "headline_dominance": "身份主张有力度，但信息密度低于搜索主图",
            "physical_evidence_anchor": visual_proof,
            "depth_plan": "商品与真实人物、手部、织物、内容物或场景形成可信尺度和接触关系",
            "commercial_closure": "一条身份、适用或核心规格短闭合，不复制搜索底栏",
            "thumbnail_scan_stops": 2,
            "visual_tension": "商品与真实关系成为第一视觉，留白只服务轮廓和标题",
            "background_competition": "LOW",
            "information_density": "MEDIUM",
        }
    return {
        "product_dominance": "商品、真实局部、内容物或动作证据主导，不得由装饰图标主导",
        "headline_dominance": "一个页面主结论清楚，数字、单位和辅助信息有明显主次",
        "physical_evidence_anchor": visual_proof,
        "depth_plan": "局部/完整双尺度、动作前后关系、场景纵深或不等权分区至少一种",
        "commercial_closure": "用本页已核验事实完成短闭合，不制造新优惠或权威",
        "thumbnail_scan_stops": 2 if index in {2, 3} else 3,
        "visual_tension": "证据、标题与商品形成明确阅读方向，禁止平均卡片分布",
        "background_competition": "LOW_TO_MEDIUM",
        "information_density": "MEDIUM_HIGH" if index in {2, 4, 5} else "MEDIUM",
    }


def _approved_execution_contract(page: dict[str, Any], proof: dict[str, Any], performance: dict[str, Any]) -> dict[str, Any]:
    required = [
        "完整可识别的当前商品身份",
        f"与“{page['primary_claim']}”对应的真实像素证据",
        "与本页职责一致且清楚分级的主标题",
        "可信接触阴影、尺度关系、动作关系或前中后景",
    ]
    if page["index"] == 0:
        required.extend(["顶部身份区", "底部商业闭合区", "至少四级文字角色差异", "唯一明确的搜索入口角色"])
    return {
        "subject_and_view": page["shot_key"],
        "composition_blueprint": page["composition"],
        "layout_contract": copy.deepcopy(page["layout_contract"]),
        "display_copy_contract": copy.deepcopy(page["display_copy_contract"]),
        "reading_path": page["reading_path"],
        "type_hierarchy": page["type_hierarchy"],
        "required_visible_elements": required,
        "selling_point_visual_proof_contract": proof,
        "visual_performance_contract": performance,
        "creative_freedom": {
            "hard_locks": ["商品身份", "事实含义/数字/条件", "页面职责与主利益", "Campaign Style", "必要商业闭合"],
            "agent_judgment": ["镜头", "裁切", "主体尺度", "构图", "光线", "空间", "动作", "结果场景", "短文案", "可信视觉隐喻"],
            "success_test": "缩略图中商品突出、主利益鲜明、画面有购买冲动、细节丰富且事实不漂移",
        },
    }


def compile_prompt(raw: dict[str, Any], identity: dict[str, Any], plan: dict[str, Any], page: dict[str, Any]) -> str:
    style = plan["campaign_style"]
    adjustment = page.get("active_user_adjustment")
    repair = page.get("repair")
    performance = page["visual_performance_contract"]
    proof = page["selling_point_visual_proof_contract"]
    display_copy = page["display_copy_contract"]
    layout = page["layout_contract"]
    lines = [
        "【AURORA DOMESTIC E-COMMERCE v2】",
        f"page={page['index']}；role={page['page_role']}。",
        "【商品身份硬锁】" + identity["identity_summary"],
        "必须保留=" + "；".join(identity["must_preserve"]) + "。",
        "绝不改变=" + "；".join(identity["must_not_change"]) + "。",
        "【本页购买任务】" + page["buyer_question"],
        "【主卖点】" + page["primary_claim"],
        "【显示文案合同】源事实=" + display_copy["source_claim"] + "；短标题种子=" + display_copy["headline_seed_source_segment"] + "；目标=" + display_copy["headline_target_chars"] + "；硬上限=" + str(display_copy["headline_hard_max_chars"]) + "；最多" + str(display_copy["headline_max_lines"]) + "行；规则=" + display_copy["compression_rule"] + "。",
        "【辅助卖点·按画面力量取舍】" + ("；".join(page["supporting_claims"]) if page["supporting_claims"] else "无强制辅助卖点"),
        "【画面证明】" + page["visual_proof"],
        "【构图职责】" + page["composition"],
        "【页面版式合同】原型=" + layout["composition_archetype"] + "；商品区=" + layout["product_zone"] + "；文字区=" + layout["copy_zone"] + "；主镜头=" + layout["dominant_view"] + "；证据拓扑=" + layout["evidence_topology"] + "；执行=" + layout["composition_instruction"] + "；邻页禁用=" + layout["forbidden_neighbor_pattern"] + "。",
        "【阅读路径】" + " → ".join(page["reading_path"]),
        "【文字层级】" + str(page["type_hierarchy"]),
        "【商业闭合】" + page["commercial_closure"],
        "【视觉世界】摄影=" + style["photography_finish"] + "；主光=" + style["light_direction"] + "；色温=" + style["color_temperature"] + "；色彩角色=" + str(style["palette_roles"]) + "；字体=" + str(style["typography"]) + "；信息面=" + style["information_surfaces"] + "；密度=" + style["density_band"] + "。",
        "【视觉表现合同】商品主导=" + performance["product_dominance"] + "；标题=" + performance["headline_dominance"] + "；真实证据=" + performance["physical_evidence_anchor"] + "；纵深=" + performance["depth_plan"] + "；张力=" + performance["visual_tension"] + "；背景竞争=" + performance["background_competition"] + "。",
        "【卖点证据合同】主证据=" + proof["primary_physical_evidence"] + "；无文字结论=" + proof["no_copy_takeaway"] + "；禁止替代物=" + "、".join(proof["forbidden_substitutes"]) + "。",
        "【表现许可】" + proof["creative_expression_permission"],
        "六页共享同一美术方向，但不得复制镜头、主体锚点、阅读路径、证据拓扑或版式骨架。",
        "原始照片只提供商品身份和真实结构；原图桌面、墙面、缝隙、杂物、键盘、色偏和拍摄边角默认是噪声，必须重建场景。",
        "先找到最能放大主卖点的一个强视觉创意，让商品与利益成为第一视觉；不要把资料逐条排成清单，也不要为了极简删除有用的数字、规格、功效、信任或条件信息。",
        "页面职责、主卖点意义、商品身份、用户事实条件和视觉世界不可变；镜头、裁切、尺度、光线、空间、短文案、商业夸张和可信视觉修辞由执行Agent判断。",
        "事实权限以用户来源为准：用户文字输入及用户原始同商品参考图中清晰可读的包装、功效、数字、背书、优惠等文案均可直接使用，不要求外部证明；不得猜测模糊文字，不得把生成图新增文字或模型推断当成事实来源。",
        "输出1:1方图且至少1024×1024；中文完整可读；不得发明数字、认证、结构、部件、优惠或效果。",
    ]
    if page.get("search_cover_blueprint"):
        blueprint = page["search_cover_blueprint"]
        lines.extend([
            "【搜索主图硬蓝图】身份区=" + blueprint["working_bands"]["identity_header"] + "；商品/信息场=" + blueprint["working_bands"]["product_and_message_field"] + "；底部闭合=" + blueprint["working_bands"]["bottom_commercial_closure"] + "。",
            "【搜索钩子】" + blueprint["headline_contract"],
            "【候选证据模块·按画面取舍】" + "；".join(blueprint["proof_modules"]) + "。" + blueprint["proof_module_rule"],
            "【搜索信息保留】" + blueprint["information_preservation_rule"],
            "【搜索文字角色】" + blueprint["type_roles"],
            "【搜索页禁止退化】" + blueprint["forbidden_regression"],
        ])
    if repair:
        lines.extend(["【唯一自动返修】", str(repair)])
    if adjustment:
        lines.extend(["【用户授权的局部差量】", str(adjustment)])
    return "\n".join(lines)


def automatic_plan(raw: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    style = build_style(raw, identity)
    route = select_route(raw, identity)
    assignments = _assign_claims(raw)
    evidence_ids = [item["evidence_id"] for item in identity["evidence"]]
    pages: list[dict[str, Any]] = []
    for index, assignment in enumerate(assignments):
        lead = assignment["lead"]["text"]
        supports = assignment["supports"]
        display_copy = _display_copy_contract(lead, supports, index)
        layout_contract = copy.deepcopy(LAYOUT_CONTRACTS[index])
        mode = "PRODUCT_PRESERVING_EDIT" if index in IDENTITY_SENSITIVE_PAGES else "REFERENCE_GUIDED_GENERATION"
        page = {
            "index": index,
            "page_role": PAGE_ROLES[index],
            "buyer_question": BUYER_QUESTIONS[index],
            "primary_claim": lead,
            "display_copy_contract": display_copy,
            "supporting_claims": supports,
            "claim_bundle": [lead, *supports],
            "observability": observability(lead),
            "visual_proof": proof_method(lead, route),
            "evidence_ids": evidence_ids,
            "shot_key": _shot_key(index),
            "composition": LAYOUTS[index] + "；" + layout_contract["composition_instruction"],
            "layout_contract": layout_contract,
            "reading_path": ["商品与主利益", "真实证据", "商业闭合"] if index in {0, 5} else ["本页主结论", "真实证据"],
            "type_hierarchy": {"identity": "品牌/商品身份", "primary": "统领性主标题或数字锚点", "evidence": "不等权辅助卖点或证据短句", "closure": "规格、交易、条件或信任闭合", "footnote": "必要的事实限定或依据说明"},
            "commercial_closure": _closure(raw, index),
            "non_repetition_reason": f"第{index}页解决独立买家问题，并使用 { _shot_key(index)['subject_anchor'] } 作为不同于相邻页面的证明中心",
            "generation_mode": mode,
            "edit_target_evidence_id": evidence_ids[0] if mode == "PRODUCT_PRESERVING_EDIT" else None,
            "revision": 0,
            "generation_origin": "INITIAL",
        }
        proof_contract = _selling_point_proof_contract(lead, supports, route)
        performance_contract = _visual_performance_contract(index, page["visual_proof"])
        page["selling_point_visual_proof_contract"] = proof_contract
        page["visual_performance_contract"] = performance_contract
        page["approved_page_execution_contract"] = _approved_execution_contract(page, proof_contract, performance_contract)
        if index == 0:
            page["search_cover_blueprint"] = _search_cover_blueprint(raw, lead, display_copy)
        pages.append(page)
    plan = {
        "schema_version": "aurora-six-image-plan-2.0.0",
        "strategy": {
            "purchase_decision_type": "VALUE_AND_EVIDENCE_DRIVEN" if any(item["source"] == "verified_offers" for item in fact_records(raw)) else "IDENTITY_AND_EVIDENCE_DRIVEN",
            "main_resistance": "买家需要依次确认商品身份、核心价值、真实证据、使用关系和最终购买条件",
            "campaign_thesis": f"以当前商品身份和“{pages[0]['primary_claim']}”为搜索钩子，用六种不同证明任务完成购买说服",
        },
        "campaign_style": style,
        "pages": pages,
    }
    for page in plan["pages"]:
        page["prompt"] = compile_prompt(raw, identity, plan, page)
    validate_plan(plan, raw, identity)
    return plan


def validate_plan(plan: dict[str, Any], raw: dict[str, Any], identity: dict[str, Any]) -> None:
    validate_schema(plan, "plan.schema.json")
    facts = {item["text"] for item in fact_records(raw)}
    evidence = {item["evidence_id"] for item in identity["evidence"]}
    layout_families: set[str] = set()
    for index, page in enumerate(plan["pages"]):
        require(page["index"] == index, f"page {index} index mismatch")
        require(page["page_role"] == PAGE_ROLES[index], f"page {index} role mismatch")
        require(page["primary_claim"] in facts, f"page {index} primary claim is not a user fact")
        require(page.get("display_copy_contract", {}).get("source_claim") == page["primary_claim"], f"page {index} display copy must preserve the source claim")
        require(all(page.get("display_copy_contract", {}).get(key) is not None for key in ("headline_seed_source_segment", "headline_hard_max_chars", "headline_max_lines", "compression_rule")), f"page {index} display copy contract is incomplete")
        require(set(page["supporting_claims"]).issubset(facts), f"page {index} supporting claim is not a user fact")
        require(page["claim_bundle"] == list(dict.fromkeys([page["primary_claim"], *page["supporting_claims"]])), f"page {index} claim bundle mismatch")
        require(set(page["evidence_ids"]).issubset(evidence), f"page {index} uses unknown evidence")
        require(all(page.get("visual_performance_contract", {}).get(key) for key in ("product_dominance", "headline_dominance", "physical_evidence_anchor", "depth_plan", "commercial_closure", "thumbnail_scan_stops", "visual_tension", "background_competition", "information_density")), f"page {index} visual performance contract is incomplete")
        require(all(page.get("selling_point_visual_proof_contract", {}).get(key) is not None for key in ("source_text", "observability_class", "primary_physical_evidence", "no_copy_takeaway", "creative_expression_permission", "forbidden_substitutes")), f"page {index} selling-point proof contract is incomplete")
        require(bool(page.get("approved_page_execution_contract", {}).get("required_visible_elements")), f"page {index} approved execution contract is incomplete")
        expected_layout = LAYOUT_CONTRACTS[index]
        require(page.get("layout_contract") == expected_layout, f"page {index} layout contract must use the page-specific v2 archetype")
        family = str(page["shot_key"].get("layout_family", ""))
        require(bool(family) and family not in layout_families, f"page {index} requires a unique layout family")
        layout_families.add(family)
        if index in IDENTITY_SENSITIVE_PAGES:
            require(page["generation_mode"] == "PRODUCT_PRESERVING_EDIT", f"page {index} must preserve-edit the product")
            require(page.get("edit_target_evidence_id") in page["evidence_ids"], f"page {index} edit target is invalid")
    require(plan["pages"][0]["shot_key"] != plan["pages"][1]["shot_key"], "search cover and identity hero need different shots")
    require(bool(plan["pages"][0].get("search_cover_blueprint", {}).get("type_roles")), "search cover blueprint is required")


def normalize_plan(raw: dict[str, Any], identity: dict[str, Any], supplied: dict[str, Any] | None) -> dict[str, Any]:
    if supplied is None:
        return automatic_plan(raw, identity)
    plan = copy.deepcopy(supplied)
    validate_plan(plan, raw, identity)
    for page in plan["pages"]:
        page.setdefault("revision", 0)
        page.setdefault("generation_origin", "INITIAL")
        page["prompt"] = compile_prompt(raw, identity, plan, page)
    plan["plan_digest"] = digest({"strategy": plan["strategy"], "style": plan["campaign_style"], "pages": plan["pages"]})
    return plan


def plan_summary(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "页码/职责": f"{page['index']} / {page['page_role']}",
            "主卖点": page["primary_claim"],
            "画面短标题": page["display_copy_contract"]["headline_seed_source_segment"],
            "辅助卖点": page["supporting_claims"],
            "画面证明": page["visual_proof"],
            "构图与主体": page["composition"],
            "版式原型": page["layout_contract"]["composition_archetype"],
            "阅读路径/文字层级": " → ".join(page["reading_path"]),
            "不重复理由": page["non_repetition_reason"],
            "商业闭合": page["commercial_closure"],
        }
        for page in plan["pages"]
    ]
