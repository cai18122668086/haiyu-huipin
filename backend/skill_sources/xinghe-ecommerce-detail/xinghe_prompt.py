"""Prompt planning for Xinghe ecommerce detail-page image sets."""

from __future__ import annotations

import math

CATEGORY_OPTIONS = {
    "自动/通用": "general",
    "服饰/鞋包/配饰": "fashion",
    "食品/饮料": "food",
    "宠物用品": "pet",
    "美妆/个护": "beauty",
    "家居/日用": "home",
    "电子/工具": "electronics",
}

HIGH_QUALITY_ROLE_KEYWORDS = (
    "封面",
    "结构",
    "细节",
    "特写",
    "材质",
    "表面",
    "工艺",
    "纹理",
    "微距",
)


def quality_for_screen(screen: dict) -> str:
    del screen
    return "high"


def estimate_output_cost_range(
    count: int,
    groups: int,
    medium_price: float,
    high_price: float,
) -> dict:
    """Return a truthful pre-planning range before dynamic roles exist."""
    page_count = count * groups
    return {
        "page_count": page_count,
        "minimum_usd": round(page_count * high_price, 4),
        "maximum_usd": round(page_count * high_price, 4),
    }


def build_prompts_from_agent_plan(
    *,
    analysis: dict,
    agent_plan: dict,
    buyer_language: str,
    product_name: str,
    product_facts: str,
    reference_image_count: int = 1,
    size: str = "1024x1536",
) -> tuple[list[dict], dict]:
    """Compile validated agent decisions into the skill's per-image prompt contract."""
    anchors = analysis.get("product_consistency_anchors", {})
    visible = analysis.get("product_image_analysis", {}).get("visible_facts", [])
    fidelity_lock = (
        f"商品：{product_name.strip() or '参考图中的商品'}；本组附带 {reference_image_count} 张同一商品的不同角度原图，"
        "必须综合全部原图作为唯一产品身份参考，不得把不同角度当成不同商品；"
        f"整体轮廓：{anchors.get('overall_silhouette', '严格保持原图')}；"
        f"颜色材质：{anchors.get('color_material', '严格保持原图')}；"
        f"图案/logo/铭牌：{anchors.get('pattern_logo_nameplate', '保持可见位置与方向，不杜撰不可读文字')}；"
        f"结构部件：{anchors.get('structural_parts', '保持部件大小和相对位置')}；"
        f"装饰元素：{anchors.get('decorative_elements', '保持原图')}；"
        f"局部与整体：{anchors.get('crop_to_whole', '放大局部只能来自同一商品')}；"
        f"禁止改变：{'；'.join(anchors.get('do_not_change', [])) or '不得变成同品类另一款，不得新增或删除关键结构'}。"
        f"可见事实：{'；'.join(map(str, visible)) or product_facts or '仅依据原图可见事实'}。"
    )
    campaign_lock = str(agent_plan["campaign_style_lock"])
    design_lock = str(agent_plan["design_strength_lock"])
    negative = (
        "禁止虚构销量、排名、评价、认证、检测、专利、品牌授权、医疗功效、精确参数、材质配比或兼容型号；"
        "不可辨认文字仅保留位置；禁止咨询式CTA、乱码、无意义英文、空标签、多屏拼图；"
        "单页最多一个局部放大框、最多两个结构标注，禁止重复展示同一部位，禁止同时出现多个裁切小窗。"
    )
    screens = agent_plan["screens"]
    width_text, height_text = size.split("x", maxsplit=1)
    width, height = int(width_text), int(height_text)
    divisor = math.gcd(width, height)
    ratio = f"{width // divisor}:{height // divisor}"
    prompts = []
    for index, screen in enumerate(screens, start=1):
        prompt = f"""e-commerce detail page image; exact canvas {width}x{height} ({ratio})
只生成一张独立竖版电商详情页，不要拼版。第 {index}/{len(screens)} 页。

【Campaign Style Lock】{campaign_lock}
【Design Strength Lock】{design_lock}
【Product Consistency Lock】{fidelity_lock}
【当前页面角色】{screen['role']}
【页面主标题】用{buyer_language}清晰显示：“{screen['title']}”
【卖点标签】用{buyer_language}清晰显示：“{screen['label']}”
【构图与主体尺度】{screen['composition']}
【视觉装置】{screen['device']}
【与上一页差异】{screen['difference']}
【文案层级】只保留一个主标题和一个短卖点标签，解释文字极少；本页只表达一个核心卖点。
【单页信息密度】只允许一个视觉主角；最多一个局部放大框、最多两个结构标注；禁止多重拼贴、重复展示同一部位和多个裁切小窗。
【负面约束】{negative}
直接生成最终详情页图像。产品可局部出现，凡出现的局部必须来自同一参考商品。"""
        prompts.append({"index": index, "screen": screen, "quality": quality_for_screen(screen), "prompt": prompt})
    combined_plan = {
        "product_image_analysis": analysis.get("product_image_analysis", {}),
        "buyer_objection_breakdown": analysis.get("buyer_objections", []),
        "campaign_style_lock": campaign_lock,
        "design_strength_lock": design_lock,
        "product_consistency_lock": fidelity_lock,
        "screen_frameworks": screens,
        "category_key": analysis.get("category_key", "general"),
        "category_reason": analysis.get("category_reason", ""),
    }
    return prompts, combined_plan
