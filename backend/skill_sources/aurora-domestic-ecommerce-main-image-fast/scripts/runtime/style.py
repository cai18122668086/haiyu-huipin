from __future__ import annotations

from typing import Any

from core import ROOT, fact_records, load_json


VISIBLE_TERMS = ("材质", "纹理", "结构", "接口", "按键", "数显", "屏", "包装", "面料", "颗粒", "膏体", "玻璃", "转轴", "部件")
ACTION_TERMS = ("使用", "操作", "调节", "切换", "清洁", "穿", "装", "折", "开", "关", "充电", "制冷", "加热", "喂", "倒", "抽取")
TEXT_TERMS = ("小时", "分钟", "分贝", "认证", "质保", "价格", "优惠", "兼容", "规格", "服务", "排名", "销量")
GENERIC_TERMS = ("新款", "升级", "高端", "神器", "好用", "强劲")


def select_route(raw: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    routes = load_json(ROOT / "references" / "category-priors.json")["routes"]
    product_text = " ".join([
        raw["product"]["name"],
        raw["product"]["category"],
        identity["product_form"],
        identity["identity_summary"],
    ])
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for order, route in enumerate(routes):
        matched = [term for term in route["terms"] if term in product_text]
        if matched:
            specificity = max(len(term) for term in matched)
            candidates.append((specificity, -order, route))
    return max(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else routes[-1]


def build_style(raw: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    route = select_route(raw, identity)
    preference = raw.get("campaign_preference", {}) if isinstance(raw.get("campaign_preference"), dict) else {}
    palette = str(preference.get("palette", "")).strip()
    tone = str(preference.get("tone", "")).strip()
    style_notes = str(preference.get("style_notes", "")).strip()
    return {
        "route_id": route["id"],
        "photography_finish": route["photography"] + (f"；语气={tone}" if tone else "") + (f"；偏好={style_notes}" if style_notes else ""),
        "light_direction": route["light"],
        "color_temperature": route["temperature"],
        "palette_roles": {
            "product_identity": "保持原商品颜色分区",
            "neutral_support": "从商品明度和材质推导中性承托色",
            "primary_text": "与背景和商品形成清晰对比",
            "functional_accent": palette or "从真实功能部件或界面提取",
            "offer_accent": "仅在完整核验优惠存在时启用",
        },
        "palette_usage_ratio": {"background_and_space": 52, "product_and_physical_evidence": 28, "primary_text": 14, "functional_accent": 5, "offer_or_counterpoint": 1},
        "typography": {"display": route["typography"], "numeric": "紧凑粗数字；数字、百分号和单位分级并绑定真实事实", "support": "短句、自然语义断行", "composition_rule": "标题是画面结构而非后贴说明；搜索页至少四级文字角色，其他页保持清楚主次"},
        "graphic_language": route["graphics"] + "；装饰不替代证明",
        "information_surfaces": route["surfaces"],
        "density_band": "搜索主图HIGH；身份页MEDIUM；证明页MEDIUM_HIGH；场景页MEDIUM；闭合页MEDIUM_HIGH。留白只服务商品分离，不得形成空洞海报",
        "prohibited_styles": list(dict.fromkeys(route["avoid"] + ["source-photo background leakage", "generated image as product evidence", "equal-card wall", "decoration replacing proof"])),
        "route_proof_prior": route["proof"],
        "durable_goods_prior_active": bool(raw.get("durable_goods_prior_opt_in", False) and route["id"] == "HOME_DURABLE"),
    }


def observability(claim: str) -> str:
    if any(term in claim for term in VISIBLE_TERMS):
        return "DIRECTLY_VISIBLE"
    if any(term in claim for term in ACTION_TERMS):
        return "ACTION_DEMONSTRABLE"
    if any(term in claim for term in TEXT_TERMS) or any(char.isdigit() for char in claim):
        return "TEXT_ONLY_VERIFIED"
    return "NOT_RENDERABLE"


def proof_method(claim: str, route: dict[str, Any]) -> str:
    kind = observability(claim)
    if kind == "DIRECTLY_VISIBLE":
        return "用当前商品的真实材质、部件、结构、界面或内容物承担主证明"
    if kind == "ACTION_DEMONSTRABLE":
        return "用可信操作、接触、使用状态或前后关系承担主证明"
    if kind == "TEXT_ONLY_VERIFIED":
        return "由准确文字限定参数，并用完整商品和可信使用语境建立价值感"
    return f"不制造伪证据；以完整商品和品类允许的真实关系承托，候选证据为 {route['proof']}"


def rank_claims(raw: dict[str, Any]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for order, item in enumerate(fact_records(raw)):
        text = item["text"]
        score = 100 - order
        kind = observability(text)
        if kind == "DIRECTLY_VISIBLE":
            score += 25
        elif kind == "ACTION_DEMONSTRABLE":
            score += 18
        elif kind == "TEXT_ONLY_VERIFIED":
            score += 6
        if item["source"] == "verified_selling_points":
            score += 16
        if any(term in text for term in GENERIC_TERMS) and kind == "NOT_RENDERABLE":
            score -= 20
        ranked.append({**item, "score": score, "observability": kind})
    return sorted(ranked, key=lambda item: (-item["score"], item["text"]))
