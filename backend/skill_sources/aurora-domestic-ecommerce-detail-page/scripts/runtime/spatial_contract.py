from __future__ import annotations

import math

from .state import HarnessError


STRUCTURE_DEFAULTS = {
    "FULL_BLEED_HERO": ("FULL_BLEED_SINGLE_SCENE", 1, 1, 0, 0),
    "SPLIT_PANEL": ("SPLIT", 2, 1, 1, 0),
    "STACKED_BANDS": ("STACKED", 2, 1, 1, 0),
    "NESTED_INFO_BOARD": ("NESTED", 3, 1, 2, 0),
    "PROCESS_STRIP_PLUS_RESULT": ("PROCESS_TO_RESULT", 3, 1, 2, 0),
    "CENTRAL_OBJECT_ORBIT": ("ORBIT", 3, 1, 2, 0),
    "FRAMED_LIFESTYLE_PLUS_CARDS": ("SCENE_TO_CARDS", 3, 1, 2, 2),
    "PRODUCT_PAIR_PLUS_TABLE": ("PRODUCT_TO_SPECIFICATION", 3, 1, 2, 0),
    "MACRO_DIAGRAM": ("NESTED", 2, 1, 1, 0),
    "PORTRAIT_STRIP_PLUS_PAIN_ARRAY": ("SCENE_TO_CARDS", 3, 1, 2, 2),
    "ASYMMETRIC_EDITORIAL": ("SPLIT", 2, 1, 1, 0),
    "COMPARISON_SPLIT": ("SPLIT", 2, 2, 0, 0),
    "SELECTION_MATRIX": ("GRID", 3, 1, 2, 2),
    "SPECIFICATION_BOARD": ("PRODUCT_TO_SPECIFICATION", 2, 1, 1, 0),
}

STRUCTURE_FAMILIES = set(STRUCTURE_DEFAULTS)
STRUCTURAL_RELATIONSHIPS = {
    "FULL_BLEED_SINGLE_SCENE", "SPLIT", "STACKED", "NESTED", "ORBIT", "PATH",
    "GRID", "PROCESS_TO_RESULT", "SCENE_TO_CARDS", "PRODUCT_TO_SPECIFICATION",
}
DEFAULT_FALLBACKS = ["一张全幅照片叠加标题", "通用卡片漂浮在同一背景上", "所有信息等大平铺"]

ZONE_LAYOUTS = {
    "FULL_BLEED_HERO": [("英雄主体区", "全幅连续场景中的主导区域", 100)],
    "SPLIT_PANEL": [("主证据区", "非对称大区", 64), ("辅助判断区", "窄侧栏或下沉区", 36)],
    "STACKED_BANDS": [("主叙事带", "上部宽带", 62), ("结果或补充带", "下部窄带", 38)],
    "NESTED_INFO_BOARD": [("主视觉底板", "大底板", 58), ("证据嵌套区", "内嵌中区", 27), ("结论锚点", "局部小区", 15)],
    "PROCESS_STRIP_PLUS_RESULT": [("过程路径", "上部连续路径", 56), ("结果主区", "下部大结果区", 29), ("购买结论", "局部收口区", 15)],
    "CENTRAL_OBJECT_ORBIT": [("中心主体", "中央大区", 60), ("环绕证据", "外围环形区", 25), ("结果锚点", "偏置小区", 15)],
    "FRAMED_LIFESTYLE_PLUS_CARDS": [("真实场景", "大幅框景", 62), ("场景证据", "下部信息带", 23), ("补充小场景", "非等大缩略区", 15)],
    "PRODUCT_PAIR_PLUS_TABLE": [("商品选择区", "上部双主体区", 60), ("差异判断区", "下部结构化区", 25), ("购买提示区", "底部收口区", 15)],
    "MACRO_DIAGRAM": [("整体或结果主体", "大区", 62), ("微距证据区", "嵌套局部区", 38)],
    "PORTRAIT_STRIP_PLUS_PAIN_ARRAY": [("人物或场景带", "上部宽带", 56), ("痛点后果区", "下部主区", 29), ("差异小场景", "错落小区", 15)],
    "ASYMMETRIC_EDITORIAL": [("大视觉区", "偏置大区", 68), ("文字与证据区", "对侧窄区", 32)],
    "COMPARISON_SPLIT": [("状态A", "左侧主区", 50), ("状态B", "右侧主区", 50)],
    "SELECTION_MATRIX": [("主选择对象", "顶部大区", 56), ("适用关系", "中部矩阵", 29), ("选择结论", "底部锚点", 15)],
    "SPECIFICATION_BOARD": [("商品全貌", "上部主区", 58), ("必要规格", "下部结构区", 42)],
}


