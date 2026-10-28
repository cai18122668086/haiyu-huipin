#!/usr/bin/env python3
"""Freeze the v0.1.11 full-corpus Campaign Style before generation."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def _text(raw: dict[str, Any], *keys: str) -> str:
    value: Any = raw
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return str(value or "").strip()


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            f"{key} {_flatten_text(item)}" for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value or "")


def _product_evidence_text(raw: dict[str, Any]) -> str:
    product = raw.get("product", {})
    preference = raw.get("campaign_preference", {})
    return " ".join(
        [
            _flatten_text(product.get("name")),
            _flatten_text(product.get("category")),
            _flatten_text(product.get("verified_attributes")),
            _flatten_text(product.get("verified_selling_points")),
            _flatten_text(product.get("verified_scenes")),
            _flatten_text(preference),
        ]
    ).lower()


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _product_palette_from_evidence(evidence: str) -> dict[str, str]:
    """Resolve palette from explicit current-product cues, never a category color."""
    routes = (
        (("粉色", "粉红", "玫粉", "桃粉"), "#C9768D", "#91C4C5", "PINK_CURRENT_PRODUCT"),
        (("红色", "酒红", "朱红", "莓红"), "#B94E55", "#C59A7A", "RED_CURRENT_PRODUCT"),
        (("橙色", "橘色", "橙黄"), "#D47745", "#B29B64", "ORANGE_CURRENT_PRODUCT"),
        (("黄色", "奶黄", "金黄"), "#C9A24D", "#7E9682", "YELLOW_CURRENT_PRODUCT"),
        (("绿色", "墨绿", "薄荷绿", "草绿"), "#5E8D72", "#9A846B", "GREEN_CURRENT_PRODUCT"),
        (("蓝色", "深蓝", "冰蓝", "天蓝"), "#527F9E", "#8E7C72", "BLUE_CURRENT_PRODUCT"),
        (("紫色", "薰衣草紫", "淡紫"), "#806F9D", "#A88A83", "PURPLE_CURRENT_PRODUCT"),
        (("棕色", "咖色", "驼色", "原木色"), "#8A6B50", "#70817A", "EARTH_CURRENT_PRODUCT"),
        (("黑色", "深灰", "石墨", "哑光黑", "磨砂黑"), "#3E8E91", "#697783", "DARK_CURRENT_PRODUCT"),
        (("白色", "浅灰", "暖白", "奶白"), "#687E86", "#9A8A78", "LIGHT_NEUTRAL_CURRENT_PRODUCT"),
        (("银色", "金属银", "铝合金"), "#627C87", "#8C8178", "LIGHT_METAL_CURRENT_PRODUCT"),
        (("透明", "半透明", "清透", "水感", "玻璃"), "#78AEB1", "#B27F8B", "CLEAR_MATERIAL_CURRENT_PRODUCT"),
    )
    for terms, accent, secondary, route_id in routes:
        if _contains_any(evidence, terms):
            return {
                "accent": accent,
                "secondary_accent": secondary,
                "route_id": route_id,
                "basis": "current_product_explicit_color_or_material_evidence",
            }
    return {
        "accent": "#737A78",
        "secondary_accent": "#A19A90",
        "route_id": "NEUTRAL_UNBOUND_FALLBACK",
        "basis": "neutral_palette_fallback_no_explicit_color_evidence",
    }


def _category_profile_prior(raw: dict[str, Any]) -> dict[str, Any]:
    category_prior = (
        _text(raw, "product", "category")
        + " "
        + _text(raw, "product", "name")
    ).lower()
    evidence = _product_evidence_text(raw)
    specialized_context = _contains_any(
        category_prior,
        (
            "母婴",
            "婴儿",
            "婴童",
            "医疗",
            "健康",
            "宠物",
            "汽车用品",
            "车载",
            "车用",
        ),
    )
    if (
        _contains_any(category_prior, ("鼠标", "键盘", "数码", "电子"))
        and not specialized_context
    ):
        business_precision = _contains_any(
            evidence,
            (
                "商务",
                "办公",
                "多设备",
                "切换",
                "蓝牙",
                "无线",
                "跨设备",
                "滚轮",
                "编程",
                "精准",
                "续航",
                "鼠标",
                "键盘",
            ),
        )
        dark_matte_product = _contains_any(
            evidence,
            ("黑色", "深灰", "哑光", "磨砂", "石墨"),
        )
        gaming_dynamic = _contains_any(
            evidence,
            ("电竞", "游戏", "rgb", "炫光", "高刷"),
        )
        if gaming_dynamic and not business_precision:
            return {
                "profile": "DARK_DYNAMIC_ELECTRONICS",
                "accent": "#7657C8",
                "secondary_accent": "#4D6678",
                "background_material_family": [
                    "深石墨微纹理工作台",
                    "低反射烟灰金属面",
                    "局部冷灰环境分离光",
                ],
                "graphic_motif": "短能量轨迹＋状态刻度＋克制参数节点",
                "key_light": "画面左后上方40°冷中性轮廓主光",
                "color_temperature": "5200K–5600K冷中性",
                "fill_ratio": "主光:补光=2.8:1",
                "depth": "中浅景深，商品轮廓与核心操控区同时清晰",
                "selection_basis": [
                    "current_product_verified_function",
                    "current_product_use_scene",
                    "current_product_color_and_surface",
                    "category_as_candidate_prior_only",
                ],
            }
        return {
            "profile": "COOL_NEUTRAL_PRECISION_BUSINESS",
            "accent": "#3E8E91",
            "secondary_accent": "#697783",
            "background_material_family": [
                (
                    "雾白与浅冷灰办公界面"
                    if dark_matte_product
                    else "中性浅灰办公界面"
                ),
                "低反射阳极氧化铝或细砂金属面",
                "克制的现代多设备办公桌面",
            ],
            "graphic_motif": "设备节点＋切换状态标记＋短精密刻度",
            "key_light": "画面左上方35°柔光主灯",
            "color_temperature": "5000K–5400K中性偏冷",
            "fill_ratio": "主光:补光=2.4:1",
            "depth": "中浅景深，商品整体与关键按键同时清晰",
            "selection_basis": [
                "current_product_verified_function",
                "current_product_use_scene",
                "current_product_color_and_surface",
                "campaign_preference",
                "category_as_candidate_prior_only",
            ],
        }
    if not specialized_context and _contains_any(
        category_prior,
        ("小家电", "吸尘器", "洗地机", "风扇", "净水器", "剃须刀", "料理机", "电饭煲"),
    ):
        return {
            "profile": "CURRENT_TASK_SMALL_APPLIANCE",
            "accent": "#737A78",
            "secondary_accent": "#A19A90",
            "background_material_family": [
                "与核验任务一致的真实工作界面",
                "突出当前机身表面的低反射中性承托面",
                "可看清控制、接触目标、部件或维护动作的可信空间",
            ],
            "graphic_motif": "真实工作状态＋短状态标记＋绑定正确部件的克制引线",
            "key_light": "按当前表面选择左前或左后上方35°–45°中性塑形光",
            "color_temperature": "4800K–5400K，由当前材质和核验场景决定",
            "fill_ratio": "主光:补光=2.0–2.6:1",
            "depth": "身份页中景深；工作界面和部件页让目标与商品同时清晰",
            "typography_direction": "按性能、维护或场景职责选择几何无衬线/清晰中黑；数字绑定真实部件",
            "prohibited_styles": [
                "默认蓝色科技背景",
                "用气流、能量弧或光效替代真实工作状态",
                "参数脱离其对应控制、部件或任务",
            ],
            "selection_basis": [
                "current_product_working_interface",
                "current_product_component_and_material",
                "current_product_verified_task",
                "current_product_use_scene",
                "category_as_candidate_prior_only",
            ],
        }
    if _contains_any(category_prior, ("汽车用品", "车载", "车用", "磁吸支架", "车载充电")):
        fragrance = _contains_any(evidence, ("香薰", "香氛", "扩香", "木塞", "香调"))
        return {
            "profile": "VEHICLE_FRAGRANCE_USE" if fragrance else "VEHICLE_INSTALLATION_TASK",
            "accent": "#737A78",
            "secondary_accent": "#A19A90",
            "background_material_family": (
                ["真实车内悬挂/放置语境", "透明液体与木塞可读的中性承托面", "低比例香调来源或情境摄影"]
                if fragrance
                else ["真实车内安装位置", "不遮挡视线的中性空间", "可看清底座、关节、接触面和设备关系的局部"]
            ),
            "graphic_motif": (
                "真实浸润/悬挂关系＋低比例景深融合香调线索"
                if fragrance
                else "安装位置标记＋真实转轴/接触面＋短兼容分组"
            ),
            "key_light": "遵循当前车内方向的中性塑形光，商品轮廓和安装关系优先",
            "color_temperature": "4600K–5300K，按当前商品材质和车内场景决定",
            "fill_ratio": "主光:补光=2.0–2.5:1",
            "depth": "商品与关键安装/接触关系同时清晰，环境仅提供尺度",
            "typography_direction": (
                "香调/体验用克制编辑字配信息无衬线" if fragrance else "安装、角度、兼容用紧凑清晰无衬线"
            ),
            "prohibited_styles": [
                "车载香薰默认暖金奢华",
                "车载配件默认蓝黑科技",
                "磁场、能量环或图标替代真实安装和部件证明",
            ],
            "selection_basis": [
                "current_product_form_fragrance_or_mechanical_accessory",
                "current_product_installation_and_material",
                "current_product_verified_claim_and_scene",
                "category_as_candidate_prior_only",
            ],
        }
    if (
        not _contains_any(
            category_prior,
            ("母婴", "婴儿", "婴童", "医疗", "健康", "宠物"),
        )
        and _contains_any(
            category_prior,
            ("服饰", "服装", "鞋", "上衣", "裤", "围巾", "背包", "箱包", "童装"),
        )
    ):
        return {
            "profile": "CURRENT_FORM_FASHION_ACCESSORY",
            "accent": "#737A78",
            "secondary_accent": "#A19A90",
            "background_material_family": [
                "与当前穿着/背负任务一致的可信场景",
                "保持轮廓和真实颜色可读的中性背景",
                "当前面料、鞋底、开口、隔层或五金的真实微距",
            ],
            "graphic_motif": "完整上身/整品锚点＋一项动作或材质证据＋克制选择信息",
            "key_light": "按当前面料与轮廓选择大面积柔光或受控方向光",
            "color_temperature": "4500K–5400K，由当前材质、场景与Campaign偏好决定",
            "fill_ratio": "主光:补光=1.8–2.5:1",
            "depth": "完整轮廓清晰；材质页允许局部浅景深但保留身份锚点",
            "typography_direction": "从当前轮廓、面料、受众和claim job选择编辑宋黑、运动窄体或商业无衬线",
            "prohibited_styles": [
                "把户外硬字、峡谷、岩石、水波或雪花强加给所有服饰",
                "隐喻替代版型、穿脱、装载、面料或结构证明",
            ],
            "selection_basis": [
                "current_product_form_and_silhouette",
                "current_product_material_and_construction",
                "current_product_wear_or_load_task",
                "current_product_audience_and_scene",
                "category_as_candidate_prior_only",
            ],
        }
    if _contains_any(category_prior, ("医疗", "健康", "保健", "营养补充", "护理器械", "康复", "医用")):
        return {
            "profile": "EVIDENCE_GRADED_MEDICAL_HEALTH",
            "accent": "#737A78",
            "secondary_accent": "#A19A90",
            "background_material_family": [
                "当前包装/剂型/器械可读的中性实景",
                "与核验用法和人群一致的克制使用语境",
                "真实来源、检测或认证对象的清晰信息面",
            ],
            "graphic_motif": "证据级别分层＋清晰用法关系＋完整风险边界",
            "key_light": "中性高可读柔光，真实包装、剂型和器械表面优先",
            "color_temperature": "4800K–5300K中性光，禁止用色温制造临床权威",
            "fill_ratio": "主光:补光=1.8–2.2:1",
            "depth": "包装/剂型/用法与证据对象均清晰",
            "typography_direction": "高可读信息无衬线与严格数字层级；情绪字体不得承担医疗权威",
            "prohibited_styles": [
                "无依据临床蓝白默认",
                "人体轮廓、医生、徽章或光环冒充功效证据",
                "把结果、适用人群和认证混成一个承诺",
            ],
            "selection_basis": [
                "current_product_form_and_packaging",
                "current_product_evidence_grade",
                "current_product_verified_audience_and_use",
                "compliance_and_result_boundary",
                "category_as_candidate_prior_only",
            ],
        }
    if _contains_any(category_prior, ("母婴", "婴儿", "婴童", "宝宝", "婴儿床", "婴儿推车", "护栏")):
        durable = _contains_any(evidence, ("推车", "婴儿床", "护栏", "轮组", "折叠", "支撑", "关节"))
        return {
            "profile": "MOTHER_BABY_DURABLE_TASK" if durable else "MOTHER_BABY_AGE_CONTACT",
            "accent": "#737A78",
            "secondary_accent": "#A19A90",
            "background_material_family": (
                ["真实照护/推行/床边任务空间", "结构、锁止、轮组和接触关系清晰的中性环境", "符合核验年龄和尺度的人物关系"]
                if durable
                else ["符合核验年龄与接触部位的真实使用语境", "当前包材和质地可读的柔和中性面", "低比例亲和图形层"]
            ),
            "graphic_motif": "适龄/接触边界＋真实照护动作或部件＋体验/功能/信任分层",
            "key_light": "柔和中性大面积光，人物动作与商品结构/包材同时可读",
            "color_temperature": "4700K–5200K，由当前商品而非母婴默认粉蓝决定",
            "fill_ratio": "主光:补光=1.7–2.2:1",
            "depth": "人物只作动作与尺度证明，商品和目标部件保持清晰",
            "typography_direction": "友好圆角无衬线或结构清晰中黑；安全、适龄与认证用完整信息字",
            "prohibited_styles": [
                "默认粉蓝、云朵、泡泡或卡通冒充安全温和",
                "人物只作装饰而不证明年龄、尺度、动作或接触",
            ],
            "selection_basis": [
                "current_product_form_consumable_or_durable",
                "current_product_verified_age_and_contact",
                "current_product_material_structure_and_task",
                "current_product_audience_and_scene",
                "category_as_candidate_prior_only",
            ],
        }
    if _contains_any(category_prior, ("宠物", "猫粮", "狗粮", "冻干", "猫砂", "宠物清洁", "宠物用品")):
        return {
            "profile": "PET_PRODUCT_FORM_AND_USE",
            "accent": "#737A78",
            "secondary_accent": "#A19A90",
            "background_material_family": [
                "与当前食品/猫砂/清洁/设备形态一致的真实材料场",
                "动物真实参与喂食、如厕、清洁或尺度证明的场景",
                "当前颗粒、肉块、结团、泡沫或部件的真实微距",
            ],
            "graphic_motif": "当前product form物性＋动物参与任务＋核验营养/卫生边界",
            "key_light": "真实食材/颗粒/泡沫/部件可读的中性商业光",
            "color_temperature": "4500K–5300K，由当前物性和场景决定",
            "fill_ratio": "主光:补光=1.9–2.4:1",
            "depth": "材料状态与动物动作清晰，包装保留身份锚点",
            "typography_direction": "食品、猫砂、清洁和设备分别按物性与任务选择；事实层保持高可读",
            "prohibited_styles": [
                "动物只作装饰而不参与卖点",
                "用夸张飞溅、卡通或图标替代真实颗粒、肉块、结团或清洁动作",
            ],
            "selection_basis": [
                "current_product_form_food_litter_care_or_device",
                "current_product_material_state",
                "current_product_verified_animal_and_task",
                "current_product_packaging_and_scene",
                "category_as_candidate_prior_only",
            ],
        }
    if any(word in category_prior for word in ("椅", "座椅", "家具")):
        return {
            "profile": "REFINED_SEATING",
            "accent": "#667B8D",
            "secondary_accent": "#B8A58D",
            "background_material_family": [
                "浅灰建筑面",
                "低反射玻璃隔断",
                "克制现代办公空间",
            ],
            "graphic_motif": "柔和支撑弧线＋短结构线＋局部磨砂信息面",
            "key_light": "画面左前上方40°大面积窗光",
            "color_temperature": "5000K中性日光",
            "fill_ratio": "主光:补光=2.2:1",
            "depth": "中等景深，商品轮廓和身份结构完整清晰",
            "selection_basis": [
                "current_product_verified_function",
                "current_product_use_scene",
                "current_product_color_and_surface",
                "category_as_candidate_prior_only",
            ],
        }
    if any(
        word in category_prior for word in ("收纳", "置物", "推车", "架", "柜")
    ):
        return {
            "profile": "ORDERED_STORAGE",
            "accent": "#6E917C",
            "secondary_accent": "#B69A78",
            "background_material_family": [
                "暖白墙面",
                "浅灰办公地面",
                "低饱和现代办公家具",
            ],
            "graphic_motif": "秩序圆点＋细引线＋轻量分类标签",
            "key_light": "画面左前上方35°柔和窗光",
            "color_temperature": "5000K中性日光",
            "fill_ratio": "主光:补光=2:1",
            "depth": "中等景深，三层结构与装载关系清晰",
            "selection_basis": [
                "current_product_verified_function",
                "current_product_use_scene",
                "current_product_color_and_surface",
                "category_as_candidate_prior_only",
            ],
        }
    if any(
        word in category_prior for word in ("食品", "零食", "饼干", "面", "饮")
    ):
        return {
            "profile": "APPETITE_FOOD",
            "accent": "#D7743F",
            "secondary_accent": "#D4A94F",
            "background_material_family": [
                "暖白餐台",
                "浅木或天然石材",
                "低饱和生活场景",
            ],
            "graphic_motif": "食欲色块＋轻弧标签＋少量原料碎片",
            "key_light": "画面左后上方45°暖柔光",
            "color_temperature": "4100K–4500K暖中性",
            "fill_ratio": "主光:补光=2.6:1",
            "depth": "中浅景深，包装与真实食物质感同时清晰",
            "selection_basis": [
                "current_product_verified_function",
                "current_product_use_scene",
                "current_product_color_and_surface",
                "category_as_candidate_prior_only",
            ],
        }
    if any(
        word in category_prior
        for word in ("美妆", "护肤", "个护", "沐浴", "洗护", "洗浴")
    ):
        clear_watery_product = _contains_any(
            evidence,
            (
                "透明",
                "半透明",
                "啫喱",
                "凝露",
                "水感",
                "清透",
                "水润",
                "水珠",
                "气泡",
                "轻盈",
            ),
        )
        fresh_cleaning = _contains_any(
            evidence,
            ("洁净", "清洁", "清爽", "洗净", "沐浴", "洗护"),
        )
        fragrance_cue = _contains_any(
            evidence,
            ("果香", "香氛", "香味", "清甜", "鲜活"),
        )
        if clear_watery_product and fresh_cleaning:
            motif = "透明液体层次＋微气泡＋克制水纹"
            if fragrance_cue:
                motif += "＋低比例果香线索自然融入水珠或液体"
            return {
                "profile": "CLEAR_WATERY_PERSONAL_CARE",
                "accent": "#CF7F91",
                "secondary_accent": "#9CCFD0",
                "background_material_family": [
                    "高明度清透湿润背景",
                    "透明玻璃、水面或低反射浅色湿区材质",
                    "洁净轻盈的浴室或洗护使用语境",
                ],
                "graphic_motif": motif,
                "key_light": "画面左后上方45°高透柔光与窄幅边缘光",
                "color_temperature": "5000K–5400K清亮中性光",
                "fill_ratio": "主光:补光=1.7:1",
                "depth": "中浅景深，透明包装边缘、啫喱质地、水珠与标签同时清晰",
                "prohibited_styles": [
                    "通用暖金高级背景",
                    "厚重木质陈列",
                    "生硬水果拼贴或整果堆放",
                    "果香线索压过商品与洁净水感",
                ],
                "selection_basis": [
                    "current_product_transparency_and_gel_texture",
                    "current_product_fresh_cleaning_claim",
                    "current_product_verified_fragrance_cue",
                    "current_product_use_scene",
                    "campaign_preference",
                    "category_as_candidate_prior_only",
                ],
            }
        return {
            "profile": "CLEAN_BEAUTY",
            "accent": "#A87D82",
            "secondary_accent": "#C3A88E",
            "background_material_family": [
                "柔雾浅石材",
                "半透明磨砂亚克力",
                "干净梳妆空间",
            ],
            "graphic_motif": "柔雾色面＋细圆弧＋克制成分标签",
            "key_light": "画面左前上方45°柔光箱",
            "color_temperature": "4700K–5000K中性柔光",
            "fill_ratio": "主光:补光=1.8:1",
            "depth": "中浅景深，包装轮廓、标签和质地清晰",
            "selection_basis": [
                "current_product_verified_function",
                "current_product_use_scene",
                "current_product_color_and_surface",
                "category_as_candidate_prior_only",
            ],
        }
    return {
        "profile": "GENERAL_REFINED_COMMERCE",
        "accent": "#737A78",
        "secondary_accent": "#A19A90",
        "background_material_family": [
            "与当前商品色材质相容的中性承托面",
            "低反射浅色或中灰材料",
            "与商品用途一致的克制实景",
        ],
        "graphic_motif": "短结构线＋轻量标签＋局部磨砂信息面",
        "key_light": "画面左前上方40°柔光",
        "color_temperature": "4800K–5200K中性光",
        "fill_ratio": "主光:补光=2.2:1",
        "depth": "中等景深，商品身份结构完整清晰",
        "selection_basis": [
            "current_product_verified_function",
            "current_product_use_scene",
            "current_product_color_and_surface",
            "campaign_preference",
            "category_as_candidate_prior_only",
        ],
    }


def _category_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply the current product palette after category/task nomination."""
    profile = copy.deepcopy(_category_profile_prior(raw))
    palette_route = _product_palette_from_evidence(
        _product_evidence_text(raw)
    )
    profile["accent"] = palette_route["accent"]
    profile["secondary_accent"] = palette_route["secondary_accent"]
    profile["palette_route"] = palette_route["route_id"]
    profile["palette_basis"] = palette_route["basis"]
    profile.setdefault("selection_basis", []).append(
        palette_route["basis"]
    )
    return profile


