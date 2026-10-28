# ruff: noqa: E501
"""Opt-in generation-side shadow transforms for Aurora v2.

The production snapshot and Harness remain authoritative.  This module only
derives an ephemeral provider request when an internal caller explicitly asks
for one of the development shadow modes; it never mutates the stored snapshot.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

CONTRACT_FIRST_REBUILD_V1 = "contract_first_rebuild_v1"
EXECUTION_SEMANTICS_V1 = "execution_semantics_v1"

_LAYOUT_DIRECTIVES = {
    "ASYMMETRIC_SEARCH_FRAME": (
        "搜索封面允许商品与短标题形成不对称搜索框架；信息模块只保留一个主闭合，"
        "不要把后续页的空间骨架复制到这里。"
    ),
    "EDITORIAL_IDENTITY_DIAGONAL": (
        "这是身份页，不是搜索封面：使用不同的三分之四或侧向身份镜头，商品偏左或居中，"
        "用一个真实儿童/亲子关系和稀疏文案建立身份；禁止底部等权卡片、圆形细节网格和多模块拼图。"
    ),
    "DOUBLE_SCALE_PROOF": (
        "这是功能证明页：完整商品与一个真实局部形成明显大小双尺度，局部解释本页功能；"
        "禁止单一英雄车加通用底栏。"
    ),
    "ACTION_DIAGONAL_SCENE": (
        "这是场景操作页：必须出现儿童驾驶或家长与遥控器的真实接触关系，形成清晰动作斜线；"
        "商品处于使用中，禁止静态孤立英雄、底部等权卡片和细节模块目录。"
    ),
    "DETAIL_CALLOUT_FIELD": (
        "这是细节信任页：以一个真实微距/结构细节为主证据，并保留有上下文的完整关系；"
        "禁止重复搜索封面骨架。"
    ),
    "CLOSURE_SPEC_ARCH": (
        "这是规格收口页：完整商品与有主次的规格/服务闭合形成收口；"
        "禁止复制身份页或功能证明页的主镜头。"
    ),
}

_EVIDENCE_DIRECTIVES = {
    "DETAIL_CALLOUT_FIELD": (
        "把一个可从参考图核验的真实结构或部件做成画面第一视觉；"
        "完整商品只作尺度和身份上下文。遮住文字后仍要先看到该真实细节，"
        "不能用标题、箭头、图标或装饰替代细节证据。"
    ),
    "CLOSURE_SPEC_ARCH": (
        "把遥控器与手部接触、车辆响应或其他可从参考图核验的实际操作关系"
        "放在商品主画面中，并让它成为第一或并列第一视觉；右侧/底部规格只能做收口，"
        "不能让规格文字代替操作证据。"
    ),
    "ACTION_DIAGONAL_SCENE": (
        "把儿童驾驶或家长手持遥控器的真实接触关系做成第一视觉；"
        "动作证据必须大而清楚，不能只用标题或信号图形表示。"
    ),
}


# The v1 contract-first prompt moved page facts ahead of shared style, but it
# still left the image model free to resolve every page as a polished hero
# image. This second opt-in mode makes the spatial decision explicit before
# any copy or finish instructions are introduced. The values are deliberately
# phrased as observable geometry and anti-repetition constraints: they can be
# checked against pixels after generation and do not invent product facts.
_EXECUTION_LAYOUT_CONTRACTS = {
    "ASYMMETRIC_SEARCH_FRAME": {
        "geometry": (
            "商品占画面约 65%-78%，低机位三分之四主体偏置；标题只占一个短区块，"
            "商品与标题形成搜索缩略图的单一阅读闭环。"
        ),
        "evidence": "商品轮廓、车轮、前脸和可核验卖点必须一眼可见。",
        "forbidden": "禁止圆形细节窗、底部等权卡片、人物关系场景和重复遥控器拼贴。",
        "delta": "本页只承担搜索识别，不承担身份、操作或规格收口。",
    },
    "EDITORIAL_IDENTITY_DIAGONAL": {
        "geometry": (
            "使用侧向或更高机位的对角关系；商品与儿童/亲子关系占主画面，"
            "文字置于留白角落，不使用搜索页的低机位正面骨架。"
        ),
        "evidence": "真实人物关系和商品身份必须同时清楚，人物不能只是背景装饰。",
        "forbidden": "禁止遥控器手部特写、圆形细节窗、底部全宽信息带和搜索封面镜头。",
        "delta": "本页只回答商品是谁、适合谁，不重复操作证据。",
    },
    "DOUBLE_SCALE_PROOF": {
        "geometry": (
            "采用明显双尺度：一个占画面至少 35%-45% 的真实结构/部件近景，"
            "配一个缩小但完整可辨的商品上下文；近景与整车不能处于同一等权层级。"
        ),
        "evidence": "近景必须来自参考图可核验的座椅、轮胎、灯组或其他结构。",
        "forbidden": "禁止单一整车英雄图、通用底栏、重复身份合影和仅靠标题宣称卖点。",
        "delta": "本页必须与搜索页和身份页形成尺度差，先看功能证据再看整车。",
    },
    "ACTION_DIAGONAL_SCENE": {
        "geometry": (
            "让儿童驾驶或家长手持遥控器的接触动作形成贯穿画面的斜线；"
            "人物、商品和控制关系占主画面，商品不能摆成无人静态展品。"
        ),
        "evidence": "手、遥控器、儿童姿态和车辆之间的真实接触关系必须足够大而清楚。",
        "forbidden": "禁止静态孤立英雄车、底部等权卡片、仅用无线信号图标表示操作。",
        "delta": "本页只回答如何使用，不复制身份页的亲子合影构图。",
    },
    "DETAIL_CALLOUT_FIELD": {
        "geometry": (
            "微距/结构细节占画面至少 45%，完整商品只保留为边缘或后景尺度参照；"
            "细节区域使用不对称裁切，不重复搜索页的完整三分之四车身。"
        ),
        "evidence": "遮住文字后仍应先看到真实仪表、材质、连接或结构细节。",
        "forbidden": "禁止整车继续占第一视觉、重复遥控器底栏和装饰性放大镜。",
        "delta": "本页只回答细节是否可信，不能再承担身份或操作收口。",
    },
    "CLOSURE_SPEC_ARCH": {
        "geometry": (
            "采用侧向/俯视规格收口构图：商品与规格/兼容/服务信息形成有主次的双区，"
            "信息区不做底部复制条，主商品镜头与前五页明显不同。"
        ),
        "evidence": "只呈现配置中已有的规格、兼容或服务事实；每项事实绑定可见商品证据。",
        "forbidden": "禁止重复搜索标题、重复操作遥控器、圆形细节窗和六页通用底栏。",
        "delta": "本页必须完成规格或服务收口；缺少事实时减少文字，不用虚构卖点填空。",
    },
}

# The Skill does not ban every information module. Search covers may use zero
# to five unequal verified modules and closure pages may use a specification
# cluster. The failure signature is the catalog-like equal-weight wall that
# erases page duty, not the mere presence of a lower information area.
_COMPOSITION_GATE_DIRECTIVES = {
    "ASYMMETRIC_SEARCH_FRAME": (
        "Search-cover commercial frame: allow 0-5 unequal verified modules and a "
        "10%-16% closure area, or 16%-24% when a complete verified offer exists. Preserve a "
        "dominant complete product, 2-3 scan stops, and at least four type roles. "
        "Reject only a catalog-like equal-weight card wall, repeated generic bottom "
        "strip, or modules that invent facts."
    ),
    "EDITORIAL_IDENTITY_DIAGONAL": (
        "Identity hero: keep copy sparse and let one real person/product relation "
        "carry the page. Do not use the search cover's commercial bottom strip; a "
        "small unequal label is allowed when it supports the identity claim."
    ),
    "DOUBLE_SCALE_PROOF": (
        "Core proof: a real macro/full-product double scale is required. A single "
        "supporting inset or label is allowed, but no equal-weight grid may compete "
        "with the dominant proof object."
    ),
    "ACTION_DIAGONAL_SCENE": (
        "Scene/operation: the contact action is the primary evidence. Small action- "
        "bound labels are allowed; do not replace the contact relation with a generic "
        "bottom card row or icon directory."
    ),
    "DETAIL_CALLOUT_FIELD": (
        "Detail/trust: use one macro-dominant callout field with a few unequal, "
        "evidence-bound annotations. A real callout or inset is allowed when it "
        "points to visible structure; reject only decorative or equal-card catalog "
        "treatment."
    ),
    "CLOSURE_SPEC_ARCH": (
        "Offer/spec closure: allow an unequal numeric/specification/service cluster "
        "on the side or lower area when every item is authorized. Keep the product "
        "and one closure conclusion dominant; reject equal-weight catalog grids or "
        "invented specifications."
    ),
}


def prepare_shadow_generation_request(
    generation_request: Mapping[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    """Return an ephemeral provider request for the opt-in shadow mode.

    The original request is copied so immutable snapshots, Harness state and
    audit artifacts retain the production request.  Automatic composition
    repairs deliberately rebuild from product references instead of sending
    the failed image back as a repair canvas.
    """

    if mode not in {CONTRACT_FIRST_REBUILD_V1, EXECUTION_SEMANTICS_V1}:
        raise ValueError(f"Unsupported Aurora generation shadow mode: {mode}")
    prepared = deepcopy(dict(generation_request))
    prepared["prompt"] = (
        build_execution_semantics_prompt(prepared)
        if mode == EXECUTION_SEMANTICS_V1
        else build_contract_first_prompt(prepared)
    )
    prepared["aurora_shadow_mode"] = mode

    if str(prepared.get("generation_origin") or "") == "AUTOMATIC_REPAIR":
        repair = _mapping(prepared.get("repair"))
        if not repair:
            repair = _mapping(_mapping(prepared.get("page_contract")).get("repair"))
        scope = str(repair.get("scope") or "").strip().upper()
        if scope in {
            "LAYOUT_OR_COMPOSITION",
            "COMPOSITION",
            "IDENTITY",
            "PRODUCT_IDENTITY",
            "IDENTITY_MISMATCH",
            "IDENTITY_DRIFT",
            "PRODUCT_IDENTITY_MISMATCH",
        }:
            prepared["aurora_shadow_repair_scope"] = (
                "REBUILD_FROM_PRODUCT_EVIDENCE"
            )
            prepared["edit_target"] = None
            prepared["repair_canvas"] = None
            allowlist = prepared.get("reference_allowlist")
            identity_paths = [
                str(item.get("path") or "").strip()
                for item in allowlist
                if isinstance(item, Mapping) and str(item.get("path") or "").strip()
            ] if isinstance(allowlist, list) else []
            if identity_paths:
                prepared["imagegen_reference_paths"] = list(
                    dict.fromkeys(identity_paths)
                )
    return prepared


def build_contract_first_prompt(generation_request: Mapping[str, Any]) -> str:
    """Build a concise, page-first prompt without changing source facts."""

    page = _mapping(generation_request.get("page_contract"))
    layout = _mapping(page.get("layout_contract"))
    identity = _mapping(_mapping(generation_request.get("campaign_constitution")).get("product_identity"))
    style = _mapping(_mapping(generation_request.get("campaign_constitution")).get("campaign_style"))
    archetype = str(layout.get("composition_archetype") or "").strip()

    lines = [
        "【AURORA SHADOW contract-first rebuild v1】",
        "只生成当前页；当前页职责优先于共享视觉风格。",
        f"页码={page.get('index', generation_request.get('page_index'))}；页面角色={page.get('page_role', generation_request.get('page_role'))}。",
        "【商品身份硬锁】" + _join(identity.get("identity_summary")),
        "必须保留=" + _join(identity.get("must_preserve")) + "。",
        "绝不改变=" + _join(identity.get("must_not_change")) + "。",
        "【本页购买任务】" + _join(page.get("buyer_question")),
        "【本页主卖点】" + _join(page.get("primary_claim")),
        "【本页视觉证明】" + _join(page.get("visual_proof")),
        "【主构图职责】" + _join(page.get("composition")),
        f"【本页构图原型】{archetype}：{_LAYOUT_DIRECTIVES.get(archetype, _join(layout.get('composition_instruction')))}",
        "【本页商品区/文字区/主镜头/证据拓扑】"
        f"{layout.get('product_zone', '按本页职责')} / {layout.get('copy_zone', '按本页职责')} / "
        f"{layout.get('dominant_view', '按本页职责')} / {layout.get('evidence_topology', '按本页职责')}。",
        "【真实证据优先】"
        + _EVIDENCE_DIRECTIVES.get(
            archetype,
            "本页主卖点对应的可见物理证据必须成为第一或并列第一视觉；"
            "标题、箭头、图标和装饰不得替代证据。",
        ),
        "【共享视觉 DNA 仅控制 finish】"
        f"摄影质感={style.get('photography_finish', '高锐度商业广告质感')}；"
        f"光线={style.get('light_direction', '自然日光')}；色温={style.get('color_temperature', '中性偏暖')}。"
        "不得从共享 finish 继承上一页的镜头、空间骨架、卡片布局或阅读路径。",
        "先完成一个强主画面，再按当前页面契约放置信息；搜索封面和规格收口页可以使用不等权 verified modules，但不得把资料排成等权目录网格。",
        "只使用参考图中可核验的商品身份、结构和事实；输出 1:1、至少 1024×1024、中文可读。",
    ]
    return "\n".join(line for line in lines if line.strip())



def build_execution_semantics_prompt(generation_request: Mapping[str, Any]) -> str:
    """Build the stricter opt-in prompt used to test page composition semantics.

    This is a separate builder instead of changing v1 in place: existing jobs
    remain reproducible while the new mode can be compared with the old
    generation boundary under the same provider and model.
    """

    page = _mapping(generation_request.get("page_contract"))
    layout = _mapping(page.get("layout_contract"))
    constitution = _mapping(generation_request.get("campaign_constitution"))
    identity = _mapping(constitution.get("product_identity"))
    style = _mapping(constitution.get("campaign_style"))
    authorized = _mapping(generation_request.get("authorized_facts"))
    archetype = str(layout.get("composition_archetype") or "").strip()
    contract = _EXECUTION_LAYOUT_CONTRACTS.get(archetype, {})
    composition_gate = _COMPOSITION_GATE_DIRECTIVES.get(
        archetype,
        "Use the page contract's information hierarchy; unequal verified modules "
        "are allowed, while equal-weight catalog grids are rejected.",
    )
    page_index = page.get("index", generation_request.get("page_index"))

    lines = [
        "【AURORA SHADOW execution semantics v1】",
        "先执行本页空间构图契约，再执行商品事实，最后才放置少量中文文案和 finish。",
        "这是六页套图中的一个独立页面；不得把整套页面都解析成同一张主视觉模板。",
        "[PIXEL COMPOSITION GATE] Output exactly one full-bleed composition. Keep one continuous scene from edge to edge; any copy must sit directly in the scene. Equal-weight catalog grids are forbidden, but page-specific unequal verified modules, real callouts, and closure clusters remain allowed when the current contract calls for them.",
        f"页码={page_index}；页面角色={page.get('page_role', generation_request.get('page_role'))}；",
        f"构图原型={archetype or 'CURRENT_PAGE_ARCHETYPE'}。",
        "【身份硬锁】" + _join(identity.get("identity_summary")),
        "[PRODUCT CONSISTENCY GATE] All attached product references are complementary views of one exact SKU. Preserve the same silhouette, proportions, wheel count and placement, red/black color partition, seat count, steering wheel, dashboard/music panel, grille, headlights, doors, steps, mirrors, labels, and component relationships. Do not average variants, merge parts, redraw logos, or add/remove/reshape visible components. A variant fact may remain copy-only unless the same component is visibly supported by the product references.",
        "[FACTUAL COPY GATE] Use only exact user-entered fact strings; if no exact seed exists, use no claim text. Never invent brand comparisons, frequencies, voltages, sizes, safety ratings, certifications, or compatibility.",
        "Allowed exact fact seeds="
        + "display=" + _join(authorized.get("display_facts"))
        + "；selling_points=" + _join(authorized.get("selling_points"))
        + "；attributes=" + _join(authorized.get("attributes"))
        + "；scenes=" + _join(authorized.get("scenes"))
        + "；offers=" + _join(authorized.get("offers"))
        + "；service_or_trust=" + _join(authorized.get("service_or_trust"))
        + "。",
        "必须保留=" + _join(identity.get("must_preserve")) + "。",
        "绝不改变=" + _join(identity.get("must_not_change")) + "。",
        "【空间构图硬契约】" + str(contract.get("geometry") or _join(layout.get("composition_instruction"))),
        "【第一视觉证据】" + str(contract.get("evidence") or _join(page.get("visual_proof"))),
        "【页面差异硬门槛】" + str(contract.get("delta") or "本页必须与相邻页面改变镜头、尺度或阅读路径。"),
        "【禁止复用的骨架】" + str(contract.get("forbidden") or "禁止复制相邻页面的镜头、锚点、信息拓扑和底栏。"),
        "【页面级信息门禁】" + composition_gate,
        "【本页购买任务】" + _join(page.get("buyer_question")),
        "【本页主卖点】" + _join(page.get("primary_claim")),
        "【本页商品区/文字区/主镜头/证据拓扑】"
        f"{layout.get('product_zone', '按本页职责')} / {layout.get('copy_zone', '按本页职责')} / "
        f"{layout.get('dominant_view', '按本页职责')} / {layout.get('evidence_topology', '按本页职责')}。",
        "【共享视觉 DNA 仅控制 finish】"
        f"摄影质感={style.get('photography_finish', '高锐度商业广告质感')}；"
        f"光线={style.get('light_direction', '自然日光')}；色温={style.get('color_temperature', '中性偏暖')}。"
        "不得继承上一页的镜头、空间骨架、标题位置、底栏、局部窗或阅读路径。",
        "若配置没有规格/服务事实，就减少文字并保持留白，不得臆造参数、认证、优惠或售后承诺。",
        "只使用参考图中可核验的商品身份、结构和事实；输出 1:1、至少 1024×1024、中文可读。",
    ]
    return "\n".join(line for line in lines if line.strip())


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _join(value: object) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value if str(item).strip()) or "无"
    return str(value or "无")


def _json(value: object) -> str:
    if value in (None, "", [], {}):
        return "无"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