def resolve_spatial_contract(module: dict) -> dict:
    """Expand a compact blueprint; keep full v0.9.2 contracts compatible."""
    raw = module.get("module_spatial_structure_contract")
    if not isinstance(raw, dict):
        raise HarnessError(f"Module {module.get('id', '?')} requires a spatial structure blueprint")
    family = raw.get("structure_family")
    if family not in STRUCTURE_DEFAULTS:
        raise HarnessError(f"Module {module.get('id', '?')} uses an unsupported spatial structure")
    relationship, default_zones, dominant, secondary, mini = STRUCTURE_DEFAULTS[family]

    if isinstance(raw.get("zones"), list) and raw["zones"]:
        zones = raw["zones"]
    else:
        if not isinstance(raw.get("zone_briefs"), list):
            raise HarnessError(f"Module {module.get('id', '?')} zone_briefs must be a list")
        briefs = [str(item).strip() for item in raw.get("zone_briefs", []) if isinstance(item, str) and item.strip()]
        if not briefs:
            raise HarnessError(f"Module {module.get('id', '?')} needs zones or compact zone_briefs")
        layouts = ZONE_LAYOUTS.get(family)
        if not layouts:
            raise HarnessError(f"Module {module.get('id', '?')} lacks a spatial layout definition")
        if len(briefs) != len(layouts):
            if module.get("authoring_plan_format") == "COMPACT_V1_3":
                raise HarnessError(f"Module {module.get('id', '?')} {family} requires exactly {len(layouts)} concrete zone briefs")
            fallback = {
                1: [("主导意义区", "连续主导大区", 100)],
                2: [("主导意义区", "非对称大区", 64), ("辅助意义区", "窄侧区或下沉区", 36)],
                3: [("主导意义区", "大区", 58), ("辅助证据区", "中区", 27), ("收口意义区", "局部小区", 15)],
            }
            layouts = fallback.get(len(briefs))
            if layouts is None:
                weights = [50, 22, 14] + [round(14 / (len(briefs) - 3), 1)] * (len(briefs) - 3)
                layouts = [("主导意义区" if i == 0 else f"辅助意义区{i}", "大区" if i == 0 else "错落局部区", weights[i]) for i in range(len(briefs))]
        zones = [
            {
                "role": layouts[index - 1][0],
                "geometry": layouts[index - 1][1],
                "content": brief,
                "visual_weight_percent": layouts[index - 1][2],
            }
            for index, brief in enumerate(briefs, start=1)
        ]

    resolved = dict(raw)
    resolved.update(
        {
            "structure_family": family,
            "zone_count": raw.get("zone_count", len(zones) or default_zones),
            "zones": zones,
            "structural_relationship": raw.get("structural_relationship", relationship),
            "full_bleed_single_scene_allowed": raw.get(
                "full_bleed_single_scene_allowed", relationship == "FULL_BLEED_SINGLE_SCENE"
            ),
            "dominant_zone_count": raw.get("dominant_zone_count", dominant),
            "secondary_panel_count": raw.get("secondary_panel_count", secondary),
            "mini_scene_count": raw.get("mini_scene_count", mini),
            "series_master_layout_inheritance": False,
            "forbidden_fallbacks": raw.get("forbidden_fallbacks") or DEFAULT_FALLBACKS,
        }
    )
    return resolved


def _minimum_families(module_count: int) -> int:
    return 1 if module_count == 1 else 2 if module_count < 6 else 3 if module_count < 8 else 4


def validate_spatial_contract(plan: dict) -> None:
    """Validate observable structure without a duplicated campaign rhythm ledger."""
    modules = plan["modules"]
    families = []
    relationships = []
    full_bleed_run = max_full_bleed_run = 0

    for module in modules:
        contract = resolve_spatial_contract(module)
        family = contract["structure_family"]
        relationship = contract["structural_relationship"]
        if relationship not in STRUCTURAL_RELATIONSHIPS:
            raise HarnessError(f"Module {module['id']} uses an unsupported spatial relationship")
        if len(contract["zones"]) != contract["zone_count"]:
            raise HarnessError(f"Module {module['id']} zone_count must match its declared zones")
        if sum(float(zone.get("visual_weight_percent", 0)) for zone in contract["zones"]) < 90:
            raise HarnessError(f"Module {module['id']} spatial zones must account for the visible canvas")
        if len(set(contract["forbidden_fallbacks"])) < 3:
            raise HarnessError(f"Module {module['id']} needs three concrete anti-collapse fallbacks")
        if contract["zone_count"] < 1 or contract["dominant_zone_count"] < 1:
            raise HarnessError(f"Module {module['id']} must have a visible meaning-bearing zone")
        if relationship != "FULL_BLEED_SINGLE_SCENE" and contract["zone_count"] < 2:
            raise HarnessError(f"Module {module['id']} needs multiple zones for its composite structure")
        if family == "COMPARISON_SPLIT" and not str(contract.get("comparison_basis", "")).strip():
            raise HarnessError(f"Module {module['id']} may use COMPARISON_SPLIT only with a truthful comparison_basis")
        if module.get("story_function") == "PURCHASE_CLOSURE" and family in {
            "PRODUCT_PAIR_PLUS_TABLE", "SPECIFICATION_BOARD"
        }:
            raise HarnessError("Purchase closure must return to product benefit, audience or use; it may not end as a table wall")

        families.append(family)
        relationships.append(relationship)
        planned_full_bleed = relationship == "FULL_BLEED_SINGLE_SCENE"
        full_bleed_run = full_bleed_run + 1 if planned_full_bleed else 0
        max_full_bleed_run = max(max_full_bleed_run, full_bleed_run)

    minimum = _minimum_families(len(modules))
    distinct = len(set(families))
    if distinct < minimum:
        raise HarnessError(f"The page needs at least {minimum} spatial families for this module count")
    if len(modules) >= 6:
        maximum = max(minimum, math.ceil(len(modules) * 0.65))
        if distinct > maximum:
            raise HarnessError("The page uses excessive one-off structures; structure diversity is driving the story")
    if max_full_bleed_run > 1:
        raise HarnessError("The page exceeds the full-bleed single-scene repetition limit")
    for index in range(max(0, len(modules) - 2)):
        if len(set(relationships[index:index + 3])) < 2:
            raise HarnessError("A rolling three-module window lacks structural relationship variation")


