"""Full-plan and identity invariants for the independent fast wire contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app import amazon_suite_fast as fast
from app.amazon_visual_suite_provider import AMAZON_ITEM_KEYS, AMAZON_ITEM_SPECS
from app.xinghe_agent import (
    PlannerRequest,
    SourceImage,
    TengliOpenAICompatibleGateway,
    XingheAgentError,
)


def request() -> PlannerRequest:
    return PlannerRequest(
        workspace_id="workspace-fast",
        job_id="job-fast",
        group_key="amazon-product",
        sources=(
            SourceImage(
                asset_id="product-primary",
                group_key="amazon-product",
                filename="product.jpg",
                content_type="image/jpeg",
                sha256="a" * 64,
                payload=b"",
                source_role="white_background_product",
            ),
            SourceImage(
                asset_id="competitor",
                group_key="amazon-product",
                filename="reference.jpg",
                content_type="image/jpeg",
                sha256="b" * 64,
                payload=b"",
                source_role="competitor_reference",
            ),
        ),
        page_count=21,
        language="en-US",
        product_name="Visible product",
        product_information=("HARD CONSTRAINT - forbidden claim: laboratory results",),
        platform_code="amazon-us",
        size="amazon-canonical-multi-size-v1",
        idempotency_key="job-fast:planner:frozen",
        planning_profile=fast.PROFILE,
    )


def test_complete_compact_plan_expands_only_canonical_metadata() -> None:
    req = request()
    raw = fast.fake_planner_payload(req)
    frozen = deepcopy(raw)
    assert Draft202012Validator(fast.planner_json_schema()).is_valid(raw)
    result = fast.validate_planner_payload(raw, sources=req.sources)
    assert raw == frozen
    assert result["analysis"] == raw["analysis"]
    assert [row["item_key"] for row in result["plan"]["screens"]] == list(
        AMAZON_ITEM_KEYS
    )
    for authored, row, spec in zip(
        raw["plan"]["screens"],
        result["plan"]["screens"],
        AMAZON_ITEM_SPECS,
        strict=True,
    ):
        assert all(row[name] == value for name, value in authored.items())
        assert row["final_size"] == f"{spec.width}x{spec.height}"
        assert row["asset_type"] == spec.asset_type


@pytest.mark.parametrize("count", [0, 6, 20, 22])
def test_no_partial_or_oversized_plan_can_be_repaired_into_success(count: int) -> None:
    raw = fast.fake_planner_payload(request())
    rows = raw["plan"]["screens"]
    raw["plan"]["screens"] = (rows + rows)[:count]
    with pytest.raises(XingheAgentError, match="complete fast suite schema"):
        fast.validate_planner_payload(raw)


def test_duplicate_or_out_of_order_inventory_is_rejected() -> None:
    raw = fast.fake_planner_payload(request())
    raw["plan"]["screens"][5]["id"] = raw["plan"]["screens"][4]["id"]
    with pytest.raises(XingheAgentError) as caught:
        fast.validate_planner_payload(raw)
    assert caught.value.code == "AMAZON_ITEM_INVENTORY_INVALID"


def test_missing_design_is_not_replaced_with_a_template() -> None:
    raw = fast.fake_planner_payload(request())
    del raw["plan"]["screens"][20]["layout_direction"]
    with pytest.raises(XingheAgentError) as caught:
        fast.validate_planner_payload(raw)
    assert caught.value.json_pointer == "/plan/screens/20"


def test_competitor_cannot_be_primary_product_identity() -> None:
    req = request()
    raw = fast.fake_planner_payload(req)
    raw["analysis"]["primary_product_source_id"] = "competitor"
    with pytest.raises(XingheAgentError) as caught:
        fast.validate_planner_payload(raw, sources=req.sources)
    assert caught.value.code == "AMAZON_FAST_PRIMARY_SOURCE_INVALID"


def test_main_cannot_add_on_image_copy() -> None:
    raw = fast.fake_planner_payload(request())
    raw["plan"]["screens"][0]["headline"] = "Best choice"
    with pytest.raises(XingheAgentError) as caught:
        fast.validate_planner_payload(raw)
    assert caught.value.code == "AMAZON_FAST_MAIN_COPY_INVALID"


def test_mobile_requires_its_own_composition() -> None:
    raw = fast.fake_planner_payload(request())
    raw["plan"]["screens"][14]["layout_direction"] = raw["plan"]["screens"][7][
        "layout_direction"
    ]
    with pytest.raises(XingheAgentError) as caught:
        fast.validate_planner_payload(raw)
    assert caught.value.code == "AMAZON_FAST_RESPONSIVE_LAYOUT_INVALID"


def test_unsupported_claims_do_not_enter_deliverable_copy() -> None:
    raw = fast.fake_planner_payload(request())
    raw["analysis"]["information_confidence"]["needs_confirmation"] = ["Lab tested"]
    raw["plan"]["screens"][10]["headline"] = "Lab tested"
    with pytest.raises(XingheAgentError) as caught:
        fast.validate_planner_payload(raw)
    assert caught.value.code == "AMAZON_UNSUPPORTED_CLAIM_INVALID"


def test_fast_manifest_locks_full_copy_without_altering_original() -> None:
    backend = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (backend / "app" / "skill_manifests" / f"{fast.SKILL_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    root = backend / "skill_sources" / fast.SKILL_ID
    for lock in manifest["files"]:
        assert (
            hashlib.sha256((root / lock["path"]).read_bytes()).hexdigest()
            == lock["sha256"]
        )
    original = backend / "skill_sources" / "xinghe-amazon-visual-suite"
    for path in original.rglob("*"):
        if path.is_file() and path.name not in {"SKILL.md", "openai.yaml"}:
            assert path.read_bytes() == (root / path.relative_to(original)).read_bytes()
    assert manifest["policy"]["auto_review_regeneration_allowed"] is False
    assert manifest["policy"]["successful_page_auto_redraw_allowed"] is False
    assert manifest["policy"]["policy_version"] == fast.POLICY_VERSION


def test_fast_selection_handles_durable_and_direct_settings() -> None:
    assert fast.selected({"amazon_speed": "fast"})
    assert fast.selected({"settings": {"amazon_speed": "fast"}})
    assert not fast.selected({})
    assert not fast.selected({"settings": {"amazon_speed": "standard"}})
    # A durable envelope cannot be overridden by an unrelated top-level field.
    assert not fast.selected({"amazon_speed": "fast", "settings": {}})


class PlannerTransport:
    def __init__(self, *, status: int = 200, finish_reason: str = "stop") -> None:
        self.status, self.finish_reason = status, finish_reason
        self.calls: list[dict[str, Any]] = []

    async def post_json(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        self.calls.append(kwargs)
        if self.status >= 400:
            return self.status, {"error": {"message": "Synthetic unavailable route"}}
        raw = fast.fake_planner_payload(request())
        # Exercise a complete long response instead of only a tiny fixture.
        for index, row in enumerate(raw["plan"]["screens"]):
            row["layout_direction"] = (
                f"Canvas {index}: subject at left, copy at right, evidence at bottom. "
                * 15
            )
        return 200, {
            "id": "req-fast-long-output",
            "choices": [
                {
                    "finish_reason": self.finish_reason,
                    "message": {"content": json.dumps(raw)},
                }
            ],
            "usage": {
                "prompt_tokens": 4500,
                "completion_tokens": 15000,
                "total_tokens": 19500,
                "completion_tokens_details": {"reasoning_tokens": 3000},
            },
        }

    async def post_multipart(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        raise AssertionError("Planning must not submit image calls")


def planner_gateway(transport: PlannerTransport) -> TengliOpenAICompatibleGateway:
    return TengliOpenAICompatibleGateway(
        api_key="synthetic-primary",
        base_url="https://planner.example",
        planner_model="gpt-5.6-sol",
        image_model="gpt-image-2.5-flare",
        failover_enabled=True,
        failover_api_key="synthetic-backup",
        failover_base_url="https://backup.example",
        transport=transport,
    )


def test_gateway_accepts_complete_long_21_plan_in_one_call() -> None:
    transport = PlannerTransport()
    gateway = planner_gateway(transport)
    result = asyncio.run(gateway.plan(request()))
    assert result.error_code is None
    validated = fast.validate_planner_payload(result.payload, sources=request().sources)
    assert len(validated["plan"]["screens"]) == 21
    assert len(json.dumps(result.payload)) > 12000
    assert len(transport.calls) == gateway.external_network_request_count == 1
    sent = transport.calls[0]["payload"]
    assert sent["model"] == "gpt-5.6-sol"
    assert sent["max_completion_tokens"] >= 24000
    assert (
        sent["response_format"]["json_schema"]["schema"] == fast.planner_json_schema()
    )


@pytest.mark.parametrize("status", [403, 429, 503])
def test_gateway_failure_does_not_replan_through_another_route(status: int) -> None:
    transport = PlannerTransport(status=status)
    gateway = planner_gateway(transport)
    result = asyncio.run(gateway.plan(request()))
    assert result.error_code is not None and result.payload is None
    assert len(transport.calls) == gateway.external_network_request_count == 1
    assert transport.calls[0]["url"] == "https://planner.example/v1/chat/completions"


def test_gateway_length_is_failure_even_when_json_is_closed_and_has_21_rows() -> None:
    transport = PlannerTransport(finish_reason="length")
    gateway = planner_gateway(transport)
    result = asyncio.run(gateway.plan(request()))
    assert result.error_code == "PLANNER_OUTPUT_TRUNCATED"
    assert result.payload is None
    assert result.normalized_usage["finish_reason"] == "length"
    assert result.normalized_usage["output_token_limit"] >= 24000
    assert len(transport.calls) == gateway.external_network_request_count == 1


@pytest.mark.parametrize("edit", ["missing_primary", "main_copy", "mobile_layout"])
def test_edited_expanded_plan_retains_fast_truth_contract(edit: str) -> None:
    req = request()
    payload = fast.validate_planner_payload(
        fast.fake_planner_payload(req), sources=req.sources
    )
    if edit == "missing_primary":
        del payload["analysis"]["primary_product_source_id"]
    elif edit == "main_copy":
        payload["plan"]["screens"][0]["headline"] = "Invented main copy"
    else:
        payload["plan"]["screens"][14]["layout_direction"] = payload["plan"]["screens"][
            7
        ]["layout_direction"]
    with pytest.raises(XingheAgentError):
        fast.validate_expanded_payload(payload, sources=req.sources)


def test_expanded_plan_round_trip_preserves_all_authored_fields() -> None:
    req = request()
    payload = fast.validate_planner_payload(
        fast.fake_planner_payload(req), sources=req.sources
    )
    assert fast.validate_expanded_payload(payload, sources=req.sources) == payload
    payload["analysis"]["primary_product_source_id"] = "competitor"
    with pytest.raises(XingheAgentError) as caught:
        fast.validate_expanded_payload(payload, sources=req.sources)
    assert caught.value.code == "AMAZON_FAST_PRIMARY_SOURCE_INVALID"


def test_expanded_plan_cannot_reset_an_edited_target_size_as_success() -> None:
    req = request()
    payload = fast.validate_planner_payload(
        fast.fake_planner_payload(req), sources=req.sources
    )
    payload["plan"]["screens"][20]["final_size"] = "1200x1600"
    with pytest.raises(XingheAgentError) as caught:
        fast.validate_expanded_payload(payload, sources=req.sources)
    assert caught.value.code == "AMAZON_TARGET_SIZE_MISMATCH"
