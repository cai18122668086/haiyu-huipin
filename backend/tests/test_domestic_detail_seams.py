from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.domain import JobStatus
from app.domestic_detail.contracts import normalize_native_plan_for_harness
from tests.test_api import WORKSPACE_ID, dispatch_pending, upload_asset
from tests.test_domestic_detail_execution import _create_detail_job, _gateway


def test_internal_seam_uses_lower_incoming_and_preserves_authority() -> None:
    upper = {
        "method_id": "CHAPTER_BAND",
        "source_region_previous": "M02 底部",
        "target_region_current": "M03 顶部",
        "seam_carrier": "前组章节带",
        "handoff_geometry": "前组进入上屏",
    }
    lower = {
        "method_id": "BACKGROUND_STRINGING",
        "source_region_previous": "M03 底部",
        "target_region_current": "M04 顶部",
        "seam_carrier": "本组连续光带",
        "handoff_geometry": "本组上屏进入下屏",
    }
    plan = {
        "modules": [
            {"id": "M03", "transition_contract": upper},
            {"id": "M04", "transition_contract": lower},
        ],
        "output_strategy": {
            "continuous_pair_rendering": {
                "pair_ids": [
                    {"pair_id": "P02", "module_ids": ["M03", "M04"]},
                ]
            }
        },
    }
    before = deepcopy(plan)
    normalized = normalize_native_plan_for_harness(plan)
    pair = normalized["output_strategy"]["continuous_pair_rendering"]["pair_ids"][0]
    assert pair["internal_seam_method_id"] == lower["method_id"]
    assert pair["internal_seam_source_region"] == "M03 底部"
    assert pair["internal_seam_target_region"] == "M04 顶部"
    assert pair["internal_seam_carrier"] == "本组连续光带"
    assert plan == before


@pytest.mark.parametrize(
    "field,expected",
    [
        ("source_region_previous", "M03"),
        ("target_region_current", "M04"),
    ],
)
def test_foreign_pair_reference_is_repaired_before_approval(
    client: TestClient,
    monkeypatch,
    field: str,
    expected: str,
) -> None:
    gateway = _gateway(client)
    original = gateway.plan

    async def foreign_reference(request):
        result = await original(request)
        payload = deepcopy(result.payload)
        if (
            request.authoring_stage == "pair"
            and request.authoring_target_ids == ("M03", "M04")
            and not (request.authoring_context or {}).get("validation_errors")
        ):
            payload["modules"][1]["transition_contract"][field] = "M02 外组区域"
        return replace(result, payload=payload)

    monkeypatch.setattr(gateway, "plan", foreign_reference)
    asset = upload_asset(client, filename="foreign-seam-reference.png")
    job = _create_detail_job(
        client, asset["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value, (
        planned.get("error_code"), planned.get("error_message")
    )
    assert [call.authoring_stage for call in gateway.planner_calls] == [
        "", "story", "pair", "pair", "pair", "pair", "pair",
    ]
    correction = gateway.planner_calls[4]
    assert correction.authoring_target_ids == ("M03", "M04")
    errors = correction.authoring_context["validation_errors"]
    assert errors == [{
        "path": "/modules/1/transition_contract/" + field,
        "constraint": "native_pair_seam",
        "message": f"Incoming pair {field} must reference only {expected}",
    }]
    assert planned["plan"]["payload"]["native_approval"]["document_sha256"]
    assert gateway.image_calls == []
