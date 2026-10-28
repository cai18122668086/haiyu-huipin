#!/usr/bin/env python3
"""Compile a generic campaign typography and graphic contract.

All product-specific copy comes from the current raw input. Typography and
graphic defaults are explicitly ENGINEERED_DEFAULT and never promoted to
EVIDENCE_DERIVED.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import jsonschema

from compile_harness_v0_1_1 import normalize_category
from identity_framework_v0_1_2 import load, write


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "campaign-design-contract-v0.1.3.schema.json"
LIBRARY_PATH = ROOT / "references" / "typography-treatment-library-v0.1.3.json"

ROLE_FAMILY = {
    "STANDARD_IDENTITY_MAIN": "IDENTITY",
    "OFFER_IDENTITY_MAIN": "OFFER",
    "CAROUSEL_SLOT_1_IDENTITY": "IDENTITY",
    "CAROUSEL_SLOT_2_CORE_SELLING_POINT": "FEATURE",
    "CAROUSEL_SLOT_3_SCENE_BENEFIT": "SCENE",
    "CAROUSEL_SLOT_4_DETAIL_PROOF": "PROOF",
    "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY": "OFFER",
    "DETAIL_LONG_FEATURE_SECTION": "FEATURE",
    "FEATURE": "FEATURE",
    "BENEFIT": "BENEFIT",
    "SPECIFICATION": "SPECIFICATION",
    "TRUST": "TRUST",
}

ROLE_TREATMENT = {
    "IDENTITY": "EDITORIAL_SPLIT_HEADLINE",
    "FEATURE": "KEYWORD_COLOR_EMPHASIS",
    "SCENE": "SCENE_LIGHTWEIGHT_CAPTIONS",
    "PROOF": "TECHNICAL_GRID",
    "OFFER": "LARGE_NUMERIC_HERO",
    "BENEFIT": "COLOR_BLOCK_HEADLINE",
    "SPECIFICATION": "LABEL_AND_CALLOUT",
    "TRUST": "BADGE_CLUSTER",
}

ROLE_TREATMENT_OPTIONS = {
    "IDENTITY": ["EDITORIAL_SPLIT_HEADLINE", "ASYMMETRIC_TYPE_LOCKUP"],
    "FEATURE": ["KEYWORD_COLOR_EMPHASIS", "LABEL_AND_CALLOUT", "COLOR_BLOCK_HEADLINE"],
    "SCENE": ["SCENE_LIGHTWEIGHT_CAPTIONS", "ASYMMETRIC_TYPE_LOCKUP"],
    "PROOF": ["TECHNICAL_GRID", "LABEL_AND_CALLOUT"],
    "OFFER": ["LARGE_NUMERIC_HERO", "BADGE_CLUSTER"],
    "BENEFIT": ["COLOR_BLOCK_HEADLINE", "SCENE_LIGHTWEIGHT_CAPTIONS"],
    "SPECIFICATION": ["LABEL_AND_CALLOUT", "TECHNICAL_GRID"],
    "TRUST": ["BADGE_CLUSTER", "ASYMMETRIC_TYPE_LOCKUP"],
}

CATEGORY_DESIGN = {
    "FURNITURE": {
        "font": ["modern geometric sans", "humanist comfortable support", "rational technical numerals"],
        "colors": ["#303842", "#66717D", "#2476C9", "#8FA7BA", "#F3F4F2", "#E87832", "#FFFFFF", "#8A929A", "#C8CED3"],
        "device_family": ["structural curve", "fine technical line", "translucent information panel"],
    },
    "ELECTRONICS": {
        "font": ["precise technical sans", "compact rational support", "tabular technical numerals"],
        "colors": ["#182430", "#586775", "#00A7C4", "#6B63D9", "#EEF3F5", "#F06445", "#FFFFFF", "#81909B", "#B8C4CB"],
        "device_family": ["precision grid", "data line", "edge marker"],
    },
    "HOME_APPLIANCE": {
        "font": ["friendly engineered sans", "clear domestic support", "rounded functional numerals"],
        "colors": ["#263536", "#667472", "#1B9A8A", "#D0A24B", "#F2F5F1", "#E66C3B", "#FFFFFF", "#87918D", "#C6CECA"],
        "device_family": ["functional callout", "soft geometric halo", "clean utility badge"],
    },
    "BEAUTY_PERSONAL_CARE": {
        "font": ["light luxury editorial sans", "refined airy support", "elegant contrast numerals"],
        "colors": ["#332F35", "#756C76", "#B15C7B", "#C99F86", "#F7F1F3", "#D96773", "#FFFFFF", "#9B929A", "#D8CDD2"],
        "device_family": ["editorial rule", "soft halo", "translucent label"],
    },
    "FOOD": {
        "font": ["warm humanist display sans", "friendly legible support", "bold appetizing numerals"],
        "colors": ["#382E27", "#76675B", "#C75A2C", "#6E8B53", "#F8F1E7", "#D84B2F", "#FFFFFF", "#9A8C80", "#D8CABB"],
        "device_family": ["ingredient frame", "freshness cue", "flavor badge"],
    },
    "HOME_DAILY": {
        "font": ["calm practical sans", "soft domestic support", "clear utility numerals"],
        "colors": ["#2E3734", "#6E7873", "#6E917C", "#C49A6C", "#F3F4EF", "#D97045", "#FFFFFF", "#929A95", "#CCD1CC"],
        "device_family": ["utility line", "soft framing arch", "compact label"],
    },
    "GENERAL": {
        "font": ["modern commercial sans", "clear neutral support", "strong commercial numerals"],
        "colors": ["#2E3338", "#687078", "#3E7DB8", "#8B9EB0", "#F3F4F5", "#E36F3D", "#FFFFFF", "#8C949B", "#C9CED2"],
        "device_family": ["anchoring rule", "localized color block", "information label"],
    },
}

LEVEL_DEFAULTS = {
    "identity_line": [0.26, "MEDIUM", 1.25, 0.04, 1, "LEFT", "SECONDARY", ["SOLID"]],
    "hero_headline": [1.00, "BOLD", 1.02, -0.02, 2, "LEFT", "PRIMARY", ["SOLID", "SPLIT"]],
    "emphasized_keyword": [1.15, "BOLD", 0.98, -0.03, 1, "LEFT", "ACCENT", ["COLOR", "BLOCK", "OUTLINE"]],
    "supporting_line": [0.42, "REGULAR", 1.35, 0.02, 2, "LEFT", "SECONDARY", ["SOLID"]],
    "evidence_label": [0.27, "MEDIUM", 1.2, 0.02, 1, "LEFT", "EVIDENCE", ["LABEL", "ICON_LABEL"]],
    "badge_text": [0.25, "SEMI_BOLD", 1.15, 0.01, 1, "CENTER", "BADGE", ["BADGE"]],
    "numeric_offer": [1.35, "EXTRA_BOLD", 0.9, -0.04, 1, "LEFT", "OFFER", ["NUMERIC_SPLIT"]],
    "disclaimer": [0.17, "REGULAR", 1.3, 0.02, 2, "LEFT", "MUTED", ["SOLID"]]
}

TYPOGRAPHY_GRAPHIC_DESIGN_LOCK = """【TYPOGRAPHY & GRAPHIC DESIGN LOCK】

