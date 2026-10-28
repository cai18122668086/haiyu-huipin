"""Observation truth checks; no synthetic result represents visual acceptance."""

import json
from copy import deepcopy

import pytest

from app.domestic_detail.campaign_protocol import (
    CampaignObservationError,
    validate_campaign_observation,
)


def example():
    context = {
        "native_contract": {"required_checks": ["identity", "seam"]},
        "artifact_binding": {
            "image_sha256": "a" * 64,
            "auditor": "synthetic",
            "audited_at": "2026-10-18T00:00:00Z",
        },
    }
    raw = {
        "status": "PASS",
        "audit_complete": True,
        "artifact_binding": deepcopy(context["artifact_binding"]),
        "checks": {
            key: {
                "observation_status": "OBSERVED",
                "pass": True,
                "visible_evidence": f"Synthetic {key}: M01 left band x=40,y=80.",
            }
            for key in context["native_contract"]["required_checks"]
        },
    }
    return context, raw


@pytest.mark.parametrize(
    "failure",
    [
        "unknown",
        "bool_int",
        "missing",
        "extra",
        "incomplete",
        "false_pass",
        "wrong_raster",
        "raw_mismatch",
        "raw_missing",
    ],
)
def test_campaign_quality_cannot_be_filled_from_missing_or_contradictory_observations(
    failure,
):
    context, raw = example()
    if failure == "unknown":
        raw["checks"]["identity"]["observation_status"] = "UNKNOWN"
        raw["checks"]["identity"]["pass"] = None
    elif failure == "bool_int":
        raw["checks"]["identity"]["pass"] = 1
    elif failure == "missing":
        raw["checks"].pop("identity")
    elif failure == "extra":
        raw["checks"]["invented"] = deepcopy(raw["checks"]["identity"])
    elif failure == "incomplete":
        raw["audit_complete"] = False
    elif failure == "false_pass":
        raw["checks"]["identity"]["pass"] = False
    elif failure == "wrong_raster":
        raw["artifact_binding"]["image_sha256"] = "b" * 64
    original = json.dumps(raw)
    if failure == "raw_mismatch":
        original = "{}"
    if failure == "raw_missing":
        original = None
    with pytest.raises(CampaignObservationError):
        validate_campaign_observation(raw, context, original_text=original)


def test_explicit_residual_observation_remains_false():
    context, raw = example()
    raw["status"] = "PASS_WITH_RESIDUALS"
    raw["checks"]["seam"]["pass"] = False
    preserved = validate_campaign_observation(
        raw, context, original_text=json.dumps(raw)
    )
    assert preserved == raw and preserved["checks"]["seam"]["pass"] is False


def test_actual_campaign_prompt_schema_and_raster_use_one_selected_http_transport():
    import asyncio
    import base64
    import hashlib

    from app.xinghe_agent import PlannerRequest, SourceImage
    from tests.test_domestic_detail_planner_dispatch import gateway

    context, raw = example()
    context["profile"] = "aurora_detail_campaign_audit"
    context["native_contract"]["judgment_schema"] = {
        "type": "object",
        "properties": {},
        "required": ["status", "audit_complete", "checks", "artifact_binding"],
    }
    context["approved_plan"] = {"synthetic": "comparison constraints only"}
    raster = b"explicit-synthetic-model-seen-bytes"
    frame = SourceImage(
        asset_id="synthetic-frame",
        group_key="campaign-review",
        filename="M01.png",
        content_type="image/png",
        sha256=hashlib.sha256(raster).hexdigest(),
        payload=raster,
        source_role="accepted_detail_screen",
    )
    original = json.dumps(raw, ensure_ascii=False)

    class Transport:
        calls = []

        async def post_json(self, **kwargs):
            self.calls.append(kwargs)
            return 200, {
                "id": "synthetic-campaign-http",
                "model": "reported-observer",
                "choices": [{"message": {"content": original}}],
                "usage": {"input_tokens": 9, "output_tokens": 8},
            }

    transport = Transport()
    provider = gateway(transport, order="failover,primary")
    route = provider.native_planner_route()
    request = PlannerRequest(
        workspace_id="synthetic",
        job_id="synthetic-review",
        group_key="campaign-review",
        sources=(frame,),
        page_count=8,
        language="zh-CN",
        product_name="Synthetic",
        product_information=(json.dumps(context),),
        platform_code="domestic-detail",
        size="1440x15360",
        idempotency_key="synthetic-review-once",
        planning_profile="aurora_detail_campaign_audit",
        domestic_protocol=True,
        native_dispatch=route,
    )
    result = asyncio.run(provider.plan(request))
    assert result.error_code is None, result.error_message
    assert len(transport.calls) == 1
    payload = transport.calls[0]["payload"]
    assert payload["model"] == route["model"] == "alternate-model"
    assert payload["max_completion_tokens"] == 16000
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["checks"]["required"] == ["identity", "seam"]
    assert schema["properties"]["checks"]["properties"]["identity"]["properties"][
        "pass"
    ]["type"] == ["boolean", "null"]
    message = json.dumps(payload["messages"])
    assert base64.b64encode(raster).decode() in message
    assert "never observations" in message and "UNKNOWN/null" in message
    assert result.audit_raw_content == original and result.payload == raw
    assert (
        result.provider_envelope["native_planner_dispatch"]["transport_attempts"] == 1
    )
    assert (
        validate_campaign_observation(result.payload, context, original_text=original)
        == raw
    )


@pytest.mark.parametrize("change", [None, "order", "pixel", "extra", "missing"])
def test_campaign_preview_is_exactly_the_accepted_ordered_pixels(change):
    from io import BytesIO

    from PIL import Image

    from app.domestic_detail.campaign_provider import validate_campaign_preview

    def encoded(image):
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    first = Image.new("RGB", (8, 12), "navy")
    second = Image.new("RGB", (8, 12), "white")
    canvas = Image.new("RGB", (8, 25 if change == "extra" else 24), "white")
    canvas.paste(first, (0, 0))
    canvas.paste(second, (0, 12))
    if change == "pixel":
        canvas.putpixel((2, 4), (200, 40, 10))
    screens = [encoded(first), encoded(second)]
    if change == "order":
        screens.reverse()
    if change == "missing":
        screens.pop()
    if change is None:
        validate_campaign_preview(encoded(canvas), screens)
    else:
        with pytest.raises(ValueError):
            validate_campaign_preview(encoded(canvas), screens)