def _build_contract(
    compiled: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any]:
    first_page = compiled["pages"][0]
    existing = copy.deepcopy(
        first_page.get("campaign_style_tokens", {}).get("tokens", {})
    )
    profile = _category_profile(raw)
    palette_route = {
        "accent": profile["accent"],
        "secondary_accent": profile["secondary_accent"],
        "route_id": profile["palette_route"],
        "basis": profile["palette_basis"],
    }
    palette = existing.setdefault("palette", {})
    palette.update(
        {
            "campaign_accent": profile["accent"],
            "secondary_accent": profile["secondary_accent"],
            "neutral_background": "72%",
            "primary_text_and_support": "20%",
            "campaign_accent_usage": "4%–8%，不是每页必用",
            "secondary_accent_usage": "0%–4%",
            "offer_color_usage": "仅促销页且必须有USER_FACT",
            "all_page_blue_accent_forbidden": True,
            "product_identity_colors_immutable": True,
        }
    )
    typography = existing.setdefault("typography", {})
    typography.update(
        {
            "display_execution": (
                profile.get(
                    "typography_direction",
                    "从当前商品材质、主张和受众选择同一展示家族；"
                    "标题可在宽度、字重、错位和色块承托间变化，不跨页换家族",
                )
            ),
            "body_execution": "同一清晰中文信息字体；常规或中等字重",
            "numeric_execution": "同一紧凑数字家族；数字、百分号、单位分级",
            "headline_color_policy": (
                "深石墨为主；每页最多1–2个完整关键词使用Campaign强调色"
            ),
            "outline_shadow_policy": "全套最多一种极轻描边；不使用立体金字或重发光",
        }
    )
    existing["graphic_language"] = {
        "shared_motif_family": profile["graphic_motif"],
        "stroke_weight": "1.5–2.0px视觉等效细线",
        "corner_radius_family": "中小圆角，视觉等效12–18px",
        "device_count_limit_per_page": 3,
        "reuse_rule": "共享母题语言，不复制相同页面骨架",
        "decorative_device_cannot_replace_evidence": True,
    }
    existing["information_surfaces"] = {
        "fill": "暖白或当前中性色，88%–94%不透明度",
        "border": "1px视觉等效中性细边",
        "corner_radius": "与共享圆角家族一致",
        "shadow": "单层极轻软阴影",
        "identical_card_stack_forbidden": True,
    }
    existing["photography_finish"] = {
        "key_light_direction": profile["key_light"],
        "color_temperature": profile["color_temperature"],
        "key_fill_ratio": profile["fill_ratio"],
        "contrast": "中等商业对比，暗部保留材质，不压黑",
        "depth_of_field": profile["depth"],
        "material_rendering": "真实表面微纹理，不塑料化、不重新设计商品",
        "background_material_family": profile["background_material_family"],
        "background_finish": "低对比、低信息密度、始终从属于商品",
        "source_photo_background_isolation": "原拍墙面、地面、杂物、色偏和透视全部隔离",
    }
    preference = raw.get("campaign_preference", {})
    preference_text = json.dumps(
        preference, ensure_ascii=False, sort_keys=True
    )
    style_seed = "|".join(
        [
            str(raw.get("request_id", "")),
            profile["profile"],
            preference_text,
            json.dumps(existing, ensure_ascii=False, sort_keys=True),
        ]
    )
    campaign_style_id = (
        "CAMPAIGN-"
        + profile["profile"]
        + "-"
        + hashlib.sha256(style_seed.encode("utf-8")).hexdigest()[:10].upper()
    )
    return {
        "campaign_style_id": campaign_style_id,
        "status": "FROZEN_BEFORE_FIRST_GENERATION",
        "source": "ENGINEERED_DEFAULT_WITH_USER_PREFERENCE",
        "profile": profile["profile"],
        "palette_route": palette_route["route_id"],
        "selection_basis": copy.deepcopy(profile["selection_basis"]),
        "tokens": existing,
        "density_and_whitespace": {
            "default_information_density": "MEDIUM",
            "hero_density": "LOW",
            "proof_page_density": "MEDIUM_HIGH",
            "safe_margin": "6%–8%",
            "background_competition": "LOW",
        },
        "fixed_across_set": [
            "photography_finish",
            "palette_roles_and_ratios",
            "typography_families_and_roles",
            "graphic_language",
            "information_surface_geometry",
            "density_and_whitespace_band",
        ],
        "variable_by_page": [
            "layout_archetype",
            "product_view_and_crop",
            "product_scale",
            "reading_path",
            "proof_method",
            "page_specific_device_mix",
        ],
        "prohibited": [
            "five unrelated visual styles",
            "same template repeated five times",
            "accent color forced onto every page",
            "generated page used as product identity reference",
            "identity-failed page used as style reference",
            "source-photo background leakage",
            *profile.get("prohibited_styles", []),
        ],
    }