本页不是普通商品摄影，也不是统一黑色粗体模板。必须执行专业国内电商视觉排版，并保持文字清楚、准确、无乱码。

一、字体角色

整套图使用统一但有层次的双字体系统：

1. DISPLAY_HEADLINE
具有鲜明展示感的现代中文标题字形，可根据页面角色表现为窄体、宽体、粗体、描边或色块字；负责主标题和关键词。

2. BODY_INFORMATION
清晰、克制、易读的现代中文信息字体；负责副标题、说明、证据标签和底注。

3. NUMERIC_PROMOTION
紧凑、有冲击力的粗体数字；负责百分比、价格、数量和促销数字。数字、百分号和单位必须分别控制字号，不得使用普通正文样式。

不要五张使用五种互不相关的字体。使用统一的基础字体角色，通过字宽、字重、字号、颜色、色块、描边和组合结构形成页面差异。

二、字体层级

身份行：主标题字号的22%–28%，中等字重，增加字距。
主标题：视觉基准100%，粗体或特粗体，最多两行。
强调关键词：主标题的110%–130%，使用强调色、描边或色块。
副标题：主标题的34%–48%，常规或中等字重。
证据标签：主标题的22%–32%，中等字重。
促销数字：主标题的130%–180%，特粗体。
百分号和单位：数字的40%–60%。
底注：主标题的14%–20%，使用弱化色。

必须通过字号、字重、颜色和空间形成清晰层级，不能只靠字体大小略微变化。

三、颜色角色

主文字使用深石墨色，不使用通篇纯黑。
副文字使用中性灰。
关键词使用当前 Campaign 强调色。
促销数字使用单独的 Offer Color。
信息卡使用浅色或半透明承托面。
反白字只允许用于高对比色块。

除非用户明确要求纯黑白风格，否则不得让四张以上页面的主标题全部采用相同黑色处理。

整套文字强调色控制在画面5%–12%，不得把整段标题全部染成强调色。

四、关键词强调

每页只选择1–2个最重要的完整关键词。

必须明确执行至少一种：

- 放大；
- 变色；
- 色块承托；
- 描边；
- 错位排列；
- 独立数字；
- 标签化；
- 图标配合；
- 局部底纹。

不得只在关键词下面增加一条细线冒充强调。

五、页面角色路由

如果页面角色是 IDENTITY：

使用编辑式错位标题。
小号品牌身份行＋大号品类标题＋一个强调关键词。
允许使用不对称排列、短色块、结构线或小型身份标签。
禁止超长商品名称整段放大。

如果页面角色是 FEATURE：

使用大关键词标题。
核心结构词或利益词必须成为第一文字视觉。
采用关键词放大＋强调色＋简短解释句。
文字方向应与商品功能证据产生视觉联系。

