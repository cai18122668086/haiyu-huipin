"""Original-observation contract for optional whole-campaign model review."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .campaign_jobs import CAMPAIGN_PROFILE
from .plans import canonical_hash


class CampaignObservationError(ValueError):
    code = "NATIVE_DETAIL_CAMPAIGN_OBSERVATION_INVALID"


def campaign_context(request: Any) -> dict[str, Any]:
    value = json.loads(request.product_information[0])
    if not isinstance(value, dict) or value.get("profile") != CAMPAIGN_PROFILE:
        raise CampaignObservationError("Original campaign-review context is missing")
    return value


def campaign_schema(request: Any) -> dict[str, Any]:
    context = campaign_context(request)
    schema = deepcopy(context["native_contract"]["judgment_schema"])
    checks = context["native_contract"]["required_checks"]
    properties = schema["properties"]
    properties["status"] = {
        "enum": ["PASS", "PASS_WITH_RESIDUALS", "INCOMPLETE_OBSERVATION"]
    }
    properties["audit_complete"] = {"type": "boolean"}
    properties["checks"] = {
        "type": "object",
        "required": checks,
        "additionalProperties": False,
        "properties": {
            name: {
                "type": "object",
                "required": ["observation_status", "pass", "visible_evidence"],
                "properties": {
                    "observation_status": {"enum": ["OBSERVED", "UNKNOWN"]},
                    "pass": {"type": ["boolean", "null"]},
                    "visible_evidence": {"type": "string", "minLength": 12},
                },
            }
            for name in checks
        },
    }
    return schema


def campaign_prompt(request: Any) -> str:
    return (
        "Review the actual supplied accepted ecommerce screen pixels as one campaign. "
        "The approved plan and facts are comparison constraints, never observations. "
        "Inspect every ordered screen and adjacent internal seam, "
        "between-pair transitions, "
        "claim recall and actual subject/presentation diversity. Cite exact screen IDs "
        "and visible locations. Give each check its own concrete observed evidence; "
        "never repeat boilerplate across checks. "
        "Preserve known material failures as false. "
        "If a required "
        "property cannot be observed, report UNKNOWN/null and audit_complete=false; "
        "do not invent OCR, pixel evidence, pass fields or a quality acceptance. "
        "Return the complete JSON observation only. Use the original artifact SHA, "
        "auditor and request timestamp supplied in the context for artifact_binding. "
        "No image editing or regeneration is authorized.\n"
        + json.dumps(campaign_context(request), ensure_ascii=False, allow_nan=False)
    )


def validate_campaign_observation(
    raw: object,
    context: Mapping[str, Any],
    *,
    original_text: str | None,
) -> dict[str, Any]:
    if (
        not isinstance(raw, Mapping)
        or not isinstance(original_text, str)
        or not original_text.strip()
    ):
        raise CampaignObservationError("Preserve the original campaign model message")
    from ..xinghe_agent import _planner_content_payload

    if canonical_hash(_planner_content_payload(original_text)) != canonical_hash(raw):
        raise CampaignObservationError(
            "Original campaign reply differs from parsed observations"
        )
    if raw.get("audit_complete") is not True or raw.get("status") not in {
        "PASS",
        "PASS_WITH_RESIDUALS",
    }:
        raise CampaignObservationError("The whole-campaign observation is incomplete")
    required = context["native_contract"]["required_checks"]
    checks = raw.get("checks")
    if not isinstance(checks, Mapping) or set(checks) != set(required):
        raise CampaignObservationError(
            "Campaign review must cover every native requirement"
        )
    for name in required:
        row = checks[name]
        if (
            not isinstance(row, Mapping)
            or row.get("observation_status") != "OBSERVED"
            or type(row.get("pass")) is not bool
            or not isinstance(row.get("visible_evidence"), str)
            or len(row["visible_evidence"].strip()) < 12
        ):
            raise CampaignObservationError(
                "Unknown or missing campaign observations cannot pass: " + name
            )
    if raw["status"] == "PASS" and any(
        checks[name]["pass"] is False for name in required
    ):
        raise CampaignObservationError("PASS contradicts observed material failures")
    binding = raw.get("artifact_binding")
    expected = context["artifact_binding"]
    if not isinstance(binding, Mapping) or any(
        binding.get(k) != expected[k] for k in ("image_sha256", "auditor", "audited_at")
    ):
        raise CampaignObservationError(
            "Campaign observation artifact binding differs from actual request"
        )
    return deepcopy(dict(raw))
