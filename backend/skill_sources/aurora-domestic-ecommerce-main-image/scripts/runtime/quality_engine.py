from __future__ import annotations

import copy
from typing import Any


ROUTES = (
    {
        "id": "PORTABLE_COOLING_APPLIANCE",
        "terms": ("风扇", "冷风", "制冷", "降温", "冰瓷", "半导体", "出风"),
        "photography": "清爽高完成度淘宝商业摄影；完整商品大体量、真实手持或桌面关系、冷面金属高光、前中后景清楚；背景服从商品，不使用空洞建筑或抽象科技棚景",
        "light": "左前上方35°干净塑形主光，右后方窄轮廓光，冷面金属具有集中高光，浅色机身保持体积阴影",
        "temperature": "4900K–5300K清爽冷白，肤色与桌面保持自然",
        "typography": "高识别中文商业黑体；主张大字参与构图，关键词可用产品冷色强调；数字与单位明确分级，禁止轻飘说明字",
        "graphics": "从风道同心圆、真实制冷片圆面、数显窗和转轴关系提取图形；装饰不能替代真实部件或动作",
        "surfaces": "一至三处不等权信息面；允许圆形真实微距窗和底部商业闭合；禁止均权卡片墙",
        "forbidden": ["多设备办公桌面", "滚轮或跨设备语法", "巨大空建筑", "抽象光弧替代冷面证据", "低对比泛白提案"],
    },
    {
        "id": "SMALL_APPLIANCE",
        "terms": ("小家电", "养生壶", "电饭煲", "吸尘器", "洗地机", "剃须刀", "料理机"),
        "photography": "真实工作状态主导的淘宝商业摄影；完整商品、控制界面、内容物或接触任务承担证明，场景提供尺度和使用关系",
        "light": "按当前材质选择35°–45°中性塑形光，控制区与工作部件同时清楚",
        "temperature": "4800K–5400K，按当前材质和使用场景决定",
        "typography": "清晰有力度的中文商业无衬线；性能、模式和数字与真实控制或任务绑定",
        "graphics": "真实工作状态、短状态标记和绑定正确部件的克制引线",
        "surfaces": "围绕工作界面与结果证据组织不等权信息面，禁止平均参数卡",
        "forbidden": ["默认蓝色科技背景", "能量弧替代工作状态", "参数脱离控制或部件"],
    },
    {
        "id": "OFFICE_ELECTRONICS",
        "terms": ("鼠标", "键盘", "多设备", "蓝牙", "滚轮", "办公鼠标"),
        "photography": "精密办公电子商业摄影；设备轮廓、真实控制动作和多设备关系清楚，桌面只承担任务证据",
        "light": "左上方35°中性柔光主灯，关键控制区保留局部高反差",
        "temperature": "5000K–5400K中性偏冷",
        "typography": "精密技术型中文无衬线，紧凑数字与操作标签绑定真实界面",
        "graphics": "设备节点、切换状态标记和短精密刻度",
        "surfaces": "少量紧凑信息面与真实操作近景结合",
        "forbidden": ["无任务关系的泛办公背景", "游戏炫光默认化", "参数悬空"],
    },
    {
        "id": "GENERAL_PRODUCT_EVIDENCE",
        "terms": (),
        "photography": "当前商品证据驱动的淘宝商业摄影；完整商品、真实材质、动作或使用关系优先，背景和装饰始终从属",
        "light": "按当前商品表面选择中性塑形光，保持轮廓、材质与接触阴影可信",
        "temperature": "4600K–5400K，按当前商品材质与可信场景决定",
        "typography": "从当前商品主张、材质和受众选择同一中文展示家族；标题必须参与构图并保持缩略图识别",
        "graphics": "只从当前商品形态、部件和动作关系提取共享图形语言",
        "surfaces": "不等权信息面，围绕一个主结论与一个真实证据组织",
        "forbidden": ["品类默认配色", "空洞编辑海报", "均权卡片墙", "装饰替代证据"],
    },
)

PERFORMANCE_FIELDS = {
    "product_dominance",
    "headline_dominance",
    "physical_evidence_anchor",
    "depth_plan",
    "commercial_closure",
    "thumbnail_scan_stops",
    "visual_tension",
    "background_competition",
    "generic_editorial_risk",
}

EXECUTION_CONTRACT_FIELDS = {
    "subject_and_view",
    "scene_geometry",
    "composition_blueprint",
    "reading_path",
    "type_hierarchy",
    "required_visible_elements",
    "forbidden_omissions",
    "selling_point_visual_proof_contract",
    "source_scene_isolation_contract",
    "plan_fidelity_rule",
    "continuity_rule",
}


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(item) for item in value)
    return str(value or "")


def _product_text(raw: dict[str, Any], identity: dict[str, Any]) -> str:
    return (_flatten(raw.get("product", {})) + " " + _flatten(identity)).lower()