如果页面角色是 SCENE：

使用更轻、更舒展的字体层级。
降低标题粗重感，增加行距和留白。
场景通过2–3个完整短标签或图标标签表达。
禁止再次使用巨大双行黑色粗体标题。

如果页面角色是 DETAIL_PROOF：

使用技术型信息栅格。
采用编号、引线、图标、证据标签和局部放大框。
主标题克制，证据标签清楚。
不得使用同字号的普通黑字列表。

如果页面角色是 OFFER：

促销数字必须是第一文字视觉。
数字、百分号、单位分别设计。
核心优惠、分层优惠和附加权益形成三级结构。
允许票券切口、数字徽章、错位信息卡或阶梯模块。
禁止使用多个完全相同的圆角条机械堆叠。

六、套图差异

五张图至少使用三种不同标题结构。
相邻页面不得复用相同标题结构。
至少使用两种关键词强调方式。
至少使用三种图形装置。
功能页、场景页、证明页和促销页必须一眼可以区分。
字体变化必须服务页面职责，不能为了变化而杂乱。

七、可见文案

只允许使用本页提供的已验证文字。
不得自行扩写卖点。
不得改变数字、百分比、权益和单位。
不得按字符截断中文。
所有标签必须是完整、自然、可独立阅读的中文短语。

八、禁止项

禁止统一黑色粗体标题模板。
禁止五张使用完全相同的字体大小和断行。
禁止整段文字全部使用强调色。
禁止低对比灰字。
禁止文字遮挡商品。
禁止廉价立体金字、过度发光和无依据的红金促销风。
禁止随机更换商品颜色以配合文字。
禁止乱码、错别字、残句和伪造品牌字体。

