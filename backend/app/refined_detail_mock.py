"""Synthetic strategy for the explicitly offline FakeXingheGateway only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .xinghe_agent import PlannerRequest


def fake_strategy(base: dict[str, Any], request: PlannerRequest) -> dict[str, Any]:
    analysis, plan = base["analysis"], base["plan"]
    plan["design_strength_lock"]["signature_devices"] = [
        "synthetic-detail-window",
        "synthetic-scene-depth",
    ]
    analysis.update(
        {
            "information_confidence": {
                "confirmed": ["合成测试素材"],
                "reasonable_inference": [],
                "needs_confirmation": ["真实材质与参数待提供"],
            },
            "buyer_demand_map": [
                {
                    "id": f"D{index}",
                    "rank": index,
                    "demand": f"合成需求 {index}",
                    "buyer_question": f"合成问题 {index}",
                    "reason": "仅用于离线流程测试",
                }
                for index in range(1, request.page_count + 1)
            ],
            "demand_to_selling_point_match": [
                {
                    "demand_id": f"D{index}",
                    "selling_point": f"合成卖点 {index}",
                    "confidence": "confirmed",
                    "evidence": "合成测试输入",
                }
                for index in range(1, request.page_count + 1)
            ],
            "product_type_strategy": {
                "type": "mixed",
                "rationale": "合成测试",
                "priority": "测试流程",
            },
        }
    )
    plan.update(
        {
            "purchase_decision_type": "合成测试决策",
            "page_strategy": "合成测试策略",
            "best_hero_direction": "合成测试开场",
            "module_plan": [],
            "visual_rhythm_plan": [f"合成节奏 {i}" for i in range(request.page_count)],
            "style_system_lock": {"palette": "合成暖白与深棕"},
            "product_identity_physics_lock": {"identity": "仅使用合成测试输入"},
        }
    )
    for index, screen in enumerate(plan["screens"], 1):
        screen.update(
            {
                "structure": f"synthetic-structure-{(index - 1) % 6}",
                "purchase_decision_type": "合成测试决策",
                "buyer_demand_id": f"D{index}",
                "buyer_demand": f"合成需求 {index}",
                "buyer_question": f"合成问题 {index}",
                "core_information": "合成测试信息",
                "matched_selling_point": f"合成卖点 {index}",
                "confidence": "confirmed",
                "copy_suggestion": "合成文案",
                "visual_proof_method": "合成示意",
                "required_proof": "合成输入",
                "required_elements": ["合成主体"],
                "forbidden_elements": ["无依据参数"],
                "risk_reminder": "离线合成测试，不代表商品质量验收",
                "scene_layout": {
                    key: "合成空间"
                    for key in (
                        "foreground",
                        "midground",
                        "background",
                        "physical_relationship",
                    )
                },
                "image_prompt": (
                    "vertical 3:4 e-commerce detail page image\n"
                    f"SYNTHETIC OFFLINE TEST ONLY; page {index}/{request.page_count}; "
                    f"{screen['title']}; {screen['label']}"
                ),
            }
        )
        plan["module_plan"].append(
            {
                "page": index,
                "role": screen["role"],
                "buyer_demand_id": f"D{index}",
            }
        )
    return base
