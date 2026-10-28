"""One original semantic scope call with durable, zero-model replay."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .campaign_durability import encoded, json_once, replace_derived, sha
from .delivery import _json
from .generation import runtime_source_hash
from .planner_dispatch import (
    dispatch_metadata,
    result_with_dispatch,
    select_route,
    validate_dispatch_result,
)
from .planning_calls import NativePlanningStore, call_binding
from .plans import canonical_hash
from .revision_protocol import (
    SCOPE_PROFILE,
    RevisionScopeError,
    require_context,
    validate_scope_response,
)

if TYPE_CHECKING:
    from ..xinghe_agent import PlannerCallResult

SCOPE_JOURNAL = "local-scope-model-result.json"
JOURNAL_VERSION = "domestic-detail-local-scope-result-v1"


def _journal(store: NativePlanningStore) -> dict[str, Any]:
    value = _json(store.root.parent / SCOPE_JOURNAL)
    if (
        value.get("version") != JOURNAL_VERSION
        or value.get("sha256")
        != canonical_hash({k: v for k, v in value.items() if k != "sha256"})
        or canonical_hash(value.get("binding")) != store.store.sha256
        or value.get("request_file_sha256")
        != sha(store.store.request_path.read_bytes())
        or not isinstance(value.get("result"), dict)
        or value.get("result_sha256") != canonical_hash(value.get("result"))
    ):
        raise RevisionScopeError(
            "NATIVE_DETAIL_REVISION_SCOPE_JOURNAL_CHANGED",
            "Original scope result or request bytes changed",
        )
    return value


def _save_result(store: NativePlanningStore, result: Mapping[str, Any]) -> None:
    path = store.root.parent / SCOPE_JOURNAL
    if path.exists():
        if canonical_hash(_journal(store)["result"]) != canonical_hash(result):
            raise RevisionScopeError(
                "NATIVE_DETAIL_REVISION_SCOPE_JOURNAL_CHANGED",
                "Original scope result cannot be replaced",
            )
        return
    value = {
        "version": JOURNAL_VERSION,
        "binding": dict(store.binding),
        "request_file_sha256": sha(store.store.request_path.read_bytes()),
        "result": dict(result),
        "result_sha256": canonical_hash(result),
        "captured_at": datetime.now(UTC).isoformat(),
    }
    json_once(path, {**value, "sha256": canonical_hash(value)})


def _restore_response(store: NativePlanningStore) -> dict[str, Any]:
    """Only reconstruct derived files from the exact original paid result."""
    journal = _journal(store)
    result = journal["result"]
    journal_sha = sha((store.root.parent / SCOPE_JOURNAL).read_bytes())
    try:
        current = (
            _json(store.store.response_path)
            if store.store.response_path.exists()
            else None
        )
    except (ValueError, OSError):
        current = None
    if (
        isinstance(current, dict)
        and isinstance(current.get("result"), dict)
        and canonical_hash(current["result"]) != canonical_hash(result)
    ):
        raise RevisionScopeError(
            "NATIVE_DETAIL_REVISION_SCOPE_JOURNAL_CHANGED",
            "Saved scope response conflicts with original model journal",
        )
    raw = result.get("audit_raw_content")
    raw_receipt = None
    if isinstance(raw, str):
        payload = raw.encode("utf-8")
        replace_derived(
            store.store.stem.with_suffix(".reply.txt"), payload, journal_sha
        )
        raw_receipt = {"sha256": sha(payload), "bytes": len(payload)}
    try:
        completed = store.store.completed()
        if completed is not None:
            return completed
    except (ValueError, OSError):
        pass
    response = {
        "binding_sha256": store.store.sha256,
        "result": result,
        "result_sha256": canonical_hash(result),
        "response": result.get("payload"),
        "raw_reply": raw_receipt,
        "raw_reply_state": "original_message_utf8"
        if raw_receipt
        else "predecoded_only",
        "completed_at": journal["captured_at"],
        "reconstructed_from_original_journal_sha256": journal_sha,
    }
    replace_derived(store.store.response_path, encoded(response), journal_sha)
    completed = store.store.completed()
    if completed is None:
        raise RevisionScopeError(
            "NATIVE_DETAIL_REVISION_SCOPE_OUTCOME_UNKNOWN",
            "The original scope response cannot be reconstructed",
        )
    return completed


async def run_scope_check(
    *,
    root: Path,
    workspace_id: str,
    job_id: str,
    context: Mapping[str, Any],
    gateway: Any,
    before_dispatch: Callable[[], None],
    account_result: Callable[[PlannerCallResult, str], None],
    before_new_dispatch: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Caller verifies current owner/source, active public lease, cancel and quota."""
    from ..xinghe_agent import (
        PlannerCallResult,
        PlannerRequest,
        _planner_content_payload,
        _planner_prompt,
        _planner_response_format,
        normalize_usage,
    )

    context = require_context(context)
    before_dispatch()
    route = select_route(gateway)
    request = PlannerRequest(
        workspace_id=workspace_id,
        job_id=job_id,
        group_key="local-revision-scope",
        sources=(),
        page_count=2,
        language="zh-CN",
        product_name=context["approved_authority"]["product_name"],
        product_information=(json.dumps(context, ensure_ascii=False),),
        platform_code="domestic-detail",
        size="semantic-scope-only",
        idempotency_key="local-revision-scope-" + job_id,
        domestic_protocol=True,
        planning_profile=SCOPE_PROFILE,
        native_dispatch=route,
    )
    store = NativePlanningStore(
        root / "planner-calls",
        call_binding(
            profile=SCOPE_PROFILE,
            group_key=request.group_key,
            target=context["selected_module_id"],
            logical_key=request.idempotency_key,
            semantics=dict(context),
            prompt=_planner_prompt(request),
            schema=_planner_response_format(request),
            route=route,
            skill_hash=context["skill_content_hash"],
            runtime_hash=runtime_source_hash(),
        ),
    )
    if root.name != job_id or root.is_symlink() or root.is_junction():
        raise RevisionScopeError(
            "NATIVE_DETAIL_REVISION_SCOPE_OWNER_INVALID",
            "The semantic preflight needs its own child workspace",
        )
    for path in store.root.glob("*.request.json"):
        if path != store.store.request_path:
            raise RevisionScopeError(
                "NATIVE_DETAIL_REVISION_SCOPE_CHANGED",
                "One original scope request already exists; never replace i"
                "ts context or route",
            )
    if (root / SCOPE_JOURNAL).exists():
        result_value = _journal(store)["result"]
    elif store.store.request_path.exists():
        raise RevisionScopeError(
            "NATIVE_DETAIL_REVISION_SCOPE_OUTCOME_UNKNOWN",
            "Reconcile the original scope request; do not dispatch another one",
        )
    else:
        before_dispatch()
        if before_new_dispatch is not None:
            before_new_dispatch()
        store.begin()
        try:
            result = await gateway.plan(
                replace(request, idempotency_key=store.wire_key)
            )
        except Exception as exc:
            code = "NATIVE_DETAIL_REVISION_SCOPE_OUTCOME_UNKNOWN"
            result = PlannerCallResult(
                payload=None,
                provider_request_id=None,
                raw_usage=None,
                normalized_usage=normalize_usage(None),
                api_latency_seconds=0,
                actual_cost_micros=None,
                error_code=code,
                error_message="Original scope transport needs reconciliation ("
                + type(exc).__name__
                + ")",
                provider_envelope={
                    "native_planner_dispatch": dispatch_metadata(
                        route,
                        attempts=1 if route["kind"] == "http" else 0,
                        response_received=False,
                        status=None,
                        error_code=code,
                    )
                },
                external_request=route["kind"] == "http",
            )
        result = replace(result, phase=SCOPE_PROFILE)
        result_value = asdict(result)
        # An invalid dispatch envelope is still an original possibly-paid response.
        # Journal and account it before validation; never replace it with another call.
        envelope = dict(result_value.get("provider_envelope") or {})
        if route["kind"] == "synthetic":
            envelope = result_with_dispatch(result_value, route)["provider_envelope"]
        envelope["native_planner_receipt"] = store._stamp()
        result_value["provider_envelope"] = envelope
        _save_result(store, result_value)
    result = PlannerCallResult(**result_value)
    account_result(result, store.store.sha256)
    validate_dispatch_result(result_value, route)
    _restore_response(store)
    if result.error_code:
        dispatch = result_value["provider_envelope"]["native_planner_dispatch"]
        code = (
            "NATIVE_DETAIL_REVISION_SCOPE_OUTCOME_UNKNOWN"
            if dispatch["outcome"] in {"outcome_unknown", "response_invalid"}
            else "NATIVE_DETAIL_REVISION_SCOPE_REJECTED"
        )
        raise RevisionScopeError(code, result.error_message or result.error_code)
    if (
        not isinstance(result.audit_raw_content, str)
        or not result.audit_raw_content.strip()
        or canonical_hash(_planner_content_payload(result.audit_raw_content))
        != canonical_hash(result.payload)
    ):
        raise RevisionScopeError(
            "NATIVE_DETAIL_REVISION_SCOPE_RAW_REPLY_INVALID",
            "The original scope model text is required and must match i"
            "ts parsed observation",
        )
    verdict = validate_scope_response(result.payload, request)
    value = {
        **verdict,
        "binding_sha256": store.store.sha256,
        "request_file_sha256": sha(store.store.request_path.read_bytes()),
        "original_journal_file_sha256": sha((root / SCOPE_JOURNAL).read_bytes()),
        "response_file_sha256": sha(store.store.response_path.read_bytes()),
        "original_message_sha256": sha(result.audit_raw_content.encode("utf-8")),
    }
    json_once(root / "local-revision-scope.json", value)
    if not verdict["accepted_local_scope"]:
        raise RevisionScopeError(
            "NATIVE_DETAIL_REVISION_REPLAN_REQUIRED",
            "The original change is global or uncertain; review a new p"
            "lan before image generation",
        )
    return value