def _prompt_block(
    contract: dict[str, Any],
    page: dict[str, Any],
) -> str:
    tokens = contract["tokens"]
    typography = tokens["typography"]
    palette = tokens["palette"]
    graphics = tokens["graphic_language"]
    surfaces = tokens["information_surfaces"]
    photography = tokens["photography_finish"]
    density = contract["density_and_whitespace"]
    variation = page.get("campaign_style_tokens", {}).get(
        "page_role_variation", {}
    )
    layout = page.get("design_prototype_overlay", {}).get(
        "layout_archetype"
    )
    fields = page.get("visual_plan", {}).get("fields", {})
    return "\n".join(
        [
            "【FROZEN CAMPAIGN STYLE — SET LEVEL, HIGHEST VISUAL PRIORITY】",
            f"campaign_style_id={contract['campaign_style_id']}；"
            f"profile={contract['profile']}；palette_route={contract['palette_route']}；"
            "五张固定使用同一合同。",
            "字体="
            f"{typography['display_family_role']} / "
            f"{typography['body_family_role']} / "
            f"{typography['numeric_family_role']}；"
            f"主标题={typography['headline_color_policy']}；"
            f"当前商品字形方向={typography['display_execution']}。",
            "色彩="
            f"主字{palette['primary_text']}、副字{palette['secondary_text']}、"
            f"强调{palette['campaign_accent']}、信息面{palette['information_surface']}；"
            f"比例={palette['usage_ratio']}；强调色可缺席，禁止全套默认蓝色。",
            "摄影="
            f"{photography['key_light_direction']}，"
            f"{photography['color_temperature']}，"
            f"{photography['contrast']}，"
            f"{photography['depth_of_field']}；"
            "背景材质="
            + "/".join(photography["background_material_family"])
            + "；必须隔离原拍背景。",
            "共享图形="
            f"{graphics['shared_motif_family']}，"
            f"每页≤{graphics['device_count_limit_per_page']}种；"
            "信息面="
            f"{surfaces['fill']}、{surfaces['border']}、{surfaces['shadow']}；"
            f"密度={density['default_information_density']}，"
            f"安全边距={density['safe_margin']}。",
            "禁用=" + " / ".join(contract["prohibited"]) + "。",
            "本页变量="
            f"role={page.get('page_role')}；layout={layout}；"
            f"view={fields.get('product.angle_view')}；"
            f"scale={fields.get('product.occupancy_range')}；"
            f"headline={variation.get('headline_treatment')}；"
            f"emphasis={variation.get('keyword_emphasis')}。",
            "只允许构图、视角、尺度、阅读路径和证明方式随页变化；"
            "摄影质感、色彩角色、字体家族、信息面和图形语言不得漂移。",
            "商品原始颜色与结构不可为适配风格而改变；"
            "previous_generated_images_used_for_product_identity=false；"
            "身份失败页不得成为商品或风格参考；逐张通过既有身份门禁后再继续。",
        ]
    )


def freeze_campaign_style(
    compiled: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(compiled)
    contract = _build_contract(result, raw)
    result["campaign_style_execution_contract"] = copy.deepcopy(contract)
    for page in result.get("pages", []):
        page["campaign_style_execution_contract"] = {
            "campaign_style_id": contract["campaign_style_id"],
            "status": contract["status"],
            "fixed_tokens": copy.deepcopy(contract["tokens"]),
            "density_and_whitespace": copy.deepcopy(
                contract["density_and_whitespace"]
            ),
        }
        page["generation_prompt"] = (
            page["generation_prompt"].rstrip()
            + "\n\n"
            + _prompt_block(contract, page)
        )
    return result
