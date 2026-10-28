"""One explicit native planner route and per-call dispatch evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, cast

from .plans import canonical_hash
from .review import DetailReviewError

NATIVE_PLANNER_PROFILES = frozenset(
    {
        "aurora_domestic_intake",
        "aurora_domestic_detail",
        "aurora_domestic_plan_repair",
        "aurora_domestic_modules_completion",
        "aurora_domestic_campaign_completion",
        "aurora_detail_pair_audit",
        "aurora_detail_campaign_audit",
        "aurora_detail_identity_audit",
        "aurora_detail_revision_scope",
    }
)
DISPATCH_VERSION = "native-planner-dispatch-v1"


class PlannerDispatchError(DetailReviewError):
    pass


def route_descriptor(
    *,
    provider: str,
    route: str,
    model: str,
    endpoint: str,
    credential: str,
    kind: str = "http",
) -> dict[str, Any]:
    value = {
        "version": DISPATCH_VERSION,
        "kind": kind,
        "provider": provider,
        "route": route,
        "model": model,
        "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(),
        "credential_sha256": hashlib.sha256(credential.encode()).hexdigest(),
    }
    return require_route({**value, "sha256": canonical_hash(value)})


def require_route(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlannerDispatchError(
            "NATIVE_PLANNER_ROUTE_REQUIRED",
            "Select the actual native planner route before dispatch",
        )
    value = deepcopy(dict(value))
    keys = {
        "version",
        "kind",
        "provider",
        "route",
        "model",
        "endpoint_sha256",
        "credential_sha256",
        "sha256",
    }
    if (
        set(value) != keys
        or value.get("version") != DISPATCH_VERSION
        or value.get("kind") not in {"http", "synthetic"}
        or any(not isinstance(value.get(k), str) or not value[k].strip() for k in keys)
        or value.get("sha256")
        != canonical_hash({k: v for k, v in value.items() if k != "sha256"})
    ):
        raise PlannerDispatchError(
            "NATIVE_PLANNER_ROUTE_INVALID",
            "The exact selected planner route fingerprint is required",
        )
    for name in ("endpoint_sha256", "credential_sha256", "sha256"):
        if len(value[name]) != 64 or any(
            ch not in "0123456789abcdef" for ch in value[name]
        ):
            raise PlannerDispatchError(
                "NATIVE_PLANNER_ROUTE_INVALID",
                "Route fingerprints must be exact SHA256 values",
            )
    return value


def select_route(gateway: Any, bound: object = None) -> dict[str, Any]:
    selector = getattr(gateway, "native_planner_route", None)
    if not callable(selector):
        raise PlannerDispatchError(
            "NATIVE_PLANNER_ROUTE_REQUIRED",
            "The gateway must expose its actual selected planner route",
        )
    route = require_route(selector())
    if bound is not None and canonical_hash(require_route(bound)) != canonical_hash(
        route
    ):
        raise PlannerDispatchError(
            "NATIVE_PLANNER_ROUTE_CHANGED",
            "The selected planner route changed before dispatch",
        )
    return route


def dispatch_metadata(
    route: Mapping[str, Any],
    *,
    attempts: int,
    response_received: bool,
    status: int | None,
    error_code: str | None,
    reported_model: str | None = None,
) -> dict[str, Any]:
    route = require_route(route)
    if route["kind"] == "synthetic":
        outcome = "synthetic_failed" if error_code else "synthetic_succeeded"
    elif attempts == 0:
        outcome = "not_sent"
    elif (
        not response_received or status is None or status >= 500 or status in {408, 409}
    ):
        outcome = "outcome_unknown"
    elif status >= 400:
        outcome = "rejected"
    elif error_code:
        outcome = "response_invalid"
    else:
        outcome = "succeeded"
    return {
        "version": DISPATCH_VERSION,
        "route_sha256": route["sha256"],
        "route": route["route"],
        "routed_model": route["model"],
        "endpoint_sha256": route["endpoint_sha256"],
        "kind": route["kind"],
        "transport_attempts": attempts,
        "response_received": response_received,
        "http_status": status,
        "outcome": outcome,
        "reported_model": reported_model,
    }


def result_with_dispatch(
    value: Mapping[str, Any], route: Mapping[str, Any]
) -> dict[str, Any]:
    """Only an explicit no-network fake may acquire synthetic dispatch evidence."""
    result = deepcopy(dict(value))
    route = require_route(route)
    envelope = dict(result.get("provider_envelope") or {})
    if "native_planner_dispatch" not in envelope and route["kind"] == "synthetic":
        envelope["native_planner_dispatch"] = dispatch_metadata(
            route,
            attempts=0,
            response_received=False,
            status=result.get("http_status"),
            error_code=result.get("error_code"),
        )
        result["provider_envelope"] = envelope
    validate_dispatch_result(result, route)
    return result


def validate_dispatch_result(
    value: Mapping[str, Any], route: Mapping[str, Any]
) -> None:
    route = require_route(route)
    envelope = value.get("provider_envelope")
    dispatch = (
        envelope.get("native_planner_dispatch")
        if isinstance(envelope, Mapping)
        else None
    )
    expected = {
        "version": DISPATCH_VERSION,
        "route_sha256": route["sha256"],
        "route": route["route"],
        "routed_model": route["model"],
        "endpoint_sha256": route["endpoint_sha256"],
        "kind": route["kind"],
    }
    if (
        not isinstance(dispatch, Mapping)
        or canonical_hash({k: dispatch.get(k) for k in expected})
        != canonical_hash(expected)
        or type(dispatch.get("transport_attempts")) is not int
        or dispatch["transport_attempts"] not in (0, 1)
    ):
        raise PlannerDispatchError(
            "NATIVE_PLANNER_DISPATCH_INVALID",
            "The result lacks exact single-route dispatch evidence",
        )
    if type(dispatch.get("response_received")) is not bool:
        raise PlannerDispatchError(
            "NATIVE_PLANNER_DISPATCH_INVALID",
            "Response visibility must be a known boolean",
        )
    if route["kind"] == "synthetic" and dispatch["transport_attempts"] != 0:
        raise PlannerDispatchError(
            "NATIVE_PLANNER_DISPATCH_INVALID",
            "A synthetic fixture cannot claim an HTTP attempt",
        )
    if (
        route["kind"] == "http"
        and dispatch["response_received"]
        and (
            type(dispatch.get("http_status")) is not int
            or not 100 <= dispatch["http_status"] <= 599
        )
    ):
        raise PlannerDispatchError(
            "NATIVE_PLANNER_DISPATCH_INVALID", "The original HTTP status is unknown"
        )
    expected_dispatch = dispatch_metadata(
        route,
        attempts=dispatch["transport_attempts"],
        response_received=dispatch["response_received"],
        status=dispatch.get("http_status"),
        error_code=value.get("error_code"),
        reported_model=dispatch.get("reported_model"),
    )
    if canonical_hash(dispatch) != canonical_hash(expected_dispatch):
        raise PlannerDispatchError(
            "NATIVE_PLANNER_DISPATCH_INVALID",
            "The original dispatch outcome is contradictory",
        )
    if route["kind"] == "http" and value.get("error_code") is None:
        if (
            dispatch["transport_attempts"] != 1
            or dispatch.get("outcome") != "succeeded"
            or dispatch.get("response_received") is not True
        ):
            raise PlannerDispatchError(
                "NATIVE_PLANNER_DISPATCH_INVALID",
                "A successful real planner result needs one known original response",
            )
        raw = value.get("audit_raw_content")
        if not isinstance(raw, str) or not raw.strip():
            raise PlannerDispatchError(
                "NATIVE_PLANNER_RAW_REPLY_MISSING",
                "Preserve the original native planner message",
            )
        payload = raw.encode("utf-8")
        if value.get("content_sha256") != hashlib.sha256(
            payload
        ).hexdigest() or value.get("content_utf8_bytes") != len(payload):
            raise PlannerDispatchError(
                "NATIVE_PLANNER_RAW_REPLY_CHANGED",
                "Original native planner message bytes differ",
            )


def planner_dispatch_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    dispatch = value.get("native_planner_dispatch")
    return deepcopy(dict(dispatch)) if isinstance(dispatch, Mapping) else None


def planning_model(
    gateway: Any,
    skill_id: str,
    *,
    request: Any = None,
    results: Any = (),
) -> str:
    if skill_id != "aurora-domestic-ecommerce-detail-page":
        return cast(str, gateway.planner_model)
    for result in results:
        dispatch = planner_dispatch_record(getattr(result, "provider_envelope", None))
        if dispatch is not None:
            return str(dispatch["routed_model"])
    bound = getattr(request, "native_dispatch", None)
    if bound is not None:
        return str(require_route(bound)["model"])
    return str(select_route(gateway)["model"])


def safe_native_provider_error(
    payload: Mapping[str, Any], secrets: Sequence[str]
) -> dict[str, str]:
    """Retain actionable native HTTP rejection data without credential echoes."""
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return {}
    result = {}
    for field in ("type", "code", "param", "message"):
        value = error.get(field)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        text = str(value)
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        text = re.sub(r"(?i)\bsk-[a-z0-9_-]+", "[redacted]", text)
        text = re.sub(r"(?i)bearer\s+[^\s,;\"<>]+", "Bearer [redacted]", text)
        text = " ".join(text.split())[: 320 if field == "message" else 100]
        if text:
            result[field] = text
    return result
