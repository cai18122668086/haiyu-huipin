"""Vision planning agent for the full Xinghe ecommerce workflow."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urljoin

import requests


CATEGORY_GUIDANCE = {
    "fashion": "服饰/鞋包/配饰：概念封面、陈列、配色、版型/身形逻辑、面料垂感、工艺细节、单品、真实生活场景、总结；有人物时保持人物身份与造型逻辑。",
    "food": "食品/饮料：包装封面、可见食材、过程、包装可见数据、便携、冲泡/食用、包装细节、生活场景、规格卡、总结；不可扩大包装信息为功效。",
    "pet": "宠物用品：封面、真实互动、可见原料/材质、结构或动作、包装、日常使用、信息卡、总结；不可编造适用年龄、喂养量或兽医推荐。",
    "beauty": "美妆/个护：质地封面、低风险痛点、包装可见成分灵感、质地微距、使用步骤、泵头/瓶口细节、浴室/梳妆台/出行场景、总结。",
    "home": "家居/日用：真实家居封面、生活痛点、结构、材质表面、手部操作、空间场景、确认信息卡、总结；不可编造承重、尺寸、抗菌或防水。",
    "electronics": "电子/工具：技术感封面、使用摩擦、端口/按钮/结构、局部细节、桌面/车载/出行场景、操作流程、确认参数卡、总结。",
    "general": "通用品类：封面、买家顾虑、真实场景、核心卖点、材质/结构、使用动作、确认信息卡、总结。",
}


ANALYSIS_SYSTEM = """你是星河电商详情2.0的商品视觉分析与品类路由Agent。只依据用户提供的信息和图片中的可见事实。
同一请求中的多张图片属于同一商品的不同角度，必须综合为一套一致性锚点；若款式、颜色或结构明显冲突，标记 needs_confirmation，不得擅自融合。
不得虚构销量、评价、认证、检测、专利、授权、医疗功效、精确材质/配比/容量/尺寸、电子参数或兼容型号。
不可辨认的小字只能记录其视觉位置，不得猜测内容。合理推测必须标记 needs_confirmation。
输出严格JSON，不要Markdown，字段必须为：
{
  "category_key": "fashion|food|pet|beauty|home|electronics|general",
  "category_reason": "...",
  "product_image_analysis": {
    "visible_facts": ["..."],
    "reasonable_assumptions": ["... needs_confirmation"],
    "invisible_information": ["..."]
  },
  "product_consistency_anchors": {
    "overall_silhouette": "...",
    "color_material": "...",
    "pattern_logo_nameplate": "...",
    "structural_parts": "...",
    "decorative_elements": "...",
    "crop_to_whole": "...",
    "do_not_change": ["..."]
  },
  "buyer_objections": ["6到10条购买前问题或顾虑"]
}"""


PLAN_SYSTEM = """你是星河电商详情2.0的页面策划Agent。根据已完成的商品分析和指定品类结构，生成一套具有购买推进逻辑的页面计划。
硬规则：每页只表达一个核心卖点；每页只保留一个主标题和一个短卖点标签；每页只允许一个视觉主角、最多一个局部放大框、最多两个结构标注；禁止多重拼贴、多个裁切小窗和重复展示同一部位；全组至少五种视觉角色、三种构图尺度、两个记忆视觉装置；不能每页都以完整商品为主角；禁止重复浅渐变+左上标题+居中商品模板；只使用可确认事实；不使用咨询式CTA。
输出严格JSON，不要Markdown：
{
  "campaign_style_lock": "包含统一配色、字体感、背景系统、光影、图标、留白、商品尺度",
  "design_strength_lock": "包含品类视觉母题、页面节奏、构图反差、至少两个视觉装置、颜色对比和模板禁令",
  "screens": [
    {"role":"...","title":"简短买家语言标题","label":"至少一条短卖点","composition":"...","device":"...","difference":"相对上一页至少改变两个维度"}
  ]
}
screens数量必须与用户要求完全一致，最后一页必须是非咨询式收束总结。"""


COMBINED_SYSTEM = """你是星河电商详情2.0的商品视觉分析与页面策划Agent。请在一次调用内完成商品分析、品类路由、产品一致性锚点、买家顾虑拆解和整组页面规划。
用户可能提供同一商品的多张不同角度原图。必须将全部图片视为同一商品组的互补证据，综合正面、侧面、背面、顶部、底部、开合状态和局部细节建立一致性锚点；不得把不同角度误判为多个商品。若图片间存在明显款式、颜色或结构冲突，必须在合理推测中标记 needs_confirmation，不得擅自融合成不存在的商品。
只依据用户提供的信息和图片中的可见事实。不得虚构销量、评价、认证、检测、专利、授权、医疗功效、精确材质/配比/容量/尺寸、电子参数或兼容型号。不可辨认的小字只能记录其视觉位置；合理推测必须标记 needs_confirmation。
页面硬规则：每页只表达一个核心卖点；只保留一个主标题和一个短卖点标签；只允许一个视觉主角、最多一个局部放大框、最多两个结构标注；禁止多重拼贴、多个裁切小窗和重复展示同一部位。全组至少五种视觉角色、三种构图尺度和两个记忆视觉装置；不能每页都以完整商品为主角；最后一页必须是非咨询式收束总结。
输出严格JSON，不要Markdown，字段必须为：
{
  "analysis": {
    "category_key": "fashion|food|pet|beauty|home|electronics|general",
    "category_reason": "...",
    "product_image_analysis": {
      "visible_facts": ["..."],
      "reasonable_assumptions": ["... needs_confirmation"],
      "invisible_information": ["..."]
    },
    "product_consistency_anchors": {
      "overall_silhouette": "...",
      "color_material": "...",
      "pattern_logo_nameplate": "...",
      "structural_parts": "...",
      "decorative_elements": "...",
      "crop_to_whole": "...",
      "do_not_change": ["..."]
    },
    "buyer_objections": ["6到10条购买前问题或顾虑"]
  },
  "plan": {
    "campaign_style_lock": "统一配色、字体感、背景系统、光影、图标、留白和商品尺度",
    "design_strength_lock": "品类视觉母题、页面节奏、构图反差、至少两个视觉装置、颜色对比和模板禁令",
    "screens": [
      {"role":"...","title":"简短买家语言标题","label":"一条短卖点","composition":"单一视觉主角的构图","device":"至多一个主要视觉装置","difference":"相对上一页至少改变两个维度"}
    ]
  }
}
screens数量必须与用户要求完全一致。"""


class AgentPlanningError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise AgentPlanningError("规划模型没有返回可识别的 JSON")
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AgentPlanningError(f"规划模型 JSON 解析失败：{exc}") from exc
    if not isinstance(value, dict):
        raise AgentPlanningError("规划模型返回的 JSON 顶层不是对象")
    return value


class XinghePlanningAgent:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: int = 300):
        self.url = urljoin(base_url.rstrip("/") + "/", "v1/chat/completions")
        self.model = model.strip()
        self.timeout = timeout
        self.headers = {"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}
        if not self.model:
            raise ValueError("视觉规划模型不能为空")

    def _chat(self, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        body = {"model": self.model, "messages": messages, "response_format": {"type": "json_object"}}
        if self.model.startswith("gpt-5.6"):
            body["reasoning_effort"] = "low"
        else:
            body["temperature"] = 0.2
        response = requests.post(self.url, headers=self.headers, json=body, timeout=self.timeout)
        if response.status_code >= 400 and "response_format" in response.text:
            body.pop("response_format", None)
            response = requests.post(self.url, headers=self.headers, json=body, timeout=self.timeout)
        if response.status_code >= 400:
            raise AgentPlanningError(f"视觉规划接口 HTTP {response.status_code}: {response.text[:1000]}")
        raw = response.json()
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentPlanningError(f"无法识别规划接口响应，顶层字段：{', '.join(raw.keys())}") from exc
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return _extract_json(str(content)), raw

    @staticmethod
    def _reference_content(
        *,
        image_bytes: bytes | None,
        image_name: str | None,
        reference_images: Sequence[tuple[str, bytes]] | None,
    ) -> list[dict[str, Any]]:
        if reference_images is not None:
            references = [(str(name), data) for name, data in reference_images]
        elif image_bytes is not None and image_name:
            references = [(image_name, image_bytes)]
        else:
            references = []
        if not references:
            raise ValueError("至少需要一张参考图")
        content: list[dict[str, Any]] = []
        for index, (name, data) in enumerate(references, start=1):
            if not name or not isinstance(data, bytes) or not data:
                raise ValueError(f"第 {index} 张参考图无效")
            mime = mimetypes.guess_type(name)[0] or "image/png"
            image_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            content.append({"type": "image_url", "image_url": {"url": image_url}})
        return content

    def analyze(
        self,
        *,
        image_bytes: bytes | None = None,
        image_name: str | None = None,
        reference_images: Sequence[tuple[str, bytes]] | None = None,
        product_name: str,
        product_facts: str,
        selling_points: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        image_content = self._reference_content(
            image_bytes=image_bytes,
            image_name=image_name,
            reference_images=reference_images,
        )
        user_text = (
            f"商品名称：{product_name or '未提供'}\n"
            f"用户确认信息：{product_facts or '未提供'}\n"
            f"用户提供卖点/顾虑：{selling_points or '未提供'}\n"
            f"本组共有 {len(image_content)} 张同一商品的不同角度原图。"
            "请综合全部图片、路由品类、建立产品一致性锚点，并拆解6到10个买家顾虑。"
        )
        return self._chat([
            {"role": "system", "content": ANALYSIS_SYSTEM},
            {"role": "user", "content": [{"type": "text", "text": user_text}, *image_content]},
        ])

    @staticmethod
    def _validate_plan(plan: dict[str, Any], count: int) -> None:
        screens = plan.get("screens")
        if not isinstance(screens, list) or len(screens) != count:
            actual = len(screens) if isinstance(screens, list) else 0
            raise AgentPlanningError(f"规划页数不正确：要求 {count}，实际 {actual}")
        required = {"role", "title", "label", "composition", "device", "difference"}
        for index, screen in enumerate(screens, start=1):
            if not isinstance(screen, dict) or not required.issubset(screen):
                raise AgentPlanningError(f"第 {index} 页规划缺少必要字段")

    def analyze_and_plan(
        self,
        *,
        image_bytes: bytes | None = None,
        image_name: str | None = None,
        reference_images: Sequence[tuple[str, bytes]] | None = None,
        product_name: str,
        product_facts: str,
        selling_points: str,
        count: int,
        buyer_language: str,
        campaign_style: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        image_content = self._reference_content(
            image_bytes=image_bytes,
            image_name=image_name,
            reference_images=reference_images,
        )
        guidance = "\n".join(f"- {key}: {value}" for key, value in CATEGORY_GUIDANCE.items())
        user_text = (
            f"商品名称：{product_name or '未提供'}\n"
            f"用户确认信息：{product_facts or '未提供'}\n"
            f"用户提供卖点/顾虑：{selling_points or '未提供'}\n"
            f"要求页数：{count}\n图内语言：{buyer_language}\n"
            f"用户偏好风格：{campaign_style or '未提供，由你依据品类制定'}\n"
            f"可用品类结构指南：\n{guidance}\n"
            f"本组共有 {len(image_content)} 张同一商品的不同角度原图。"
            "请综合观察全部图片并一次性完成分析与页面规划。"
        )
        combined, raw = self._chat([
            {"role": "system", "content": COMBINED_SYSTEM},
            {"role": "user", "content": [{"type": "text", "text": user_text}, *image_content]},
        ])
        analysis = combined.get("analysis")
        plan = combined.get("plan")
        if not isinstance(analysis, dict) or not isinstance(plan, dict):
            raise AgentPlanningError("单次规划响应缺少 analysis 或 plan 对象")
        self._validate_plan(plan, count)
        return analysis, plan, raw

    def plan(
        self,
        *,
        analysis: dict[str, Any],
        count: int,
        buyer_language: str,
        campaign_style: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        category = analysis.get("category_key", "general")
        guidance = CATEGORY_GUIDANCE.get(category, CATEGORY_GUIDANCE["general"])
        user_text = (
            f"要求页数：{count}\n图内语言：{buyer_language}\n用户偏好风格：{campaign_style or '未提供，由你依据品类制定'}\n"
            f"品类结构指南：{guidance}\n商品分析JSON：{json.dumps(analysis, ensure_ascii=False)}"
        )
        plan, raw = self._chat([
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": user_text},
        ])
        self._validate_plan(plan, count)
        return plan, raw