def scope_billing_observation(
    root: Path, binding_sha256: str, result: Any
) -> dict[str, Any]:
    """A validated HTTP not-sent trace has zero requests and zero billing."""
    journal_value = _json(root / SCOPE_JOURNAL)
    store = NativePlanningStore(root / "planner-calls", journal_value["binding"])
    journal = _journal(store)
    result_value = asdict(result)
    if store.store.sha256 != binding_sha256 or canonical_hash(
        journal["result"]
    ) != canonical_hash(result_value):
        raise RevisionScopeError(
            "NATIVE_DETAIL_REVISION_SCOPE_JOURNAL_CHANGED",
            "Original scope billing must match its exact request and result",
        )
    route = store.binding["dispatch_route"]
    dispatch = (result.provider_envelope or {}).get("native_planner_dispatch") or {}
    not_sent = False
    try:
        validate_dispatch_result(result_value, route)
    except ValueError:
        # An invalid envelope may still describe a paid request. Never turn it
        # into a zero charge merely because its self-reported flags say so.
        pass
    else:
        not_sent = (
            route["kind"] == "http"
            and dispatch["outcome"] == "not_sent"
            and dispatch["transport_attempts"] == 0
            and result.external_request is False
            and bool(result.error_code)
            and not result.supplemental_results
            and (
                result.actual_cost_micros is None
                or type(result.actual_cost_micros) is int
                and result.actual_cost_micros == 0
            )
        )
    return {
        "version": "domestic-detail-scope-billing-v1",
        "binding_sha256": binding_sha256,
        "original_journal_file_sha256": sha((root / SCOPE_JOURNAL).read_bytes()),
        "original_result_sha256": canonical_hash(journal["result"]),
        "dispatch_route_sha256": route["sha256"],
        "state": "not_sent" if not_sent else "potentially_sent",
        "provider_request_count": 0 if not_sent else 1,
        "charged_cost_micros": 0 if not_sent else None,
    }
