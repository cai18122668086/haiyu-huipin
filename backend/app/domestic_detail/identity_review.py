"""Scoped identity observations and one bounded, lossless identity follow-up."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator

from ..config import default_skill_root
from .review import DetailReviewError

IDENTITY_KEYS = ("shape_consistent", "colorway_consistent", "texture_consistent")


def identity_schema() -> dict[str, Any]:
    return json.loads(
        (
            default_skill_root("aurora-domestic-ecommerce-detail-page")
            / "schemas/identity-observation.schema.json"
        ).read_bytes()
    )


def identity_complete(value: object, expected_scope: str | None = None) -> bool:
    return (
        isinstance(value, Mapping)
        and not list(Draft202012Validator(identity_schema()).iter_errors(value))
        and (expected_scope is None or value.get("comparison_scope") == expected_scope)
    )


def observed_pair_scope(reports: object) -> str:
    schema = identity_schema()
    try:
        if not isinstance(reports, list) or len(reports) != 2:
            raise ValueError("two atomic observations are required")
        scopes = []
        for report in reports:
            presence = report["observations"]["product_presence_observed"]
            scope = schema["x-presence-to-scope"][presence]
            if not identity_complete(report["identity_silhouette"], scope):
                raise ValueError("atomic scope differs from observed presence")
            scopes.append(scope)
        return str(max(scopes, key=schema["x-scope-priority"].index))
    except (KeyError, TypeError, ValueError) as exc:
        raise DetailReviewError(
            "AURORA_NATIVE_IDENTITY_SCOPE_INVALID",
            "Both module identity/presence observations must be complete",
        ) from exc


def merge_identity_followup(
    initial: Mapping[str, Any], observed: Mapping[str, Any], expected_scope: str
) -> dict[str, Any]:
    if observed.get("audit_complete") is not True:
        raise DetailReviewError(
            "AURORA_NATIVE_IDENTITY_AUDIT_INVALID",
            "The bounded identity review is uncertain",
        )
    value = {
        key: deepcopy(item)
        for key, item in observed.items()
        if key not in {"audit_complete", "uncertainty"}
    }
    if not identity_complete(value, expected_scope):
        raise DetailReviewError(
            "AURORA_NATIVE_IDENTITY_AUDIT_INVALID",
            "The identity follow-up lacks a complete scoped comparison",
        )
    known_scopes = identity_schema()["x-scope-priority"]
    if (
        initial.get("comparison_scope") in known_scopes
        and initial["comparison_scope"] != expected_scope
    ):
        raise DetailReviewError(
            "AURORA_NATIVE_IDENTITY_AUDIT_INVALID",
            "The original comparison scope contradicts actual module presence",
        )
    for key in IDENTITY_KEYS:
        prior = initial.get(key)
        current = value[key]
        if (type(prior) is bool and current == "NOT_APPLICABLE") or (
            prior == "NOT_APPLICABLE" and type(current) is bool
        ):
            raise DetailReviewError(
                "AURORA_NATIVE_IDENTITY_AUDIT_INVALID",
                "A follow-up cannot change original comparison applicability",
            )
        if prior is False:
            value[key] = False
    features = initial.get("visible_features")
    if isinstance(features, list) and all(
        isinstance(item, str) and len(item.strip()) >= 8 for item in features
    ):
        value["visible_features"] = list(
            dict.fromkeys([*features, *value["visible_features"]])
        )
    original_evidence = initial.get("visible_evidence")
    if isinstance(original_evidence, str) and original_evidence.strip():
        value["visible_evidence"] = (
            original_evidence.strip() + " " + value["visible_evidence"]
        )
    if not identity_complete(value, expected_scope):
        raise DetailReviewError(
            "AURORA_NATIVE_IDENTITY_AUDIT_INVALID",
            "Merged identity applicability conflicts with the original observations",
        )
    return value
