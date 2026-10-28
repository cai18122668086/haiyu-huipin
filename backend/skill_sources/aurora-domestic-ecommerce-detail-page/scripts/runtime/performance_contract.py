from __future__ import annotations


def resolve_performance_contract(plan: dict, order: int = 1, module_count: int = 1) -> dict:
    """Read the visual-performance contract or provide a strict compatibility contract for older sessions."""
    existing = plan.get("module_visual_performance_contract")
    if existing:
        return existing

    breathing = bool(plan.get("breathing_screen"))
    expression = plan.get("module_visual_expression_contract") or {}
    presence = expression.get("product_presence_policy", "REQUIRED_DOMINANT")
    subject_policy = expression.get("evidence_subject_policy", "PRODUCT_DOMINANT")
    story_function = plan.get("story_function")
    story_strategy = {
        "PAIN_ARTICULATION": "PAIN",
        "PAIN_SOLUTION_COMBINED": "PAIN_SOLUTION_COMBINED",
        "SOLUTION_PROOF": "EFFICACY_PROOF",
        "MECHANISM_PROOF": "MECHANISM",
        "MATERIAL_PROOF": "MATERIAL_INGREDIENT",
        "USE_OR_RESULT": "USE_SCENE",
        "TRUST_OR_RISK_REMOVAL": "TRUST",
        "SELECTION_OR_SPECIFICATION": "PARAMETER_SPECIFICATION",
        "PURCHASE_CLOSURE": "PURCHASE_CLOSURE",
    }.get(story_function, "OTHER_PRODUCT_PROOF")
    if order == 1:
        strategy = "FIRST_SCREEN_CORE_BENEFIT"
    elif order == module_count:
        strategy = "PURCHASE_CLOSURE"
    else:
        strategy = story_strategy
    headline_required = bool(plan.get("visible_copy"))
    prominence = {
        "REQUIRED_DOMINANT": 30, "REQUIRED_SUPPORTING": 12,
        "IDENTITY_TOKEN_ONLY": 0, "OPTIONAL": 0,
        "ABSENT_ALLOWED": 0, "ABSENT_PREFERRED": 0,
    }.get(presence, 30)
    product_dominance = {
        "REQUIRED_DOMINANT": "商品必须是第一视觉主体，并保持清晰可识别的主要视觉重量",
        "REQUIRED_SUPPORTING": "商品只作为第二或第三视觉锚点，不能压过本屏的功效对象、动作或结果",
        "IDENTITY_TOKEN_ONLY": "禁止完整商品或完整包装出镜；只允许经过批准的局部、材质、色彩或部件身份线索",
        "OPTIONAL": "商品可作为小型识别锚点，也可以缺席；不得为了露出商品而挤压核心证据",
        "ABSENT_ALLOWED": "允许商品完全不出镜；本屏必须把功效对象、作用路径或结果变化做成绝对主角",
        "ABSENT_PREFERRED": "优先让完整商品与包装完全缺席；来源、材质、机制、结果、场景或信任证据必须成为绝对主角",
    }.get(presence, "商品必须是第一视觉主体")
    closure_required = presence in ("REQUIRED_DOMINANT", "REQUIRED_SUPPORTING")
    scan_order = (["功效对象", "可见变化", "结果利益"] if subject_policy in ("CLAIM_OBJECT_DOMINANT", "ACTION_RESULT_DOMINANT") else ["核心结论", "卖点证据", "完整商品"])
    return {
        "strategy_type": strategy,
        "target_energy": "HIGH" if order in (1, module_count) else "MEDIUM_HIGH",
        "product_dominance": product_dominance,
        "product_prominence_min_percent": prominence,
        "headline_dominance": "有标题时进入前三视觉层级并服务于商品证据，而不是悬浮口号",
        "headline_must_enter_top_three": headline_required,
        "headline_rank_max": 3,
        "primary_evidence_anchor": plan.get("visual_proof_contract", {}).get("visual_verb", "商品证据"),
        "evidence_must_be_primary_visual": True,
        "depth_plan": "使用功效对象、作用或状态变化、结果环境形成至少三层有意义纵深；商品仅在出镜策略要求时占据其中一层",
        "depth_layers_min": 2 if breathing else 3,
        "visual_tension": "通过商品与核心证据的尺度、动作或状态反差形成可见张力",
        "visual_tension_required": not breathing,
        "background_competition_max": "LOW_TO_MEDIUM",
        "information_density": "LOW" if breathing else "MEDIUM_HIGH",
        "type_role_target": 2 if breathing else 3,
        "unequal_information_groups_min": 2,
        "mobile_scan_stops_min": 2 if breathing else 3,
        "mobile_scan_order": scan_order if not breathing else ["场景价值", "结果利益"],
        "commercial_closure": "最终视线必须落到当前卖点带来的结果利益；只有出镜策略要求时才回到商品",
        "commercial_closure_required": closure_required,
        "creative_permissions": ["功效对象与作用结果双尺度", "受控透视、前景遮挡和景深", "不冒充实验的概念性科学可视化"],
        "forbidden_regression": [
            "商品加一句标题", "装饰替代卖点证据", "所有信息等大平铺",
            "通用摄影棚背景", "漂亮但看不出在卖什么",
        ],
    }