def select_route(raw: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    text = _product_text(raw, identity)
    for route in ROUTES:
        if route["terms"] and any(term in text for term in route["terms"]):
            return copy.deepcopy(route)
    return copy.deepcopy(ROUTES[-1])


def _preference(raw: dict[str, Any]) -> dict[str, Any]:
    value = raw.get("campaign_preference", {})
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _palette_roles(raw: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    preference = _preference(raw)
    palette = str(preference.get("palette", "")).strip()
    identity_text = "；".join(identity.get("must_preserve", []))
    return {
        "product_identity": identity_text or identity.get("identity_summary", "保持原商品颜色分区"),
        "neutral_support": "从当前商品明度选择冰白、暖白、浅灰或深灰承托，不覆盖商品原色",
        "primary_text": "与当前商品形成清晰明度对比的深色或反白文字",
        "functional_accent": "只从当前商品功能部件、界面或用户明确偏好中提取",
        "user_palette_preference": palette or "未提供；使用商品身份颜色与中性色推导",
        "offer_accent": "仅在完整核验优惠存在时启用",
    }


def _style_evidence(raw: dict[str, Any], identity: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    preference = _preference(raw)
    cues = list(identity.get("must_preserve", []))[:6]
    facts = raw.get("product", {}).get("verified_selling_points", [])
    if isinstance(facts, list):
        cues.extend(str(item) for item in facts[:4])
    mappings = [
        {"source": cue, "visual_consequence": "必须在商品轮廓、材质、动作、局部高光、场景或文字层级中形成可见对应"}
        for cue in cues if str(cue).strip()
    ]
    return {
        "route_id": route["id"],
        "route_basis": "具体商品形态与工作机制优先于宽泛品类词",
        "current_product_cues": mappings,
        "user_preference_applied": {
            "tone": preference.get("tone", ""),
            "palette": preference.get("palette", ""),
            "materials": preference.get("materials", []),
            "style_notes": preference.get("style_notes", ""),
        },
        "forbidden_cross_product_tokens": route["forbidden"],
        "category_is_prior_only": True,
    }


def build_campaign_style(raw: dict[str, Any], identity: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    route = select_route(raw, identity)
    preference = _preference(raw)
    tone = str(preference.get("tone", "")).strip()
    style_notes = str(preference.get("style_notes", "")).strip()
    materials = preference.get("materials", [])
    material_text = "；".join(str(x) for x in materials) if isinstance(materials, list) else str(materials)
    photography = route["photography"]
    if tone:
        photography += f"；用户语气要求={tone}"
    if material_text:
        photography += f"；当前商品材质线索={material_text}"
    if style_notes:
        photography += f"；执行偏好={style_notes}"
    style = {
        "photography_finish": photography,
        "light_direction": route["light"],
        "color_temperature": route["temperature"],
        "palette_roles": _palette_roles(raw, identity),
        "palette_usage_ratio": {
            "background_and_space": 52,
            "product_and_physical_evidence": 28,
            "primary_text": 14,
            "functional_accent": 5,
            "offer_or_counterpoint": 1,
        },
        "typography": {
            "display": route["typography"],
            "numeric": "紧凑粗数字；数字、百分号和单位分级；数字必须绑定真实事实或部件",
            "body": "清晰中文信息字；短句优先；不得机械截断中文",
            "composition_rule": "标题是画面结构，不是后贴说明层；每页一个主张、一个副层、最多三个短证据",
        },
        "graphic_language": route["graphics"],
        "information_surface_geometry": route["surfaces"],
        "density_band": "搜索主图HIGH；身份页MEDIUM；证明页MEDIUM_HIGH；场景页MEDIUM；闭合页MEDIUM_HIGH。留白必须服务商品分离，不得形成空洞海报",
        "prohibited_styles": list(dict.fromkeys(route["forbidden"] + [
            "上一商品的颜色、背景、构图或图形语言",
            "源照片背景泄漏",
            "生成图作为商品身份依据",
            "为了高级感降低商品和主标题存在感",
        ])),
    }
    return style, _style_evidence(raw, identity, route)


def _verified_claims(raw: dict[str, Any]) -> list[str]:
    product = raw.get("product", {})
    values: list[str] = []
    for key in (
        "verified_selling_points",
        "user_provided_display_facts",
        "verified_attributes",
        "verified_scenes",
        "verified_offers",
        "verified_service_or_trust_facts",
    ):
        item = product.get(key, [])
        if isinstance(item, dict):
            values.extend(f"{name}：{value}" for name, value in item.items())
        elif isinstance(item, list):
            values.extend(str(value) for value in item)
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def rank_search_hooks(raw: dict[str, Any]) -> list[dict[str, Any]]:
    differentiators = ("制冷", "半导体", "冰瓷", "结团", "不粘", "防晒", "双线", "安全带", "材质", "自带")
    visible = ("片", "屏", "按键", "滚轮", "面料", "颗粒", "膏体", "接口", "线", "结构", "玻璃")
    generic = ("新款", "神器", "高端", "升级", "强劲", "便携", "好用")
    ranked = []
    service_or_trust = _service_or_trust_claims(raw)
    eligible = [claim for claim in _verified_claims(raw) if claim not in service_or_trust]
    for order, claim in enumerate(eligible):
        score = 50 - min(order, 20)
        reasons = []
        if any(word in claim for word in differentiators):
            score += 24
            reasons.append("差异化机制或结果")
        if any(word in claim for word in visible):
            score += 14
            reasons.append("存在可见部件或材质证据")
        if any(char.isdigit() for char in claim):
            score += 5
            reasons.append("含可扫读参数")
        if any(word in claim for word in generic) and not reasons:
            score -= 12
            reasons.append("仅通用营销词")
        ranked.append({"source_text": claim, "score": score, "reasons": reasons or ["已核验事实"]})
    return sorted(ranked, key=lambda item: (-item["score"], item["source_text"]))


def _pick_claim(claims: list[str], terms: tuple[str, ...], used: set[str]) -> str | None:
    candidates = [claim for claim in claims if claim not in used and any(term in claim for term in terms)]
    return candidates[0] if candidates else next((claim for claim in claims if claim not in used), None)


def assign_page_claims(raw: dict[str, Any], ranking: list[dict[str, Any]]) -> list[str]:
    service_or_trust = _service_or_trust_claims(raw)
    claims = list(
        dict.fromkeys(
            item["source_text"]
            for item in ranking
            if item["source_text"] not in service_or_trust
        )
    )
    if not claims:
        return [raw["product"]["name"]] * 6
    used: set[str] = set()
    role_terms = (
        (),
        ("包装", "翻盖", "抽取", "便携", "外观", "设计", "型号", "容量", "身份"),
        ("材质", "面料", "无纺布", "纹理", "纤维", "加厚", "结构", "成分", "原料"),
        ("按键", "数显", "调速", "模式", "操作", "手持", "场景", "使用", "清洁", "擦拭"),
        ("安全", "认证", "试验", "零添加", "不含", "温和", "信任", "售后", "保障"),
        ("价格", "优惠", "活动", "规格", "整箱", "套装", "片", "容量", "续航", "充电"),
    )
    assigned: list[str] = []
    for index, terms in enumerate(role_terms):
        claim = claims[0] if index == 0 else _pick_claim(claims, terms, used)
        if claim is None:
            claim = next((item for item in claims if item not in used), None)
        if claim is None:
            claim = claims[index % len(claims)]
        assigned.append(claim)
        used.add(claim)
    return assigned


def _service_or_trust_claims(raw: dict[str, Any]) -> set[str]:
    """Keep service facts available for closure, never as a page's lead claim."""

    value = raw.get("product", {}).get("verified_service_or_trust_facts", [])
    if isinstance(value, dict):
        return {str(item).strip() for item in value.values() if str(item).strip()}
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()} if str(value).strip() else set()


def proof_method_for_claim(claim: str) -> str:
    if any(term in claim for term in ("按键", "数显", "调速", "模式", "切换", "滚轮")):
        return "真实手部操作、控制区或工作界面反馈"
    if any(term in claim for term in ("材质", "冰瓷", "面料", "颗粒", "膏体", "薄脆", "玻璃")):
        return "当前商品真实材质或内容物微距，并保留完整商品身份"
    if any(term in claim for term in ("90°", "结构", "支撑", "安全带", "转轴", "接口", "线")):
        return "真实结构、连接点或不等权多状态关系"
    if any(term in claim for term in ("续航", "静音", "充电", "保温", "规格", "兼容")):
        return "可信使用场景提供语境；不可见参数只以已核验文字表达"
    return "当前商品完整身份加一个真实部件、材质、动作或使用关系"


def _observability_class(claim: str) -> str:
    if any(term in claim for term in ("材质", "冰瓷", "面料", "颗粒", "膏体", "玻璃", "结构", "转轴", "接口", "数显", "按键")):
        return "DIRECTLY_VISIBLE"
    if any(term in claim for term in ("手持", "桌面", "挂脖", "调节", "切换", "制冷", "冰敷", "清洁", "使用", "充电")):
        return "ACTION_DEMONSTRABLE"
    if any(term in claim for term in ("续航", "分贝", "小时", "分钟", "认证", "质保", "价格", "优惠")):
        return "TEXT_ONLY_VERIFIED"
    return "ACTION_DEMONSTRABLE"


def selling_point_visual_proof_contract(claim: str, task: dict[str, Any]) -> dict[str, Any]:
    observability = _observability_class(claim)
    method = proof_method_for_claim(claim)
    renderable = observability in {"DIRECTLY_VISIBLE", "ACTION_DEMONSTRABLE"}
    material_claim = any(term in claim for term in ("材质", "面料", "无纺布", "纹理", "纤维", "柔韧", "厚实"))
    proof_mode = "DIRECT_MACRO_MATERIAL_PROOF" if material_claim else ("DIRECT_ACTION_PROOF" if observability == "ACTION_DEMONSTRABLE" else "DIRECT_COMPONENT_PROOF")
    primary_evidence = (
        "展开的真实材料成为第一主体；折边、表面纹理与纤维细节在侧逆光/微距中清楚可辨"
        if material_claim else method
    )
    bundle_claims = task.get("selling_point_bundle_source_texts") or [claim, *task.get("supporting_selling_points", [])]
    bundle_claims = list(dict.fromkeys(bundle_claims))
    claim_evidence_plan = [
        {
            "source_text": item,
            "role": "LEAD" if item == claim else "SUPPORT",
            "observability_class": _observability_class(item),
            "visual_proof_method": proof_method_for_claim(item),
        }
        for item in bundle_claims
    ]
    return {
        "source_text": claim,
        "bundle_source_texts": bundle_claims,
        "claim_evidence_plan": claim_evidence_plan,
        "observability_class": observability,
        "buyer_doubt": str(task.get("buyer_question") or task.get("buyer_demand") or "这个卖点是否由当前商品真实承担"),
        "must_be_visible_without_copy": "画面应先传达主利益与商品关系；文字负责精确限定事实" if renderable else "参数由文字准确限定，画面用真实商品与可信语境建立价值感",
        "pixel_evidence_method": method,
        "renderable_claim_must_survive_copy_removal": renderable,
        "text_only_boundary": "数字、认证、服务和不可见机理由文字准确限定；可见利益优先转译为商品、材质、动作、状态、结果或可信视觉隐喻",
        "forbidden_text_only_fallback": renderable,
        "visual_storyboard": {
            "proof_mode": proof_mode,
            "primary_physical_evidence": primary_evidence,
            "supporting_evidence": "允许一个强主画面统领多个相关卖点；辅助卖点只有在增强主叙事时才加入，不逐条配图，不做检查清单",
            "camera_scale_light": "由执行Agent选择最能放大利益感的镜头、尺度、光线、动作和空间张力，不受预设百分比或固定机位约束",
            "evidence_dominance_target": "商品与主利益关系必须是第一或并列第一视觉；不规定机械占比",
            "claim_to_evidence_relation": f"观者先感受到“{claim}”带来的利益，再由短文案精确理解事实",
            "no_copy_takeaway": f"遮住文案后仍能感到本页在强化“{claim}”的核心利益方向，无需逐字复述原句",
            "creative_expression_permission": "允许可信的商业夸张、感官氛围、尺度对比、动态效果、结果化场景和视觉隐喻；不得把表现手法写成新的数字、认证或结构事实",
            "forbidden_substitutes": ["大面积纯色或氛围背景", "图标/箭头/光效", "卖点文字", "与证明无关的人物或装饰", "小尺寸角落特写"],
        },
    }


def search_cover_blueprint(raw: dict[str, Any], hook: str, ranking: list[dict[str, Any]]) -> dict[str, Any]:
    offers = raw.get("product", {}).get("verified_offers", [])
    offer_mode = bool(offers)
    proofs = [item["source_text"] for item in ranking[1:4]]
    return {
        "role": "TAOBAO_LISTING_CLICK_COVER_NOT_CAROUSEL_HERO",
        "transaction_mode": "OFFER_COMPLETE" if offer_mode else "NO_OFFER",
        "working_bands": {
            "brand_identity_header": "6%–10%; current brand/store/authenticity only",
            "product_and_message_zone": "58%–72%",
            "bottom_commercial_closure": "16%–24%" if offer_mode else "10%–16%",
            "safe_frame": "2%–4%",
        },
        "hero_contract": "完整当前商品外接框约占画布42%–58%，高度约55%–75%；商品是第一或并列第一视觉，不得成为场景摆件",
        "headline_contract": f"最高排序点击钩子“{hook}”必须成为大标题并参与构图，不得写成轻量说明字",
        "proof_modules": proofs,
        "proof_module_rule": "允许0–5个不等权辅助模块；一个强主画面可统领多个相关卖点，不要求逐条配图，禁止均权卡片墙",
        "useful_information_preservation_rule": "品牌、规格、数量、价格/优惠、信任、服务或单位信息栏只要已由用户提供且层级清楚就应保留；不得仅因一页出现多个卖点或模块而删除",
        "physical_proof_object": "至少一个真实物理证据/使用结果/操作关系进入主画面；纯图标、光弧、雪花符号和文字标签不计",
        "type_roles": "品牌身份 → 强主标题/数字锚点 → 证据短句 → 交易或规格结论 → 信任闭合，至少四级可见差异",
        "scan_stops": ["完整大商品或强主标题", "一个真实证据锚点", "底部商业闭合"],
        "closure_rule": "有完整核验优惠时使用一个主交易锚点和至多两个条件；无优惠时用规格/服务/适用人群/正品信任闭合，禁止价格氛围",
        "visual_strength": "高完成度商业合成：商品、标题、证据与上下闭合区形成咬合；不得退化为留白生活方式海报、普通棚拍或详情页首图",
        "unique_role_test": "隐藏文件名和页序后，六图中必须只有本页会被识别为搜索主图；若任一轮播页也同样像搜索主图，或本页只能被猜测而不能确定，则整套失败",
        "transfer_source": "用户验证的十套搜索主图共同规律；九阳HU108仅是质量标尺，不复制其品牌、配色、文案、商品姿态或完整版式",
    }


def approved_page_execution_contract(page: dict[str, Any], task: dict[str, Any], proof: dict[str, Any], search_blueprint: dict[str, Any] | None) -> dict[str, Any]:
    index = page["index"]
    layouts = (
        "商业搜索封面：品牌顶栏+大商品/强标题咬合+真实证据+底部闭合",
        "点击后身份页：真实手持/使用或完整商品场景，低于搜索页信息密度",
        "核心机理证明：完整商品与真实部件/材质双尺度，不等权分区",
        "操作或场景证明：手部/人物/工作状态与商品形成接触关系",
        "结构细节证明：自然视角完整商品+关键局部微距，避免悬空参数",
        "规格/信任收口：完整商品或包装+规格/服务闭合，不复制搜索交易带",
    )
    subject_views = (
        "完整商品大体量主视觉；选择最能同时守住身份与点击钩子的正面或可信三分之四视角",
        "与搜索封面不同的自然视角和尺度；商品或真实使用主体占主导",
        "完整商品与卖点对应真实局部形成双尺度证据",
        "真实手部、人物或场景动作与商品正确接触",
        "自然完整视角保身份，局部微距只证明真实结构/材质",
        "完整商品、包装或最终使用状态承担购买闭合",
    )
    reading = search_blueprint["scan_stops"] if search_blueprint else ["本页主结论", "真实物理证据", "短闭合"]
    required = [
        "完整可识别的当前商品身份",
        f"与“{proof['source_text']}”对应的真实像素证据",
        "与本页职责一致且清楚分级的主标题",
        "可信接触阴影、尺度关系、动作关系或前中后景中的至少一项",
    ]
    if index == 0:
        required.extend(["顶部品牌/身份区", "底部商业闭合区", "至少四级文字角色差异", "相对于五张轮播页唯一明确的搜索入口角色"])
    contract = {
        "subject_and_view": subject_views[index],
        "scene_geometry": "背景从当前商品形态、材质、机制与可信使用场景推导；原图背景是采集噪声；主体、证据、标题必须形成明确空间关系",
        "composition_blueprint": layouts[index],
        "reading_path": reading,
        "type_hierarchy": search_blueprint["type_roles"] if search_blueprint else "一个统领性主结论、若干有主次的相关卖点与短闭合；文字是构图结构而非后贴说明",
        "required_visible_elements": required,
        "forbidden_omissions": [
            "不得丢失本页职责、主卖点利益、当前商品身份和必要交易条件",
            "不得让文字成为唯一主体而商品与利益关系退化为装饰",
            "不得用均权卡片和逐条对照资料取代一个强主画面",
        ],
        "selling_point_visual_proof_contract": proof,
        "source_scene_isolation_contract": {
            "source_background_is_capture_noise": True,
            "rebuild_scene": True,
            "survives_repairs_and_user_adjustments": True,
            "forbidden_leakage": ["原图桌面", "原图墙面", "原图缝隙", "原图杂物", "原图键盘", "拍摄环境边角"],
            "automatic_repair_canvas_rule": "返修只能编辑第一次生成的干净商业画布；原图仅核对商品身份，禁止再次把完整原始照片作为编辑底图。",
        },
        "plan_fidelity_rule": "本合同冻结页面职责、主卖点意义、买家结论、商品身份和必要商业条件，不冻结像素版式。执行Agent可重选镜头、构图、比例、动作、光线、文案措辞和视觉隐喻，只要结果更有表现力且不改变事实。",
        "creative_freedom": {
            "hard_locks": ["商品身份", "用户事实的含义/数字/条件", "本页职责与主利益", "整套视觉世界", "必要商业闭合"],
            "agent_judgment": ["镜头与裁切", "主体尺度", "构图和空间关系", "光线与氛围", "动作/结果/视觉隐喻", "短文案措辞", "辅助卖点取舍"],
            "success_test": "不是逐项对照计划，而是缩略图中商品突出、主利益鲜明、画面有购买冲动且事实无漂移。",
        },
        "continuity_rule": "本页合同在首次生图、自动返修和用户中途修改后都持续生效；用户修改仅叠加明确差量，未点名部分不得变化。",
    }
    if search_blueprint is not None:
        contract["search_cover_blueprint"] = search_blueprint
    return contract


def performance_contract(index: int, task: dict[str, Any]) -> dict[str, Any]:
    if index == 0:
        return {
            "product_dominance": "完整商品为第一或并列第一视觉；建议占画高55%–75%，不得小于主要装饰物",
            "headline_dominance": "一个最强点击钩子形成大标题；与商品共同构成两至三个扫描停顿",
            "physical_evidence_anchor": "至少一个当前商品真实部件、材质、动作或工作状态承担差异化证明",
            "depth_plan": "使用真实台面、前景切入、接触阴影或场景纵深中的至少两项，禁止空洞建筑背景",
            "commercial_closure": "必须有一条已核验规格、服务、受众或优惠闭合；无完整优惠时禁止价格氛围",
            "thumbnail_scan_stops": 3,
            "visual_tension": "商品、标题与证据形成咬合、对角或三角张力，禁止平行摆放成提案页",
            "background_competition": "LOW；背景不得成为第一主体",
            "generic_editorial_risk": "搜索主图不得退化为轻飘生活方式海报或纯产品棚拍",
        }
    if index == 1:
        return {
            "product_dominance": "完整商品或真实使用主体占画面主导，建议占画高55%–80%",
            "headline_dominance": "身份主张清晰有力度，但信息量低于搜索主图",
            "physical_evidence_anchor": "真实手持、穿着、使用、内容物或结构关系至少一项",
            "depth_plan": "人物/手部/场景与商品形成可信尺度和接触关系",
            "commercial_closure": "一条身份、适用或核心规格短闭合，不复制搜索底栏",
            "thumbnail_scan_stops": 2,
            "visual_tension": "商品与真实关系成为第一视觉，留白只服务轮廓和标题",
            "background_competition": "LOW",
            "generic_editorial_risk": "不得把商品缩成时尚道具或静态站立提案",
        }
    return {
        "product_dominance": "商品、真实局部或动作证据必须主导，不得由装饰图标主导",
        "headline_dominance": "一个页面主结论清楚，数字和单位有主次",
        "physical_evidence_anchor": str(task.get("visual_proof_method", "当前商品真实证据")),
        "depth_plan": "使用局部/完整双尺度、动作前后关系、场景纵深或不等权分区中的至少一种",
        "commercial_closure": "以本页已核验事实完成短闭合，不制造新优惠或权威",
        "thumbnail_scan_stops": 2 if index in (2, 3) else 3,
        "visual_tension": "证据、标题与商品形成明确阅读方向，禁止平均卡片分布",
        "background_competition": "LOW_TO_MEDIUM，且始终从属于证据",
        "generic_editorial_risk": "去掉文字后仍必须看懂本页证明对象",
    }


def enhance_v013_plan(plan: dict[str, Any], raw: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(plan)
    style, evidence = build_campaign_style(raw, identity)
    result["campaign_style"] = style
    result["style_evidence_contract"] = evidence
    result["search_hook_ranking"] = rank_search_hooks(raw)
    hook = result["search_hook_ranking"][0]["source_text"] if result["search_hook_ranking"] else raw["product"]["name"]
    assigned_claims = assign_page_claims(raw, result["search_hook_ranking"])
    search_blueprint = search_cover_blueprint(raw, hook, result["search_hook_ranking"])
    result["search_cover_blueprint"] = search_blueprint
    result["design_intelligence"] = {
        "product_form_adapter": evidence["route_id"],
        "campaign_thesis": f"以当前商品身份和“{hook}”为最高优先级，把真实部件、材质或动作证据转化为高表现力淘宝商业画面",
        "product_derived_visual_cues": [item["source"] for item in evidence["current_product_cues"][:6]] or [identity["identity_summary"]],
        "evidence_topology_rationale": "搜索页用最强差异化钩子和完整商品抢点击；后续页面按买家疑虑切换真实材质、动作、场景、结构与闭合证据",
        "prohibited_template_shortcuts": list(evidence["forbidden_cross_product_tokens"]),
    }
    commercial = result["commercial_orchestration"]
    commercial["information_confidence"] = [
        {"source_text": claim, "confidence_level": "CONFIRMED", "authority": "USER_FACT"}
        for claim in list(dict.fromkeys([raw["product"]["name"], *_verified_claims(raw)]))
    ]
    if commercial.get("page_task_table"):
        ranked_claims = [item["source_text"] for item in result["search_hook_ranking"]]
        # Do not distribute leftover facts merely to maximize coverage. That
        # turns six images into a checklist and creates unrelated bundles. The
        # lead claim drives each page; optional support is selected only when a
        # reviewed/manual strategy explicitly supplies it.
        support_by_page: dict[int, list[str]] = {index: [] for index in range(6)}
        for index, task in enumerate(commercial["page_task_table"]):
            claim = assigned_claims[index]
            task["matched_selling_point"] = claim
            task["primary_selling_point_source_text"] = claim
            bundle = list(dict.fromkeys([claim, *support_by_page[index]]))
            task["selling_point_bundle_id"] = f"PAGE_{index}_{task['page_role']}"
            task["selling_point_bundle_source_texts"] = bundle
            task["selling_point_display_label"] = " / ".join(bundle)
            task["supporting_selling_points"] = support_by_page[index]
            task["claim_visual_evidence_map"] = [
                {
                    "source_text": item,
                    "role": "LEAD" if item == claim else "SUPPORT",
                    "visual_proof_method": proof_method_for_claim(item),
                }
                for item in bundle
            ]
            task["non_repetition_reason"] = f"第{index}页承担“{task['buyer_demand']}”这一独立购买任务；允许同页组合相关卖点，但不得让连续页面复述相同卖点组合、相同证明对象和相同结论"
            task["visual_proof_method"] = proof_method_for_claim(claim)
            task["required_evidence"] = "当前商品原图中与本页主张对应的完整身份、真实部件、材质、动作、界面或可信场景"
            task["copy_direction"] = (
                f"优先表达最高排序点击钩子“{claim}”；允许保留相关辅助卖点和商业闭合，只要主次清楚"
                if index == 0
                else f"本页围绕“{claim}”组织一个主结论；相关辅助卖点可同时出现，但不得改变本页职责"
            )
        commercial["demand_to_selling_point_match"] = [
            {
                "page_index": task["page_index"],
                "buyer_demand": task["buyer_demand"],
                "matched_selling_point": task["matched_selling_point"],
                "visual_proof_method": task["visual_proof_method"],
            }
            for task in commercial["page_task_table"]
        ]
        commercial["selling_point_coverage_map"] = []
        for claim in ranked_claims:
            if claim in assigned_claims:
                usage = "PRIMARY"
                page_indexes = [assigned_claims.index(claim)]
            else:
                page_indexes = [index for index, items in support_by_page.items() if claim in items]
                usage = "SUPPORT" if page_indexes else "DEFERRED_DUE_TO_SIX_IMAGE_CAPACITY"
            commercial["selling_point_coverage_map"].append({
                "source_text": claim,
                "usage": usage,
                "page_indexes": page_indexes,
            })
    for page in result["pages"]:
        task = commercial["page_task_table"][page["index"]]
        # The bridge creates a page-local task snapshot before v0.1.17 enriches
        # the orchestration table. Keep one authority after enrichment so
        # generate-next never reads stale legacy task fields.
        page["commercial_page_task"] = copy.deepcopy(task)
        page["claim_source_texts"] = list(task["selling_point_bundle_source_texts"])
        proof = selling_point_visual_proof_contract(task["matched_selling_point"], task)
        page["approved_page_execution_contract"] = approved_page_execution_contract(
            page, task, proof, search_blueprint if page["index"] == 0 else None
        )
        page["visual_performance_contract"] = performance_contract(page["index"], task)
        page["v015_execution_prompt"] = concise_execution_prompt(raw, identity, style, evidence, task, page)
    result["compiler_lineage"] = "V0_1_13_FACT_IDENTITY_INPUT_PLUS_V0_1_17_STRATEGY_BOUND_CREATIVE_EXECUTION_ENGINE"
    return result


def concise_execution_prompt(raw: dict[str, Any], identity: dict[str, Any], style: dict[str, Any], evidence: dict[str, Any], task: dict[str, Any], page: dict[str, Any]) -> str:
    execution = page["approved_page_execution_contract"]
    proof = execution["selling_point_visual_proof_contract"]
    storyboard = proof["visual_storyboard"]
    performance = page["visual_performance_contract"]
    identity_locks = list(page["generation_contract"].get("locked_product_invariants", []))
    compact_identity_locks = identity_locks[:8]
    search_context = (
        "【搜索主图硬要求】必须同时出现品牌/身份顶栏、商品与强主标题、真实证明锚点、底部商业闭合；"
        + "至少四级可见字体角色；商品约占画布高度55%–75%；禁止退化成白底商品海报。"
        if page["index"] == 0 else ""
    )
    return "\n".join([
        "【最高优先级：只执行当前页已批准合同】",
        f"第{page['index'] + 1}张；页面职责={page['page_role']}；买家结论={task.get('buyer_question') or task.get('buyer_demand')}。",
        "本页唯一主卖点=" + task["primary_selling_point_source_text"] + "。",
        "主卖点必须由真实像素证明，删掉全部文案后仍能看懂；不得只把卖点写成标题。",
        "【构图蓝图】" + str(execution.get("composition_blueprint")),
        "【主体与视角】" + str(execution.get("subject_and_view")),
        "【场景空间】" + str(execution.get("scene_geometry")),
        "【阅读路径】" + " → ".join(str(item) for item in execution.get("reading_path", [])),
        "【必须逐项可见】" + "；".join(str(item) for item in execution.get("required_visible_elements", [])),
        "【物理证明】" + str(storyboard.get("primary_physical_evidence")) + "；关系=" + str(storyboard.get("claim_to_evidence_relation")),
        "【缩略图目标】商品=" + str(performance.get("product_dominance")) + "；标题=" + str(performance.get("headline_dominance")) + "；闭合=" + str(performance.get("commercial_closure")),
        search_context,
        "【商品身份】" + identity["identity_summary"],
        "关键身份锁=" + "；".join(str(item) for item in compact_identity_locks) + "。",
        "【整组风格】摄影=" + str(style.get("photography_finish")) + "；光线=" + str(style.get("light_direction")) + "；字体=" + str(style.get("typography", {}).get("display")) + "。",
        "【禁止退化】不得生成普通白底棚拍、纯产品目录图或只有标题与商品的模板；不得复制前一页的机位、商品姿态、背景和版式骨架；不得用图标、光效或文字代替物理证明。",
        "原图仅用于保持商品身份，原图背景是采集噪声，必须重建为符合本页职责的商业场景。不得发明价格、认证、数字、部件、结构或功效。",
        "输出1:1方图、至少1024×1024，中文必须完整可读。先满足本页职责、构图蓝图、必须可见元素和物理证明，再考虑装饰。",
    ])


def validate_quality_plan(plan: dict[str, Any]) -> None:
    evidence = plan.get("style_evidence_contract")
    if not isinstance(evidence, dict) or not evidence.get("route_id"):
        raise ValueError("style_evidence_contract is required")
    cues = evidence.get("current_product_cues")
    if not isinstance(cues, list) or not cues:
        raise ValueError("current-product style cues are required")
    ranking = plan.get("search_hook_ranking")
    if not isinstance(ranking, list) or not ranking:
        raise ValueError("search_hook_ranking is required")
    style = plan.get("campaign_style", {})
    style_text = _flatten({key: value for key, value in style.items() if key != "prohibited_styles"})
    if evidence["route_id"] == "PORTABLE_COOLING_APPLIANCE" and any(token in style_text for token in ("多设备办公桌面", "滚轮", "跨设备")):
        raise ValueError("portable cooling route contains office-electronics style contamination")
    pages = plan.get("pages")
    if not isinstance(pages, list) or len(pages) != 6:
        raise ValueError("quality plan must contain six pages")
    for page in pages:
        contract = page.get("visual_performance_contract")
        if not isinstance(contract, dict) or PERFORMANCE_FIELDS - set(contract):
            raise ValueError(f"page {page.get('index')} visual_performance_contract is incomplete")
        execution = page.get("approved_page_execution_contract")
        if not isinstance(execution, dict) or EXECUTION_CONTRACT_FIELDS - set(execution):
            raise ValueError(f"page {page.get('index')} approved_page_execution_contract is incomplete")
        proof = execution.get("selling_point_visual_proof_contract", {})
        if proof.get("renderable_claim_must_survive_copy_removal") is True and proof.get("forbidden_text_only_fallback") is not True:
            raise ValueError(f"page {page.get('index')} renderable selling point permits text-only fallback")
        storyboard = proof.get("visual_storyboard")
        if not isinstance(storyboard, dict) or not all(storyboard.get(key) for key in ("proof_mode", "primary_physical_evidence", "camera_scale_light", "evidence_dominance_target", "claim_to_evidence_relation", "no_copy_takeaway", "forbidden_substitutes")):
            raise ValueError(f"page {page.get('index')} selling-point visual storyboard is incomplete")
        isolation = execution.get("source_scene_isolation_contract")
        if not isinstance(isolation, dict) or isolation.get("source_background_is_capture_noise") is not True or isolation.get("survives_repairs_and_user_adjustments") is not True:
            raise ValueError(f"page {page.get('index')} source-scene isolation contract is incomplete")
        if page.get("index") == 0 and not isinstance(execution.get("search_cover_blueprint"), dict):
            raise ValueError("search cover requires its dedicated commercial blueprint")
        if not isinstance(page.get("v015_execution_prompt"), str) or not page["v015_execution_prompt"].strip():
            raise ValueError(f"page {page.get('index')} v0.1.15 execution prompt is missing")