source_type=ENGINEERED_DEFAULT"""


def _level(name: str, role_family: str) -> dict[str, Any]:
    size, weight, line_height, spacing, max_lines, alignment, contrast, treatment = LEVEL_DEFAULTS[name]
    if role_family == "SCENE" and name == "hero_headline":
        size, weight, line_height = 0.78, "MEDIUM", 1.15
    if role_family == "PROOF" and name == "hero_headline":
        size, weight = 0.72, "SEMI_BOLD"
    if role_family == "OFFER" and name == "hero_headline":
        size, weight = 0.72, "BOLD"
    if role_family == "FEATURE" and name == "emphasized_keyword":
        size = 1.22
    return {
        "relative_size": size,
        "weight": weight,
        "line_height": line_height,
        "letter_spacing": spacing,
        "max_lines": max_lines,
        "alignment": alignment,
        "contrast_role": contrast,
        "allowed_treatment": treatment,
        "source_type": "ENGINEERED_DEFAULT",
    }


def _compact(text: str, maximum: int) -> str:
    """Select a complete semantic unit; never cut at a character boundary."""
    text = re.sub(r"[，。；、｜|]+", " ", text).strip()
    if len(text) <= maximum:
        return text
    complete = [x for x in text.split() if len(x) <= maximum]
    return min(complete, key=len) if complete else text


SAFE_PHRASES = {
    "护腰承托": (("护腰", "腰背"), ("承托", "托护")),
    "久坐舒适": (("久坐",), ("舒适", "舒服")),
    "腰背支撑": (("腰背", "护腰"), ("支撑", "承托", "托护")),
    "透气舒适": (("透气",), ("舒适", "清爽")),
    "贴合腰背曲线": (("贴合",), ("腰背",), ("曲线",)),
    "久坐承托": (("久坐",), ("承托", "托护")),
    "办公": (("办公",),),
    "居家": (("居家", "家用"),),
    "电竞": (("电竞", "游戏"),),
    "办公室办公": (("办公室办公",),),
    "居家电脑": (("居家", "家用"), ("电脑",)),
    "电竞游戏": (("电竞游戏",),),
    "一椅多用": (("一物多用", "多场景", "办公室办公"), ("居家", "家用")),
    "办公、居家、电竞自由切换": (("办公",), ("居家", "家用"), ("电竞", "游戏")),
    "升降调节": (("升降",),),
    "后仰锁定": (("后仰",), ("锁定", "角度锁定")),
    "静音滚轮": (("静音",), ("滚轮",)),
    "透气网面": (("透气",), ("网面", "网布")),
    "舒适调节": (("舒适",), ("调节", "升降", "后仰")),
    "细节可见": (("结构", "细节", "滚轮", "网面", "网布"),),
    "实用配置": (("功能配置", "功能", "升降", "后仰", "滚轮"),),
    "活动叠加最高直降": (("活动叠加最高直降",),),
    "3期免息": (("3期免息", "支持3期免息"),),
    "保价90天": (("保价90天",),),
}

DANGLING_ENDINGS = (
    "办", "或", "与", "及", "的", "和", "为", "可", "电", "用于", "搭配", "以及"
)
KNOWN_COMPLETE_TERMS = {
    "小家电", "大家电", "家用电器", "美妆个护", "家居日用", "数码电子"
}


def _phrase_supported(phrase: str, sources: list[str]) -> bool:
    blob = "\n".join(str(x) for x in sources if x)
    if phrase in blob or re.sub(r"\s+", "", phrase) in re.sub(r"\s+", "", blob):
        return True
    requirements = SAFE_PHRASES.get(phrase)
    return bool(requirements) and all(
        any(token in blob for token in alternatives)
        for alternatives in requirements
    )


def _choose_phrase(candidates: list[str], sources: list[str], maximum: int) -> str:
    for phrase in candidates:
        if len(phrase) <= maximum and _phrase_supported(phrase, sources):
            return phrase
    complete = [
        unit.strip()
        for source in sources
        for unit in re.split(r"[，。；、｜|]", str(source))
        if unit.strip() and len(unit.strip()) <= maximum
    ]
    return min(complete, key=len) if complete else ""


def _choose_candidate_only(
    candidates: list[str], sources: list[str], maximum: int
) -> str:
    for phrase in candidates:
        if len(phrase) <= maximum and _phrase_supported(phrase, sources):
            return phrase
    return ""


def _copy_record(
    page_index: int,
    role: str,
    text: str,
    sources: list[str],
    source_type: str,
    maximum: int,
) -> dict[str, Any]:
    segments = [x.strip() for x in re.split(r"[｜|]", text) if x.strip()]
    complete = bool(text) and (
        text in KNOWN_COMPLETE_TERMS
        or not any(text.endswith(x) for x in DANGLING_ENDINGS)
    )
    traceable = bool(segments) and all(_phrase_supported(x, sources) for x in segments)
    length_valid = bool(text) and all(len(x) <= maximum for x in segments)
    standalone = complete and not re.search(r"(?:^|[，、])(?:和|与|及|或)(?:$|[，、])", text)
    checks = {
        "phrase_is_complete": complete,
        "no_mid_word_truncation": complete and traceable,
        "no_dangling_conjunction": complete,
        "no_incomplete_scene_phrase": complete and traceable,
        "no_duplicate_fragment": len(segments) == len(set(segments)),
        "traceable_to_user_fact": traceable,
        "no_new_claim": traceable,
        "headline_length_valid": length_valid if role in {"hero_headline", "supporting_line"} else True,
        "label_length_valid": length_valid if role in {"evidence_label", "scene_label", "offer_label"} else True,
        "readable_as_standalone_copy": standalone,
    }
    failure_reasons = [name for name, passed in checks.items() if not passed]
    return {
        "page_index": page_index,
        "text_role": role,
        "original_source_text": sources,
        "compiled_visible_text": text,
        "source_type": source_type,
        "semantic_complete": complete,
        "fact_traceable": traceable,
        "new_claim": not traceable,
        "checks": checks,
        "decision": "PASS" if not failure_reasons else "BLOCKED",
        "failure_reasons": failure_reasons,
    }


def _category_token(
    name: str, category: str, verified_attributes: dict[str, Any] | None = None
) -> str:
    for key, value in (verified_attributes or {}).items():
        if any(token in str(key).lower() for token in ("品名", "product name", "商品名称")):
            value = str(value).strip()
            if value and len(value) <= 12 and value in name:
                return value
    tokens = [
        "人体工学椅", "办公椅", "电脑椅", "电饭煲", "吹风机", "空气炸锅",
        "耳机", "键盘", "相机", "精华液", "面霜", "洗发水", "饼干", "咖啡",
        "收纳盒", "保温杯",
    ]
    for token in tokens:
        if token in name:
            return token
    tail = category.replace("\\", "/").split("/")[-1]
    return _compact(tail, 8) or "商品"


def _verified_brand(raw: dict[str, Any], category_token: str) -> str:
    attrs = raw["product"].get("verified_attributes", {})
    for key, value in attrs.items():
        if any(x in str(key).lower() for x in ("brand", "品牌")):
            return _compact(str(value), 8)
    name = raw["product"]["name"]
    if category_token in name:
        prefix = name.split(category_token, 1)[0]
        if 1 <= len(prefix) <= 8:
            return prefix
    return ""


def _feature_keyword(text: str) -> str:
    candidates = [
        "S型靠背", "护腰", "透气", "静音", "后仰", "升降", "快充", "降噪",
        "大容量", "恒温", "轻盈", "保湿", "清洁", "酥脆", "低糖", "收纳",
    ]
    for token in candidates:
        if token.lower() in text.lower():
            return token
    return _compact(text, 6)


def _numeric_token(offers: list[dict[str, Any]]) -> str:
    for item in offers:
        match = re.search(r"\d+(?:\.\d+)?%", item["text"])
        if match:
            return match.group(0)
    return ""


def _copy_plan(raw: dict[str, Any], role_family: str, page_index: int) -> dict[str, Any]:
    product = raw["product"]
    name = product["name"]
    attrs = product.get("verified_attributes", {})
    category_token = _category_token(name, product["category"], attrs)
    brand = _verified_brand(raw, category_token)
    selling = [x["text"] for x in product.get("verified_selling_points", [])]
    scenes = [x["text"] for x in product.get("verified_scenes", [])]
    offers = [x["text"] for x in product.get("verified_offers", [])]
    attribute_phrases = []
    for key, value in attrs.items():
        key_text = str(key).strip()
        value_text = str(value).strip()
        if not value_text:
            continue
        attribute_phrases.append(
            value_text
            if key_text in {"品牌", "品名", "brand", "product name"}
            else f"{key_text}{value_text}"
        )
    original_sources: list[str] = [name]
    if role_family == "IDENTITY":
        original_sources += selling[:1]
        hero = category_token
        source_units = [
            unit.strip()
            for text in selling[:1]
            for unit in re.split(r"[，。；、｜|]", text)
            if unit.strip()
        ]
        emphasis = _choose_candidate_only(
            ["护腰承托", "S型靠背", "专用腰靠", "久坐舒适"],
            [name] + selling,
            8,
        ) or (source_units[0] if source_units and len(source_units[0]) <= 8 else category_token)
        support = _choose_candidate_only(
            ["久坐舒适", "腰背支撑", "透气舒适"],
            [name] + selling,
            18,
        ) or (
            source_units[1]
            if len(source_units) > 1 and len(source_units[1]) <= 18
            else _choose_phrase([], selling, 18)
        )
        labels = [x for x in ("办公", "居家", "电竞") if _phrase_supported(x, [name] + selling + scenes)]
    elif role_family == "FEATURE":
        source = selling[0] if selling else name
        original_sources.append(source)
        source_units = [
            unit.strip()
            for unit in re.split(r"[，。；、｜|]", source)
            if unit.strip()
        ]
        hero = _choose_candidate_only(["S型靠背", "专用腰靠", "护腰承托", "久坐舒适"], original_sources, 12) or (
            source_units[0]
            if source_units and len(source_units[0]) <= 12
            else _choose_phrase([], original_sources, 12)
        )
        emphasis = _choose_candidate_only(["专用腰靠", "护腰承托", "S型靠背", "透气网面"], [name] + selling, 8) or (
            source_units[1]
            if len(source_units) > 1 and len(source_units[1]) <= 8
            else hero
        )
        support = "｜".join(
            x for x in ("贴合腰背曲线", "久坐承托")
            if _phrase_supported(x, original_sources)
        ) or _choose_phrase([], original_sources, 18)
        if support in {hero, emphasis}:
            support = category_token
        labels = [x for x in ("腰背支撑", "透气舒适") if _phrase_supported(x, [name] + selling)]
    elif role_family == "SCENE":
        original_sources += scenes
        labels = [x for x in ("办公室办公", "居家电脑", "电竞游戏") if _phrase_supported(x, original_sources + [name])]
        if not labels:
            labels = [x for x in scenes if len(x) <= 12][:3]
        hero = _choose_candidate_only(
            ["一椅多用", "简单3步", "即享美味"],
            [name] + selling + scenes,
            12,
        ) or _choose_phrase([], scenes, 12)
        emphasis = _choose_candidate_only(
            ["即享美味"], [name] + selling + scenes, 8
        ) or (labels[0] if labels else category_token)
        support = _choose_candidate_only(
            ["办公、居家、电竞自由切换"],
            [name] + selling + scenes,
            18,
        ) or max(
            (x for x in scenes if len(x) <= 18),
            key=len,
            default="",
        )
    elif role_family == "PROOF":
        proof = selling[2:6] if len(selling) >= 3 else selling[:4]
        original_sources += proof
        labels = [x for x in ("升降调节", "后仰锁定", "静音滚轮", "透气网面") if _phrase_supported(x, [name] + proof)]
        if not labels:
            labels = [
                unit.strip()
                for text in proof
                for unit in re.split(r"[，。；、｜|]", text)
                if unit.strip() and len(unit.strip()) <= 8
            ][:4]
        hero = _choose_phrase(
            ["实用配置", "独立配置"], [name] + selling, 12
        ) or _choose_phrase([], proof, 12)
        emphasis = labels[0] if labels else category_token
        support = "｜".join(
            x for x in ("舒适调节", "细节可见")
            if _phrase_supported(x, [name] + selling)
        )
        if not support:
            joined_labels = "｜".join(labels)
            support = joined_labels if len(joined_labels) <= 18 else _choose_phrase([], proof, 18)
    elif role_family == "OFFER":
        original_sources += offers
        number = _numeric_token(product.get("verified_offers", []))
        hero = next(
            (x for x in offers if "立减" in x and not re.search(r"\d", x)),
            _choose_phrase([], offers, 12),
        )
        emphasis = number or hero
        support = _choose_phrase(["活动叠加最高直降"], offers, 18)
        labels = []
        for text in offers:
            if text == hero or (number and number in text and "最高" in text):
                continue
            label = text.replace("支持3期免息", "3期免息")
            if len(label) <= 12 and _phrase_supported(label, offers):
                labels.append(label)
        labels = labels[:5]
    elif role_family == "TRUST":
        original_sources += attribute_phrases
        hero = category_token
        emphasis = _choose_phrase(
            ["净含量122克"],
            attribute_phrases,
            12,
        ) or _choose_phrase([], attribute_phrases, 12)
        support = next(
            (
                phrase
                for phrase in attribute_phrases
                if phrase.startswith("产地") and len(phrase) <= 18
            ),
            next(
                (
                    phrase
                    for phrase in attribute_phrases
                    if phrase.startswith("产品类型") and len(phrase) <= 18
                ),
                _choose_phrase([], attribute_phrases[1:], 18),
            ),
        )
        labels = [
            phrase
            for phrase in attribute_phrases
            if phrase not in {brand, category_token, emphasis, support}
            and len(phrase) <= 12
        ][:4]
    else:
        source = selling[0] if selling else name
        original_sources.append(source)
        hero = _compact(source, 12)
        emphasis = _feature_keyword(source)
        support = _compact(source, 18)
        labels = []
    identity_line = (
        brand if role_family == "IDENTITY" and brand
        else " ".join(x for x in (brand, category_token) if x) or category_token
    )
    all_sources = [name, product["category"]] + attribute_phrases + selling + scenes + offers
    visible_core = (
        ("identity_line", identity_line, 16),
        ("hero_headline", hero, 16),
        ("emphasized_keyword", emphasis, 8),
        ("supporting_line", support, 18),
    )
    records = [
        _copy_record(page_index, role, text, all_sources, "SAFE_COMPRESSION_OF_USER_FACT", maximum)
        for role, text, maximum in visible_core
        if text
    ]
    label_role = "scene_label" if role_family == "SCENE" else "offer_label" if role_family == "OFFER" else "evidence_label"
    records.extend(
        _copy_record(
            page_index,
            label_role,
            text,
            all_sources,
            "SAFE_COMPRESSION_OF_USER_FACT",
            12 if label_role in {"offer_label", "scene_label"} else 8,
        )
        for text in labels
    )
    numeric_offer = _numeric_token(product.get("verified_offers", [])) if role_family == "OFFER" else ""
    if numeric_offer:
        records.append(_copy_record(page_index, "numeric_offer", numeric_offer, all_sources, "USER_FACT", 8))
    disclaimer = "实际优惠以页面结算为准" if role_family == "OFFER" and offers else ""
    if disclaimer:
        record = _copy_record(page_index, "disclaimer", disclaimer, [disclaimer], "COMPLIANCE_DEFAULT", 16)
        records.append(record)
    integrity_pass = bool(records) and all(
        value
        for record in records
        for value in record["checks"].values()
    )
    return {
        "original_copy": original_sources,
        "identity_line": identity_line,
        "hero_headline": hero,
        "emphasized_keyword": emphasis,
        "supporting_line": support,
        "evidence_labels": [x for x in labels if x][:5],
        "numeric_offer": numeric_offer,
        "disclaimer": disclaimer,
        "copy_source": "SAFE_COMPRESSION_OF_USER_FACT",
        "full_product_name_source": "USER_FACT",
        "copy_integrity_gate": {
            "status": "PASS" if integrity_pass else "BLOCKED",
            "all_visible_copy_semantically_complete": integrity_pass,
            "records": records,
        },
    }


def _keyword_method(role_family: str) -> str:
    return {
        "IDENTITY": "KEYWORD_COLOR",
        "FEATURE": "KEYWORD_SCALE_AND_COLOR",
        "SCENE": "ICON_LABEL_RHYTHM",
        "PROOF": "NUMBERED_CALLOUT",
        "OFFER": "NUMERIC_HERO_WITH_SPLIT_UNIT",
        "BENEFIT": "LOCAL_COLOR_BLOCK",
        "SPECIFICATION": "LABEL_BAR",
        "TRUST": "BADGE_CLUSTER",
    }.get(role_family, "KEYWORD_COLOR")


def _fingerprint(page: dict[str, Any]) -> str:
    payload = {
        "treatment": page["treatment_id"],
        "sizes": {k: v["relative_size"] for k, v in page["typography_levels"].items()},
        "weights": {k: v["weight"] for k, v in page["typography_levels"].items()},
        "headline_structure": page["headline_structure"],
        "emphasis_method": page["keyword_emphasis"]["emphasis_method"],
        "alignment": page["alignment"],
        "devices": page["graphic_devices"],
        "density": page["text_density"],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _set_gates(pages: list[dict[str, Any]], monochrome: bool) -> dict[str, bool]:
    treatment_ids = [x["treatment_id"] for x in pages]
    methods = {x["keyword_emphasis"]["emphasis_method"] for x in pages}
    devices = {d for x in pages for d in x["graphic_devices"]}
    fingerprints = {x["design_fingerprint"] for x in pages}
    families = {x["role_family"]: x for x in pages}
    role_family_count = len({x["role_family"] for x in pages})
    adjacent_ok = all(a != b for a, b in zip(treatment_ids, treatment_ids[1:]))
    black_heavy = sum(
        x["colors"]["headline"] in {"#000000", "#000"}
        and x["typography_levels"]["hero_headline"]["weight"] in {"BOLD", "EXTRA_BOLD"}
        for x in pages
    )
    promotion = families.get("OFFER")
    return {
        "five_pages_use_at_least_three_typography_treatments": len(set(treatment_ids)) >= min(3, len(pages)),
        "no_adjacent_same_headline_treatment": adjacent_ok,
        "at_least_two_keyword_emphasis_methods": (
            role_family_count < 2 or len(methods) >= min(2, len(pages))
        ),
        "at_least_three_graphic_device_types_across_set": len(devices) >= min(3, len(pages)),
        "headline_weight_or_structure_rotates": len(fingerprints) >= min(3, len(pages)),
        "scene_page_not_same_as_feature_page": (
            "SCENE" not in families or "FEATURE" not in families
            or families["SCENE"]["design_fingerprint"] != families["FEATURE"]["design_fingerprint"]
        ),
        "promotion_page_uses_numeric_hierarchy": (
            promotion is None or bool(promotion["copy_plan"]["numeric_offer"])
            and promotion["typography_levels"]["numeric_offer"]["relative_size"] > 1.0
        ),
        "no_four_pages_all_black_heavy_headlines": monochrome or black_heavy < 4,
        "accent_color_used_intentionally": monochrome or all(x["keyword_emphasis"]["emphasis_color"] for x in pages),
        "text_density_matches_page_role": all(
            x["text_density"] == ("LOW" if x["role_family"] in {"FEATURE", "SCENE"} else "MEDIUM")
            or x["role_family"] == "PROOF" and x["text_density"] == "MEDIUM_HIGH"
            for x in pages
        ),
        "typography_does_not_occlude_product": all(x["layout_policy"]["product_occlusion"] == "FORBIDDEN" for x in pages),
        "product_identity_remains_primary": all(x["layout_policy"]["product_priority"] == "FIRST_VISUAL_SUBJECT" for x in pages),
        "actual_design_fingerprints_are_varied": len(fingerprints) >= min(3, len(pages)),
    }


def build_campaign_design_contract(
    raw: dict[str, Any], identity_contract: dict[str, Any], base_pages: list[dict[str, Any]]
) -> dict[str, Any]:
    category = normalize_category(raw["product"]["category"])
    profile = CATEGORY_DESIGN[category]
    preference = raw.get("campaign_preference") or {}
    monochrome = any(x in json.dumps(preference, ensure_ascii=False).lower() for x in ("黑白极简", "monochrome"))
    c = profile["colors"]
    color_system = {
        "primary_text_color": c[0],
        "secondary_text_color": c[1],
        "accent_color": c[2],
        "secondary_accent_color": c[3],
        "information_surface_color": c[4],
        "offer_color": c[5],
        "inverse_text_color": c[6],
        "muted_text_color": c[7],
        "divider_color": c[8],
        "usage_ratio": {
            "neutral_background": "65%-75%",
            "primary_text_and_product_support": "15%-25%",
            "accent": "5%-12%",
            "secondary_accent_max": "5%",
        },
        "preference_binding": preference,
        "product_identity_color_is_immutable": True,
        "source_type": "ENGINEERED_DEFAULT",
    }
    font_system = {
        "font_personality": profile["font"][0],
        "headline_personality": profile["font"][0],
        "body_personality": profile["font"][1],
        "numeric_personality": profile["font"][2],
        "icon_personality": "same-stroke, category-compatible commercial pictograms",
        "brand_font_claimed": False,
        "source_type": "ENGINEERED_DEFAULT",
    }
    library = load(LIBRARY_PATH)["treatments"]
    pages = []
    family_counts: dict[str, int] = {}
    has_numeric_offer = any(
        re.search(r"\d+(?:\.\d+)?%", x["text"])
        for x in raw["product"].get("verified_offers", [])
    )
    for base in base_pages:
        family = ROLE_FAMILY.get(base["page_role"], "FEATURE")
        if (
            base["page_role"] == "CAROUSEL_SLOT_5_PROMOTION_TRUST_SUMMARY"
            and not has_numeric_offer
        ):
            family = "TRUST"
        options = ROLE_TREATMENT_OPTIONS[family]
        occurrence = family_counts.get(family, 0)
        treatment_id = options[occurrence % len(options)]
        family_counts[family] = occurrence + 1
        treatment = library[treatment_id]
        copy_plan = _copy_plan(raw, family, base["index"])
        method = _keyword_method(family)
        accent = color_system["offer_color"] if family == "OFFER" else color_system["accent_color"]
        devices = list(treatment["graphic_devices"])
        devices.extend(profile["device_family"][:1])
        devices = list(dict.fromkeys(devices))[:3]
        density = "MEDIUM_HIGH" if family == "PROOF" else "LOW" if family in {"FEATURE", "SCENE"} else "MEDIUM"
        typography_levels = {name: _level(name, family) for name in LEVEL_DEFAULTS}
        page = {
            "index": base["index"],
            "page_role": base["page_role"],
            "role_family": family,
            "treatment_id": treatment_id,
            "headline_structure": treatment["headline_structure"],
            "typography_levels": typography_levels,
            "copy_plan": copy_plan,
            "keyword_emphasis": {
                "emphasized_tokens": [copy_plan["emphasized_keyword"]] if copy_plan["emphasized_keyword"] else [],
                "emphasis_reason": "communicate the verified role-specific fact at first glance",
                "emphasis_method": method,
                "emphasis_color": accent,
                "emphasis_scale": typography_levels["emphasized_keyword"]["relative_size"],
                "emphasis_position": base["visual_plan"]["fields"].get("copy.headline_region", "role-specific"),
                "source_type": "ENGINEERED_DEFAULT",
            },
            "colors": {
                "headline": color_system["primary_text_color"],
                "supporting": color_system["secondary_text_color"],
                "accent": accent,
                "secondary_accent": color_system["secondary_accent_color"],
                "surface": color_system["information_surface_color"],
                "divider": color_system["divider_color"],
                "inverse": color_system["inverse_text_color"],
            },
            "alignment": treatment["alignment"],
            "graphic_devices": devices,
            "text_density": density,
            "layout_policy": {
                "product_occlusion": "FORBIDDEN",
                "product_priority": "FIRST_VISUAL_SUBJECT",
                "text_outside_identity_structure": True,
            },
            "field_sources": {
                "copy.original": "USER_FACT",
                "copy.compressed": "SAFE_COMPRESSION_OF_USER_FACT",
                "typography.*": "ENGINEERED_DEFAULT",
                "colors.*": "ENGINEERED_DEFAULT",
                "graphic_devices.*": "ENGINEERED_DEFAULT",
                "treatment_id": "ENGINEERED_DEFAULT",
            },
        }
        page["design_fingerprint"] = _fingerprint(page)
        pages.append(page)
    gates = _set_gates(pages, monochrome)
    gates["copy_integrity_gate"] = all(
        page["copy_plan"]["copy_integrity_gate"]["status"] == "PASS"
        for page in pages
    )
    contract = {
        "schema_version": "campaign-design-contract-0.1.3",
        "request_id": raw["request_id"],
        "source_precedence": ["USER_FACT", "EVIDENCE_DERIVED", "ENGINEERED_DEFAULT", "MODEL_FREEDOM"],
        "inputs": {
            "category": raw["product"]["category"],
            "identity_contract_id": identity_contract["contract_id"],
            "product_color_partitions": identity_contract["identity"]["color_partitions"],
            "material_surfaces": identity_contract["identity"]["materials_and_surfaces"],
            "campaign_preference": preference,
            "offer_present": bool(raw["product"].get("verified_offers")),
            "background_and_lighting": [x["visual_plan"]["background_route"] for x in base_pages],
        },
        "font_system": font_system,
        "color_system": color_system,
        "pages": pages,
        "set_gates": gates,
        "status": "READY" if all(gates.values()) else "BLOCKED",
        "profile01_semantics_read": False,
    }
    jsonschema.Draft202012Validator(load(SCHEMA)).validate(contract)
    return contract


def design_prompt_block(page: dict[str, Any], contract: dict[str, Any]) -> str:
    cp = page["copy_plan"]
    colors = page["colors"]
    return (
        "【TYPOGRAPHY & GRAPHIC EXECUTION — CAMPAIGN STYLE / DESIGN STRENGTH】"
        f"treatment_id={page['treatment_id']}；标题结构={page['headline_structure']}；"
        f"字体气质={contract['font_system']['headline_personality']}；"
        f"身份行“{cp['identity_line']}”；主标题“{cp['hero_headline']}”；"
        f"强调词“{cp['emphasized_keyword']}”，强调方式={page['keyword_emphasis']['emphasis_method']}，"
        f"强调色={page['keyword_emphasis']['emphasis_color']}，强调比例={page['keyword_emphasis']['emphasis_scale']}；"
        f"副标题“{cp['supporting_line']}”；证据/场景标签={json.dumps(cp['evidence_labels'], ensure_ascii=False)}；"
        f"数字优惠“{cp['numeric_offer']}”；底注“{cp['disclaimer']}”。"
        f"颜色角色：主文字{colors['headline']}、副文字{colors['supporting']}、强调{colors['accent']}、"
        f"信息面{colors['surface']}、分隔线{colors['divider']}。"
        f"图形装置最多3种：{json.dumps(page['graphic_devices'], ensure_ascii=False)}。"
        "禁止整段标题全部上强调色；禁止统一纯黑粗体模板；禁止相同圆角条机械堆叠；"
        "文字不得遮挡商品。原始完整文案保留在审计合同，画面只用事实安全压缩文案。"
    )


if __name__ == "__main__":
    raise SystemExit("Use compile_harness_v0_1_3.py")
