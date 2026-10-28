"""Approved absence/token plans keep truthful observations through full jobs."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from app.aurora_detail_runtime import detail_workspace
from app.domestic_detail.atomic_review import synthetic_identity
from app.domestic_detail.identity_review import observed_pair_scope
from app.xinghe_agent import XingheImageProvider
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_domestic_detail_execution import (
    _aurora_approval_payload,
    _create_detail_job,
    _gateway,
)
from tests.test_domestic_detail_identity_scope import SCOPE_PRESENCE, actual_role


def raw_role(module, role, *, spine_only=False):
    subject = "ENVIRONMENT_SCENE" if role == "ABSENT" else "PRODUCT_DETAIL"
    camera = "wide" if role == "ABSENT" else "macro"
    module.update(
        primary_subject_class=subject,
        presentation_form="ASYMMETRIC_EDITORIAL",
        camera_scale=camera,
        complete_product_role=role,
    )
    if spine_only:
        return
    module["expression_signature"] = "+".join(
        (subject, "ASYMMETRIC_EDITORIAL", camera, role)
    )
    module["expression"].update(
        expression_mode="IMAGE_PLUS_COPY",
        claim_visualizability="VISUALIZABLE",
        primary_visual_carrier="USE_SCENE" if role == "ABSENT" else "PRODUCT_DETAIL",
        product_presence_policy="ABSENT_PREFERRED"
        if role == "ABSENT"
        else "IDENTITY_TOKEN_ONLY",
        transformation_required=False,
    )
    module["product_presence_decision"].update(
        policy="ABSENT_PREFERRED" if role == "ABSENT" else "IDENTITY_TOKEN_ONLY",
        primary_subject_without_product="既有使用情境"
        if role == "ABSENT"
        else "原素材局部身份线索",
        identity_binding_without_product=["只使用原始素材的身份与系列视觉线索"],
        absence_reason="保留商品专属情境与局部身份线索，不使用完整商品",
    )
    module["proof"]["primary_subject"] = (
        "既有使用情境" if role == "ABSENT" else "原素材局部身份线索"
    )
    module["proof"]["visual_specificity_anchor"] = ["沿用用户原始素材可见的身份线索"]
    module["visual_cast"].update(
        hero_subject=module["proof"]["primary_subject"],
        product_identity_anchor="只使用原始素材的身份与系列视觉线索",
    )


@pytest.mark.parametrize("mode", ["matching", "unexpected_full", "unexpected_absence"])
def test_approved_absence_and_token_roles_are_honored_without_false_silhouette_pass(
    client, monkeypatch, mode
):
    gateway = _gateway(client)
    original_plan = gateway.plan

    async def author(request):
        result = await original_plan(request)
        if request.planning_profile not in {
            "aurora_domestic_detail",
            "aurora_domestic_campaign_completion",
            "aurora_domestic_plan_repair",
            "aurora_domestic_modules_completion",
        }:
            return result
        payload = deepcopy(result.payload)
        for module in payload.get("modules", []):
            if module.get("id") == "M03":
                raw_role(
                    module, "ABSENT", spine_only=request.authoring_stage == "story"
                )
            elif module.get("id") == "M04":
                raw_role(
                    module,
                    "IDENTITY_TOKEN_ONLY",
                    spine_only=request.authoring_stage == "story",
                )
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", author)
    source = upload_asset(client, filename="scoped-identity-original.png")
    created = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    planned = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created['id']}"
    ).json()
    assert planned["status"] == "awaiting_confirmation", (
        planned.get("error_code"),
        planned.get("error_message"),
        planned.get("plan"),
    )
    reports = []

    async def inspect(self, request, native, sources, artifact):
        judgment = self._native_detail_judgment(native, artifact)
        if mode == "unexpected_full" and native["pair_id"] == "P02":
            actual_role(
                judgment["module_judgments"][0],
                native["approved_module_contracts"][0]["contract"],
                "DOMINANT",
            )
            judgment.update(status="MATERIAL_FAIL", material_failures=[])
        elif mode == "unexpected_absence" and native["pair_id"] == "P01":
            for row, contract in zip(
                judgment["module_judgments"],
                native["approved_module_contracts"],
                strict=True,
            ):
                actual_role(row, contract["contract"], "ABSENT")
            judgment.update(status="MATERIAL_FAIL", material_failures=[])
        scope = observed_pair_scope(judgment["module_judgments"])
        judgment["identity_silhouette"] = synthetic_identity(
            SCOPE_PRESENCE[scope],
            "SYNTHETIC aggregate of actual per-slice observed identity scopes",
        )
        reports.append(deepcopy(judgment))
        return self._normalize_native_pair_judgment(judgment, native, artifact)

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", inspect
    )
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert confirmed.status_code == 202, confirmed.text
    dispatch_pending(client)
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created['id']}"
    ).json()
    assert current["status"] == "awaiting_final_review", current
    assert len(gateway.image_calls) == (4 if mode == "matching" else 5)
    state = json.loads(
        (detail_workspace(created["id"]) / "session.json").read_text(encoding="utf-8")
    )
    assert [
        module["plan"]["complete_product_role"] for module in state["modules"][2:4]
    ] == ["ABSENT", "IDENTITY_TOKEN_ONLY"]
    assert all(
        module["status"] in {"COMPLETE", "COMPLETE_WITH_RESIDUALS"}
        for module in state["modules"]
    )
    if mode == "matching":
        pair = next(row for row in reports if row["pair_id"] == "P02")
        assert (
            pair["identity_silhouette"]["comparison_scope"] == "VISIBLE_IDENTITY_TOKEN"
        )
        identity = pair["module_judgments"][0]["identity_silhouette"]
        assert all(
            identity[key] == "NOT_APPLICABLE"
            for key in ("shape_consistent", "colorway_consistent", "texture_consistent")
        )
    else:
        target = (
            state["modules"][2:4] if mode == "unexpected_full" else state["modules"][:2]
        )
        assert all(
            module["status"] == "COMPLETE_WITH_RESIDUALS"
            and len(module["attempts"]) == 2
            for module in target
        )
        expected_scope = (
            "VISIBLE_COMPLETE_PRODUCT"
            if mode == "unexpected_full"
            else "NO_VISIBLE_PRODUCT"
        )
        assert all(
            module["attempts"][-1]["judgment"]["identity_silhouette"][
                "comparison_scope"
            ]
            == expected_scope
            for module in target
        )
        if mode == "unexpected_absence":
            assert all(
                module["attempts"][-1]["judgment"]["identity_silhouette"][
                    "shape_consistent"
                ]
                == "NOT_APPLICABLE"
                for module in target
            )
