"""Verify accepted audits against every original immutable provider message."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, NoReturn

from ..skill_runtime import SkillManifestRegistry
from .identity_review import (
    identity_complete,
    merge_identity_followup,
    observed_pair_scope,
)
from .pixels import pixel_review_context
from .planner_dispatch import require_route, validate_dispatch_result
from .plans import canonical_hash
from .review import (
    DetailReviewError,
    ReviewStore,
    approved_review_authority,
    normalize_pair_observation,
    require_review_authority,
    validate_pair_before_identity,
)

REVIEW_BINDING_VERSION = "native-pixel-review-v4"


def review_runtime_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    return canonical_hash(
        {
            "adapter": hashlib.sha256(
                (root / "xinghe_agent.py").read_bytes()
            ).hexdigest(),
            **{
                name: hashlib.sha256(
                    (root / "domestic_detail" / (name + ".py")).read_bytes()
                ).hexdigest()
                for name in (
                    "review",
                    "identity_review",
                    "review_provenance",
                    "planner_dispatch",
                    "revision_review",
                    "revision_runtime",
                    "revision_accounts",
                    "website_review",
                )
            },
        }
    )


def pair_review_contract(
    native: Mapping[str, Any],
    candidate_sha: str,
    width: int,
    height: int,
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    authority = require_review_authority(native.get("review_authority"))
    value = {
        **(
            {"review_profile": native["review_profile"]}
            if "review_profile" in native
            else {}
        ),
        "pair_id": native.get("pair_id"),
        "module_ids": native.get("module_ids"),
        "pair_render": native.get("pair_render"),
        "output_surface": native.get("output_surface"),
        "product_name": authority["product_name"],
        "product_information": authority["product_information"],
        "review_authority": authority,
        "approved_module_contracts": list(
            native.get("approved_module_contracts") or []
        ),
        "pixel_lineage": pixel_review_context(native["pixel_lineage"]),
        "audit_context_version": "aurora-v4.3-pair-audit-v5",
        "candidate_sha256": candidate_sha,
        "candidate_dimensions": [width, height],
        "source_inventory": inventory,
        "native_request_sha256": canonical_hash(native),
        "approved_module_contracts_sha256": canonical_hash(
            native.get("approved_module_contracts")
        ),
    }

    if "review_transport_version" in native:
        value["review_transport_version"] = native["review_transport_version"]
    from .revision_review import baseline_inventory, local_model_context

    local = local_model_context(native)
    if local is not None:
        expected = baseline_inventory(native)
        attached = [
            row
            for row in inventory
            if str(row.get("role", "")).startswith("local_revision_original_")
        ]
        if attached != expected:
            _invalid("Actual original canvas and both fixed slices must be attached")
        value["local_revision_context"] = local
    return value


def _invalid(reason: str) -> NoReturn:
    raise DetailReviewError("NATIVE_DETAIL_REVIEW_PROVENANCE_INVALID", reason)


def _original_file(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        _invalid("Original review file reference is missing")
    path = root / str(relative)
    if (
        Path(str(relative)).is_absolute()
        or path.parent != root / "reviews"
        or path.is_symlink()
        or path.parent.is_symlink()
        or path.parent.is_junction()
        or not path.resolve().is_relative_to(root.resolve())
    ):
        _invalid("Original review reference escaped its exact render workspace")
    return path


def verify_review_provenance(
    root: Path,
    invocation: Mapping[str, Any],
    lineage: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    """Read only; a missing or uncertain original never permits another call."""
    try:
        _verify_review_provenance(root, invocation, lineage, evidence)
    except DetailReviewError:
        raise
    except (KeyError, ValueError, TypeError, OSError) as exc:
        raise DetailReviewError(
            "NATIVE_DETAIL_REVIEW_PROVENANCE_INVALID",
            "Original accepted review evidence is missing, changed or unreadable",
        ) from exc


def _verify_review_provenance(
    root: Path,
    invocation: Mapping[str, Any],
    lineage: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    raw = evidence["audit_input"]
    references = raw.get("review_provenance")
    if references is None:
        inspection = raw["artifact_binding"]["visual_inspection_output"]
        synthetic = isinstance(inspection, dict) and (
            inspection.get("mode") == "SYNTHETIC_OFFLINE_FIXTURE"
            or inspection.get("fixture") == "synthetic"
        )
        if invocation.get("provider") in {"mock", "synthetic"} and synthetic:
            return
        _invalid(
            "A real accepted canvas requires its original provider review messages"
        )
    phases = (
        [row.get("phase") for row in references] if isinstance(references, list) else []
    )
    if phases not in (
        ["aurora_detail_pair_audit"],
        ["aurora_detail_pair_audit", "aurora_detail_identity_audit"],
    ):
        _invalid("Expected one original pair report and at most one identity follow-up")
    saved: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for row in references:
        request = _original_file(root, row.get("request_file"))
        response = _original_file(root, row.get("response_file"))
        if (
            hashlib.sha256(request.read_bytes()).hexdigest()
            != row["request_file_sha256"]
            or hashlib.sha256(response.read_bytes()).hexdigest()
            != row["response_file_sha256"]
        ):
            _invalid("Cited original review request or response bytes changed")
        binding = json.loads(request.read_bytes())["binding"]
        store = ReviewStore(root / "reviews", binding)
        if (
            store.request_path != request
            or store.response_path != response
            or store.sha256 != row["binding_sha256"]
        ):
            _invalid(
                "Cited review files do not belong to their canonical original binding"
            )
        reply = store.stem.with_suffix(".reply.txt")
        if reply.is_symlink() or not reply.resolve().is_relative_to(root.resolve()):
            _invalid("Original provider message escaped its render workspace")
        result = store.completed()
        if not isinstance(result, dict) or result.get("error_code") is not None:
            _invalid(
                "An accepted audit cannot reuse an uncertain or failed original review"
            )
        original = result.get("audit_raw_content")
        if (
            not isinstance(original, str)
            or not original.strip()
            or hashlib.sha256(original.encode()).hexdigest()
            != row["original_message_sha256"]
        ):
            _invalid("The exact original provider message is missing or changed")
        bindings.append(binding)
        saved.append(result)

    # Lazy import avoids the provider adapter importing itself at module load.
    from ..xinghe_agent import (
        PlannerRequest,
        _planner_content_payload,
        _planner_prompt,
        _planner_response_format,
    )

    for result in saved:
        if canonical_hash(
            _planner_content_payload(result["audit_raw_content"])
        ) != canonical_hash(result["payload"]):
            _invalid("Original model text differs from the saved parsed observation")
    native = deepcopy(invocation["native_request"])
    native["pixel_lineage"] = pixel_review_context(lineage)
    authority = approved_review_authority(
        json.loads((root / "session.json").read_bytes())
    )
    if canonical_hash(native.get("review_authority")) != canonical_hash(authority):
        _invalid("Original review authority differs from the exact compiled approval")
    inventory = [
        {key: row[key] for key in ("asset_id", "role", "sha256")}
        for row in invocation["source_assets"]
    ]
    inventory += [
        {
            "asset_id": f"native-slice:{row['module_id']}:{row['sha256']}",
            "role": "generated_module_candidate:" + row["module_id"],
            "sha256": row["sha256"],
        }
        for row in lineage["slices"]
    ]
    from .revision_review import original_revision_sources

    original_sources = original_revision_sources(native)
    inventory += [
        {
            "asset_id": source.asset_id,
            "role": source.source_role,
            "sha256": source.sha256,
        }
        for source in original_sources
    ]
    inventory.append(
        {
            "asset_id": "generated:" + lineage["canvas"]["sha256"],
            "role": "generated_pair_candidate",
            "sha256": lineage["canvas"]["sha256"],
        }
    )
    contract = pair_review_contract(
        native,
        lineage["canvas"]["sha256"],
        lineage["canvas"]["width_px"],
        lineage["canvas"]["height_px"],
        inventory,
    )
    expected = {
        "version": REVIEW_BINDING_VERSION,
        "approved_review_authority_sha256": authority["sha256"],
        "render_token": native["token"],
        "candidate_sha256": lineage["canvas"]["sha256"],
        "native_request_sha256": canonical_hash(native),
        "approved_module_contracts_sha256": canonical_hash(
            native["approved_module_contracts"]
        ),
        "source_inventory": inventory,
        "provider": invocation["provider"],
        "skill_content_hash": SkillManifestRegistry.bundled()
        .get("aurora-domestic-ecommerce-detail-page")
        .content_hash,
        "review_runtime_sha256": review_runtime_hash(),
    }
    if native.get("review_profile") == "WEBSITE_LEAN_V1":
        # Verify original runtime provenance as original, then revalidate its
        # unchanged pixel observations with the current six-check contract.
        expected.pop("review_runtime_sha256")
        if any(
            not isinstance(row.get("review_runtime_sha256"), str)
            or len(row["review_runtime_sha256"]) != 64
            for row in bindings
        ):
            _invalid("Original website review runtime fingerprint is missing")
    initial = deepcopy(saved[0]["payload"])
    if native.get("review_profile") == "WEBSITE_LEAN_V1":
        from .website_review import bind_pixel_metadata

        initial = bind_pixel_metadata(
            initial,
            native,
            lineage["canvas"]["sha256"],
            lineage["canvas"]["width_px"],
            lineage["canvas"]["height_px"],
        )
    validate_pair_before_identity(
        initial,
        native,
        image_sha256=lineage["canvas"]["sha256"],
        width=lineage["canvas"]["width_px"],
        height=lineage["canvas"]["height_px"],
    )
    scope = observed_pair_scope(initial["module_judgments"])
    needs_identity = not identity_complete(initial.get("identity_silhouette"), scope)
    if needs_identity != (len(saved) == 2):
        _invalid("Identity follow-up differs from the original incomplete comparison")
    identity_context = {
        "pair_id": contract["pair_id"],
        "product_name": contract["product_name"],
        "candidate_sha256": lineage["canvas"]["sha256"],
        "source_inventory": inventory,
        "locked_identity": authority["locked_identity"],
        "review_authority_sha256": authority["sha256"],
        "expected_observed_comparison_scope": scope,
        "module_identity_observations": [
            {
                "module_id": row["module_id"],
                "observed_presence": row["observations"]["product_presence_observed"],
                "identity_silhouette": row["identity_silhouette"],
            }
            for row in initial["module_judgments"]
        ],
    }
    for index, (binding, phase) in enumerate(zip(bindings, phases, strict=True)):
        route = require_route(binding.get("dispatch_route"))
        validate_dispatch_result(saved[index], route)
        if (
            route["provider"] != invocation["provider"]
            or binding.get("model") != route["model"]
        ):
            _invalid("Original review model differs from its actual routed request")
        if (
            any(binding.get(k) != v for k, v in {**expected, "phase": phase}.items())
            or not isinstance(binding.get("model"), str)
            or not binding["model"].strip()
        ):
            _invalid("Original review binding differs from approved context or runtime")
        audit_request = PlannerRequest(
            workspace_id="recovery",
            job_id="recovery",
            group_key="recovery",
            sources=(),
            page_count=2,
            language="zh-CN",
            product_name=authority["product_name"],
            product_information=(
                json.dumps(
                    contract if index == 0 else identity_context, ensure_ascii=False
                ),
            ),
            platform_code="domestic-detail",
            size="1440x3840",
            idempotency_key="recovery-readonly",
            domestic_protocol=True,
            planning_profile=phase,
        )
        if binding["prompt_sha256"] != hashlib.sha256(
            _planner_prompt(audit_request).encode()
        ).hexdigest() or binding["response_schema_sha256"] != canonical_hash(
            _planner_response_format(audit_request)
        ):
            _invalid("Original review prompt or schema differs from approved context")
    if needs_identity:
        initial["identity_silhouette"] = merge_identity_followup(
            initial.get("identity_silhouette") or {}, saved[1]["payload"], scope
        )
    normalized = normalize_pair_observation(
        initial,
        native,
        image_sha256=lineage["canvas"]["sha256"],
        width=lineage["canvas"]["width_px"],
        height=lineage["canvas"]["height_px"],
    )
    normalized["review_provenance"] = references
    if canonical_hash(normalized) != canonical_hash(raw):
        _invalid("Accepted observation differs from the original model messages")
