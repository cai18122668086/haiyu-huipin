"""Exact source-scoped quote and human authority for a new local revision."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import JobRecord
from ..storage import ImageStorage
from .campaign_jobs import source_scope
from .delivery import _json
from .generation import runtime_source_hash
from .planner_dispatch import require_route, route_descriptor, select_route
from .plans import DetailPlanError, canonical_hash

QUOTE_VERSION = "domestic-detail-local-revision-quote-v1"
AUTH_VERSION = "domestic-detail-local-revision-authorization-v1"
REVISION_APPROVAL_TEXT = (
    "我已审阅原方案、原图、本次局部修改范围和最高费用，"
    "明确授权创建独立双屏修订任务，其余屏幕保留，生成后等待我审阅确认。"
)
REVISION_DISCLOSURE = (
    "本次双屏最多两次图片请求：初稿及一次自动修复；"
    "局部范围预检模型最多一次，像素审核模型最多四次，包含有界身份补充核验。"
    "原任务、原图和原费用保留；全局变更须重新策划。"
    "请求结果未知时先核对原请求，不以新周期补发。"
)


def revision_policy_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    return canonical_hash(
        {
            "revision_authorization": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "request_schema": hashlib.sha256(
                (root / "schemas_v1.py").read_bytes()
            ).hexdigest(),
        }
    )


def normalize_adjustment(value: Mapping[str, Any]) -> dict[str, Any]:
    from ..schemas_v1 import DomesticDetailLocalRevisionDelta

    result = DomesticDetailLocalRevisionDelta.model_validate(dict(value)).model_dump()
    text = result["verbatim_instruction"].strip().strip("。.!！ ").casefold()
    if text in {"continue", "retry", "继续", "重试", "重新生成"}:
        raise DetailPlanError("A local revision needs an explicit named change")
    for key in ("change_only", "preserve", "must_not_regress"):
        if any(not text.strip() for text in result[key]):
            raise DetailPlanError(
                "Every local change and preservation duty must be explicit"
            )
    if not result["verbatim_instruction"].strip():
        raise DetailPlanError("Original local change instructions are required")
    return result


def image_dispatch(gateway: Any) -> dict[str, Any]:
    """Fingerprint the actual primary image account without exposing its secret."""
    review = select_route(gateway)
    selector = getattr(gateway, "_image_routes", None)
    if callable(selector):
        routes = selector()
        if len(routes) != 1:
            raise DetailPlanError("A local revision requires one exact image route")
        route = routes[0]
        return route_descriptor(
            provider=gateway.provider_name,
            route=route.name,
            model=route.image_model,
            endpoint=route.image_base_url,
            credential=route.image_api_key,
        )
    if review["kind"] != "synthetic":
        raise DetailPlanError("The actual image route is unavailable")
    return route_descriptor(
        provider=gateway.provider_name,
        route="offline-synthetic-image",
        model=gateway.image_model,
        endpoint="offline://synthetic-image",
        credential="no-external-image-credential",
        kind="synthetic",
    )


def _price(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise DetailPlanError(
            "Current revision prices must be explicit nonnegative integers"
        )
    return value


def quote_value(
    *,
    source: JobRecord,
    plan_id: str,
    plan_revision: int,
    plan_payload: Mapping[str, Any],
    context: Mapping[str, Any],
    state: Mapping[str, Any],
    packet: Mapping[str, Any],
    source_session_sha256: str,
    adjustment: Mapping[str, Any],
    actor_id: str,
    image_route: Mapping[str, Any],
    review_route: Mapping[str, Any],
    image_unit_micros: int,
    review_reserve_micros: int,
    pricing_version: str,
) -> dict[str, Any]:
    delta = normalize_adjustment(adjustment)
    image_route, review_route = require_route(image_route), require_route(review_route)
    if image_route["kind"] != review_route["kind"]:
        raise DetailPlanError(
            "Image and review routes must share real or synthetic scope"
        )
    modules = state["modules"]
    if len(modules) not in {8, 10, 12} or len(modules) != source.expected_item_count:
        raise DetailPlanError("The original campaign screen count changed")
    pairs = state["plan"]["output_strategy"]["continuous_pair_rendering"]["pair_ids"]
    candidates = [pair for pair in pairs if delta["module_id"] in pair["module_ids"]]
    if len(candidates) != 1:
        raise DetailPlanError(
            "Select one existing screen in its approved adjacent pair"
        )
    pair = candidates[0]
    positions = [
        i for i, module in enumerate(modules) if module["id"] in pair["module_ids"]
    ]
    if (
        len(positions) != 2
        or positions[0] % 2
        or positions[1] != positions[0] + 1
        or [modules[i]["id"] for i in positions] != pair["module_ids"]
    ):
        raise DetailPlanError(
            "The local revision pair differs from the original sequence"
        )
    screens = [context["screens"][i] for i in positions]
    if [row["module_id"] for row in screens] != pair["module_ids"]:
        raise DetailPlanError(
            "The accepted source screens differ from the selected pair"
        )
    canvases = [
        row for row in packet["pair_canvases"] if row["pair_id"] == pair["pair_id"]
    ]
    if len(canvases) != 1 or canvases[0]["module_ids"] != pair["module_ids"]:
        raise DetailPlanError("The original accepted pair master changed")
    canvas = canvases[0]
    image_price, review_price = _price(image_unit_micros), _price(review_reserve_micros)
    maximum = image_price * 2 + review_price * 5
    value = {
        "version": QUOTE_VERSION,
        "source_job_id": source.id,
        "workspace_id": source.workspace_id,
        "actor_user_id": actor_id,
        "source_plan_id": plan_id,
        "source_plan_revision": plan_revision,
        "source_plan_payload_sha256": canonical_hash(plan_payload),
        "final_review_sha256": packet["sha256"],
        "source_context_sha256": canonical_hash(context),
        "source_native_session_sha256": source_session_sha256,
        "source_generation_binding_sha256": context["generation_binding_sha256"],
        "native_approval_document_sha256": plan_payload["native_approval"][
            "document_sha256"
        ],
        "original_approval_receipt_sha256": canonical_hash(
            plan_payload["aurora_v4_3"]["approval_receipt"]
        ),
        "source_approved_scope_sha256": plan_payload["aurora_v4_3"]["contract_binding"][
            "sha256"
        ],
        "skill_content_hash": plan_payload["native_approval"]["skill_content_hash"],
        "runtime_source_sha256": runtime_source_hash(),
        "revision_policy_sha256": revision_policy_hash(),
        "approved_screen_count": len(modules),
        "selected_module_id": delta["module_id"],
        "pair_id": pair["pair_id"],
        "module_ids": list(pair["module_ids"]),
        "source_screens": [
            {
                key: row[key]
                for key in (
                    "module_id",
                    "asset_id",
                    "ordinal",
                    "sha256",
                    "width",
                    "height",
                )
            }
            for row in screens
        ],
        "source_pair_canvas": {
            key: canvas[key]
            for key in ("asset_id", "sha256", "width", "height", "pixel_lineage_sha256")
        },
        "source_quality_state": packet["quality_state"],
        "source_residual_issues": deepcopy(packet["residual_issues"]),
        "adjustment": delta,
        "image_dispatch_sha256": image_route["sha256"],
        "review_dispatch_sha256": review_route["sha256"],
        "image_provider": image_route["provider"],
        "image_model": image_route["model"],
        "review_model": review_route["model"],
        "dispatch_kind": image_route["kind"],
        "pricing_version": pricing_version,
        "image_unit_micros": image_price,
        "review_reserve_micros": review_price,
        "maximum_image_requests": 2,
        "maximum_scope_model_requests": 1,
        "maximum_review_model_requests": 4,
        "maximum_model_requests": 5,
        "maximum_provider_requests": 7,
        "maximum_cost_micros": maximum,
        "currency": "USD",
        "delivery_mode": "USER_FINAL_REVIEW",
        "authorization_text": REVISION_APPROVAL_TEXT,
        "disclosure": REVISION_DISCLOSURE,
    }
    return {**value, "sha256": canonical_hash(value)}


async def build_revision_quote(
    db: Session,
    source: JobRecord,
    storage: ImageStorage,
    settings: Settings,
    gateway: Any,
    *,
    fingerprint: str,
    adjustment: Mapping[str, Any],
    actor_id: str,
) -> dict[str, Any]:
    delta = normalize_adjustment(adjustment)
    if source.type != "detail_pack" or source.status not in {
        "awaiting_final_review",
        "completed",
    }:
        raise DetailPlanError(
            "The original detail campaign is not ready for a local revision"
        )
    plan, context, state, root, packet = await source_scope(
        db, source, storage, fingerprint
    )
    binding = _json(root / "generation-binding.json")
    if binding["sha256"] != context["generation_binding_sha256"]:
        raise DetailPlanError(
            "Original generation binding differs from accepted source"
        )
    return quote_value(
        source=source,
        plan_id=plan.id,
        plan_revision=plan.revision,
        plan_payload=plan.payload,
        context=context,
        state=state,
        packet=packet,
        source_session_sha256=hashlib.sha256(
            (root / "session.json").read_bytes()
        ).hexdigest(),
        adjustment=delta,
        actor_id=actor_id,
        image_route=image_dispatch(gateway),
        review_route=select_route(gateway),
        image_unit_micros=gateway.high_price_micros,
        review_reserve_micros=settings.tengli_planner_reserve_micros,
        pricing_version=gateway.pricing_version,
    )


def authorize_revision_quote(
    current_quote: Mapping[str, Any],
    *,
    submitted_sha256: str,
    approval_text: str,
    source_job_id: str,
    workspace_id: str,
    actor_id: str,
) -> dict[str, Any]:
    """Caller must recompute current_quote from source_scope, never a client body."""
    value = deepcopy(dict(current_quote))
    if (
        value.get("version") != QUOTE_VERSION
        or value.get("sha256")
        != canonical_hash({k: v for k, v in value.items() if k != "sha256"})
        or submitted_sha256 != value.get("sha256")
        or value.get("source_job_id") != source_job_id
        or value.get("workspace_id") != workspace_id
        or value.get("actor_user_id") != actor_id
        or value.get("runtime_source_sha256") != runtime_source_hash()
        or value.get("revision_policy_sha256") != revision_policy_hash()
        or value.get("delivery_mode") != "USER_FINAL_REVIEW"
        or approval_text != REVISION_APPROVAL_TEXT
    ):
        raise DetailPlanError(
            "Current local revision quote and explicit human authority are required"
        )
    authority = {
        "version": AUTH_VERSION,
        "source_job_id": source_job_id,
        "workspace_id": workspace_id,
        "actor_user_id": actor_id,
        "quote": value,
        "quote_sha256": value["sha256"],
        "approval_text": approval_text,
    }
    return {**authority, "sha256": canonical_hash(authority)}
