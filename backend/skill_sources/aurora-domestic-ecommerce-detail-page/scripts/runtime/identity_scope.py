"""Truthful comparison scope follows observed presence, never inferred approval."""
from __future__ import annotations
from functools import lru_cache

from .schema_validation import SCHEMA_DIR, validate_schema
from .state import HarnessError, load_json


@lru_cache(maxsize=1)
def identity_contract() -> dict:
    return load_json(SCHEMA_DIR / "identity-observation.schema.json")


def scope_for_presence(presence: object) -> str:
    scope = identity_contract()["x-presence-to-scope"].get(presence) if isinstance(presence, str) else None
    if scope is None:
        raise HarnessError("Observed product presence is unknown; identity scope cannot be inferred")
    return scope


def pair_observed_scope(reports: list[dict]) -> str:
    scopes = []
    for report in reports:
        observed = report.get("observations") or {}
        scope = scope_for_presence(observed.get("product_presence_observed"))
        if (report.get("identity_silhouette") or {}).get("comparison_scope") != scope:
            raise HarnessError("Atomic identity scope contradicts actual observed product presence")
        scopes.append(scope)
    if len(scopes) != 2:
        raise HarnessError("Both atomic observed scopes are required")
    priority = identity_contract()["x-scope-priority"]
    return max(scopes, key=priority.index)


def validate_identity_observation(value: dict, context: str, expected_scope: str | None = None) -> None:
    validate_schema(value, "identity-observation.schema.json", context + " identity observation")
    if expected_scope is not None and value["comparison_scope"] != expected_scope:
        raise HarnessError(context + ": identity scope differs from actual observed presence")
