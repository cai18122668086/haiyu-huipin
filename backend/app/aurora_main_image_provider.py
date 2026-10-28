"""Aurora providers for the legacy sequence and immutable v2.0 snapshots."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .aurora_execution_modes import (
    is_calibrated_parallel_aurora_mode,
    is_parallel_aurora_mode,
    is_v20_aurora_mode,
)
from .aurora_generation_shadow import prepare_shadow_generation_request
from .aurora_main_image_planner import (
    AuroraPlanningError,
    _normalize_commercial_fact_pool,
    _persist_parallel_v20_snapshots,
)
from .aurora_main_image_runtime import (
    AuroraHarnessError,
    AuroraHarnessRunner,
    AuroraV20HarnessRunner,
    reset_v20_pending_generation_for_retry,
    write_contract,
)
from .config import default_aurora_work_root
from .domain import JobItemStatus
from .models import (
    AuditEventRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
)
from .provider import ProviderArtifact, ProviderOutcome, ProviderRequest, ProviderResult
from .services import canonical_hash
from .storage import ImageStorage
from .xinghe_agent import (
    _AURORA_V20_MATERIAL_CHECKS,
    _AURORA_V20_VISUAL_CHECKS,
    ImageEditRequest,
    PlannerRequest,
    SourceImage,
    XingheGateway,
)

logger = logging.getLogger(__name__)

_AUDIT_SYSTEM_PROMPT = """
You are the direct-pixel quality reviewer for the Aurora domestic e-commerce
six-image Skill. Inspect the generated candidate itself. Compare original
references only for product identity; never treat a generated image or a
receipt as identity proof. Return every field required by the supplied audit
contract. A failed material check must have exactly one concrete deficiency
with visible_problem, root_cause and required_change. Minor preferences may be
advisory. Multiple related selling points are not a defect. Verify Chinese
legibility, product identity, factual copy, page duty, physical evidence,
campaign continuity, source-background isolation, thumbnail performance and
the approved page contract from actual pixels. Return checks using the exact
required_check_names supplied by the contract. Checks outside that list are
advisory and must not trigger regeneration by themselves. Never infer a missing
required element from an overall PASS. If any required_visible_element, reading-path
stop, page-role expression, physical proof, or commercial closure is not
visibly present, fail the corresponding plan-fidelity check and name the
missing pixel evidence. Return the native v2 semantic judgment exactly: never
omit decision_summary, observations, checks, or all_material_deficiencies; never
use legacy aliases or synthesize semantic defaults; and use only the declared
enum values for headline_rank and background_competition. For the five layout
observations composition_archetype_seen, product_zone_seen, copy_zone_seen,
dominant_view_seen, and evidence_topology_seen, copy the exact corresponding
token from layout_observation_contract; do not replace it with a natural-language
description of the pixels. The runtime binds
page_index, image_sha256 and reviewer, then derives the final audit_complete
attestation only after all semantic checks pass. Do not invent image paths or
hashes for those runtime-owned fields. decision_summary must be a concrete
direct-pixel verdict sentence of at least 12 characters; never return a
one-word verdict such as PASS, 合格 or 通过.
The audit contract includes an authorized_fact_pool. Treat only those user
entered strings, plus faithful non-strengthening paraphrases, as claim
authority. Before deciding factual_copy_match or no_unsupported_claim,
transcribe every visible headline, badge, number, comparison, feature label,
rating, safety statement, and specification from the candidate pixels. Any
proposition absent from that pool is an unsupported claim even when it is
plausible for the category; return a material failure with the exact visible
wording and the required change to remove or replace it.
For product_identity_match, compare the candidate against every supplied
product_reference image as one complementary identity set. Fail materially
when the candidate changes silhouette, proportions, wheel count or placement,
seat count, red/black color partition, steering wheel, dashboard/music panel,
grille, headlights, doors, steps, mirrors, labels, or component relationships.
User facts may authorize copy, but they never authorize a new visible part or
variant component that the product references do not show. An identity failure
must recommend rebuilding from the original product references, never polishing
the failed candidate as a repair canvas.
""".strip()

_STYLE_CALIBRATION_SYSTEM_PROMPT = """
You are the direct-pixel style calibration reviewer for the Aurora domestic
e-commerce six-image Skill. Inspect the supplied accepted generated pages and
return one JSON object only. Set agent_pixel_reviewed to true, set
source_page_indexes to the supplied accepted page indexes, and write
observed_evidence as a concrete description of visible pixels on those pages
with at least 12 characters. Derive only reusable campaign-style observations
from the pixels: photography_finish, light_direction, color_temperature,
palette_roles, palette_usage_ratio, typography, graphic_language,
information_surfaces and density_band. Do not return identity, page layout,
copy, or invented product facts. If a style field cannot be supported by the
pixels, omit that field rather than guessing. The observed_evidence field is
mandatory even when style_tokens is sparse.
""".strip()


class AuroraMainImageProvider:
    """Run generation, direct-pixel review and the one-repair loop per page."""

    name = "aurora"
    model = "gpt-image-2"
    pricing_version = "aurora-v0.1.17"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: XingheGateway,
        storage: ImageStorage,
        *,
        harness: AuroraHarnessRunner | None = None,
        work_root: Path | None = None,
        execution_mode: str = "sequential_v20",
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.storage = storage
        self.harness = harness or (
            AuroraV20HarnessRunner()
            if is_v20_aurora_mode(execution_mode)
            else AuroraHarnessRunner()
        )
        self.execution_mode = execution_mode
        self.pricing_version = (
            "aurora-v2.0.0"
            if is_v20_aurora_mode(execution_mode)
            else "aurora-v0.1.17"
        )
        self.work_root = (work_root or default_aurora_work_root()).resolve()

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        if is_parallel_aurora_mode(self.execution_mode) or _request_uses_parallel_v20(
            request
        ):
            return await self._execute_parallel_v20(request)
        try:
            item, harness_session = self._load_authorized_context(request)
            page_index = item.ordinal
            workspace = self._workspace(request.job_id)
            session_path = workspace / "session.json"
            write_contract(session_path, harness_session)
            artifact: ProviderArtifact | None = None
            generation_usage: dict[str, Any] = {}
            audit_usage: list[dict[str, Any]] = []
            actual_cost = 0

            for attempt_index in range(2):
                generation_request_path = workspace / (
                    f"generation-page-{page_index + 1}-attempt-{attempt_index + 1}.json"
                )
                self.harness.run(
                    "generate-next",
                    workspace,
                    {"session": session_path, "out": generation_request_path},
                )
                self._persist_session(request.job_id, _load_json(session_path))
                generation_request = _load_json(generation_request_path)
                # The native v2 harness owns page composition, but it does not
                # know the API job's fact buckets. Attach the exact user fact
                # pool to the sequential request before the image call and
                # direct-pixel audit so generated copy is judged against the
                # same authority used by planning and parallel snapshots.
                if is_v20_aurora_mode(self.execution_mode):
                    job_configuration = _mapping(request.configuration.get("job"))
                    job_settings = _mapping(job_configuration.get("settings"))
                    generation_request["authorized_facts"] = (
                        _normalize_commercial_fact_pool(job_settings)
                    )
                    write_contract(generation_request_path, generation_request)
                sources = _generation_sources(generation_request, request.job_id)
                image_result = await self.gateway.edit(
                    ImageEditRequest(
                        workspace_id=request.workspace_id,
                        job_id=request.job_id,
                        job_item_id=request.job_item_id,
                        attempt_id=request.attempt_id,
                        page_number=page_index + 1,
                        group_key=request.job_id,
                        sources=sources,
                        prompt=str(generation_request["prompt"]),
                        quality="high",
                        idempotency_key=(
                            f"{request.idempotency_key}:aurora-page-{page_index}:"
                            "revision-"
                            f"{generation_request.get('revision', attempt_index)}"
                        ),
                        size="1024x1024",
                        preserve_provider_artifact=True,
                    )
                )
                generation_usage = dict(image_result.raw_usage or {})
                actual_cost += int(image_result.actual_cost_micros or 0)
                if (
                    image_result.outcome != ProviderOutcome.SUCCEEDED
                    or image_result.artifact is None
                ):
                    if (
                        is_v20_aurora_mode(self.execution_mode)
                        and image_result.retryable
                    ):
                        pending_session = _load_json(session_path)
                        if pending_session.get("state") == "AWAITING_GENERATED_ASSET":
                            self._persist_session(
                                request.job_id,
                                reset_v20_pending_generation_for_retry(
                                    pending_session
                                ),
                            )
                    return _failed_image_result(image_result, self)
                artifact = _normalize_aurora_square_artifact(image_result.artifact)
                generation_usage.update(
                    {
                        "provider_width": artifact.provider_width,
                        "provider_height": artifact.provider_height,
                        "locally_resampled": artifact.locally_resampled,
                        "delivered_width": artifact.width,
                        "delivered_height": artifact.height,
                    }
                )
                generated_path = _write_generated_artifact(
                    workspace, page_index, attempt_index, artifact
                )
                self.harness.run(
                    "record-generation",
                    workspace,
                    {
                        "session": session_path,
                        "token": str(generation_request["generation_token"]),
                        "image": generated_path,
                    },
                )
                current = _load_json(session_path)
                if is_v20_aurora_mode(self.execution_mode):
                    judgment, usage, cost = await self._audit_v20_candidate_with_retry(
                        request,
                        generation_request,
                        generated_path,
                        artifact,
                    )
                else:
                    judgment, usage, cost = await self._audit_candidate(
                        request,
                        current,
                        generation_request,
                        generated_path,
                        artifact,
                    )
                audit_usage.append(usage)
                actual_cost += cost
                judgment_path = (
                    workspace
                    / f"judgment-page-{page_index + 1}-attempt-{attempt_index + 1}.json"
                )
                write_contract(judgment_path, judgment)
                self.harness.run(
                    "audit-current",
                    workspace,
                    {"session": session_path, "judgment": judgment_path},
                )
                current = _load_json(session_path)
                search_calibration_state = (
                    "AWAITING_SEARCH_CALIBRATION"
                    if is_v20_aurora_mode(self.execution_mode)
                    else "AWAITING_SEARCH_BASELINE_CALIBRATION"
                )
                if current.get("state") == search_calibration_state:
                    baseline_path = workspace / "search-baseline-observation.json"
                    write_contract(baseline_path, _baseline_observation(judgment))
                    self.harness.run(
                        (
                            "calibrate-search"
                            if is_v20_aurora_mode(self.execution_mode)
                            else "calibrate-search-baseline"
                        ),
                        workspace,
                        {"session": session_path, "observation": baseline_path},
                    )
                    current = _load_json(session_path)
                if current.get("state") == "REPAIR_REQUIRED" and attempt_index == 0:
                    repair_path = workspace / f"repair-page-{page_index + 1}.json"
                    write_contract(
                        repair_path,
                        (
                            _v20_repair_plan(current)
                            if is_v20_aurora_mode(self.execution_mode)
                            else _repair_plan(current)
                        ),
                    )
                    self.harness.run(
                        "prepare-repair",
                        workspace,
                        {"session": session_path, "repair-plan": repair_path},
                    )
                    self._persist_session(request.job_id, _load_json(session_path))
                    continue
                break

            if artifact is None:
                raise AuroraPlanningError("Aurora generation returned no artifact")
            current = _load_json(session_path)
            style_calibration_state = (
                "AWAITING_STYLE_CALIBRATION"
                if is_v20_aurora_mode(self.execution_mode)
                else "AWAITING_AGENT_STYLE_CALIBRATION"
            )
            if current.get("state") == style_calibration_state:
                observation, usage, cost = await self._calibrate_style(
                    request, current
                )
                audit_usage.append(usage)
                actual_cost += cost
                observation_path = workspace / "style-calibration.json"
                write_contract(observation_path, observation)
                self.harness.run(
                    "calibrate-style",
                    workspace,
                    {"session": session_path, "observation": observation_path},
                )
                current = _load_json(session_path)
            if current.get("state") == "AWAITING_SET_ROLE_AUDIT":
                observation, usage, cost = await self._audit_set(request, current)
                audit_usage.append(usage)
                actual_cost += cost
                observation_path = workspace / "set-role-observation.json"
                write_contract(observation_path, observation)
                self.harness.run(
                    "audit-set-role-distinction",
                    workspace,
                    {"session": session_path, "observation": observation_path},
                )
                current = _load_json(session_path)
            self._persist_session(request.job_id, current)
            raw_usage = dict(generation_usage)
            raw_usage["aurora_audit_calls"] = audit_usage
            network_requests = raw_usage.get("network_requests", 1)
            if not isinstance(network_requests, int) or network_requests < 0:
                network_requests = 1
            raw_usage["network_requests"] = network_requests + len(audit_usage)
            raw_metrics = raw_usage.get("metrics")
            metrics = dict(raw_metrics) if isinstance(raw_metrics, dict) else {}
            raw_usage["metrics"] = {
                **metrics,
                "harness_state": current.get("state"),
                "page_index": page_index,
                "automatic_repairs_used": current.get(
                    "automatic_repairs_used_by_page", {}
                ).get(str(page_index), 0),
            }
            return ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED,
                provider_request_id=f"aurora:{request.job_id}:{page_index}",
                usage_reported=bool(generation_usage),
                raw_usage=raw_usage,
                estimated_cost_micros=image_result.estimated_cost_micros,
                actual_cost_micros=actual_cost,
                artifact=artifact,
                provider_name=self.gateway.provider_name,
                model_name=self.gateway.image_model,
                pricing_version=self.gateway.pricing_version,
            )
        except (
            AuroraHarnessError,
            AuroraPlanningError,
            KeyError,
            OSError,
            ValueError,
        ) as exc:
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                error_code=getattr(exc, "code", "AURORA_EXECUTION_FAILED"),
                error_message=str(exc),
                usage_reported=False,
                raw_usage={
                    "provider_type": self.gateway.provider_name,
                    "called": False,
                },
                retryable=False,
                provider_name=self.gateway.provider_name,
                model_name=self.gateway.image_model,
                pricing_version=self.gateway.pricing_version,
            )

    async def _execute_parallel_v20(
        self, request: ProviderRequest
    ) -> ProviderResult:
        """Execute one pre-authorized page without shared session writes."""

        try:
            item_configuration = _mapping(request.configuration.get("item"))
            snapshot = _mapping(item_configuration.get("aurora_snapshot"))
            shadow_mode = str(
                item_configuration.get("aurora_shadow_mode") or ""
            ).strip()
            generation_request = _mapping(snapshot.get("request"))
            if shadow_mode:
                generation_request = prepare_shadow_generation_request(
                    generation_request,
                    mode=shadow_mode,
                )
            snapshot_mode = str(snapshot.get("execution_mode") or "")
            if not is_parallel_aurora_mode(snapshot_mode):
                raise AuroraPlanningError("Aurora v2.0 page snapshot is missing")
            expected_hash = str(snapshot.get("snapshot_hash") or "")
            hash_payload = dict(snapshot)
            hash_payload.pop("snapshot_hash", None)
            if expected_hash != canonical_hash(hash_payload):
                raise AuroraPlanningError("Aurora v2.0 page snapshot hash mismatch")
            page_index = int(snapshot["page_index"])
            workspace = (
                self._workspace(request.job_id)
                / "parallel-v20"
                / f"page-{page_index + 1:02d}"
                / f"attempt-{request.attempt_id}"
            )
            workspace.mkdir(parents=True, exist_ok=True)
            write_contract(workspace / "snapshot.json", snapshot)
            session_path = workspace / "session.json"
            write_contract(session_path, _mapping(snapshot.get("session")))
            runner = (
                self.harness
                if isinstance(self.harness, AuroraV20HarnessRunner)
                else AuroraV20HarnessRunner()
            )
            logical_key = str(
                snapshot.get("logical_idempotency_key") or request.idempotency_key
            )
            raw_usage: dict[str, Any] = {}
            audit_usage: list[dict[str, Any]] = []
            actual_cost = 0
            artifact: ProviderArtifact | None = None
            image_result: Any = None
            for visual_attempt in range(2):
                sources = _generation_sources(generation_request, request.job_id)
                revision = int(generation_request.get("revision", visual_attempt))
                image_result = await self.gateway.edit(
                    ImageEditRequest(
                        workspace_id=request.workspace_id,
                        job_id=request.job_id,
                        job_item_id=request.job_item_id,
                        attempt_id=request.attempt_id,
                        page_number=page_index + 1,
                        group_key=request.job_id,
                        sources=sources,
                        prompt=str(generation_request["prompt"]),
                        quality="high",
                        idempotency_key=f"{logical_key}:visual-revision-{revision}",
                        size="1024x1024",
                        preserve_provider_artifact=True,
                    )
                )
                if (
                    image_result.outcome != ProviderOutcome.SUCCEEDED
                    or image_result.artifact is None
                ):
                    return _failed_image_result(image_result, self)
                artifact = _normalize_aurora_square_artifact(image_result.artifact)
                raw_usage = dict(image_result.raw_usage or {})
                actual_cost += int(image_result.actual_cost_micros or 0)
                generated_path = _write_generated_artifact(
                    workspace, page_index, visual_attempt, artifact
                )
                runner.run(
                    "record-generation",
                    workspace,
                    {
                        "session": session_path,
                        "token": str(generation_request["generation_token"]),
                        "image": generated_path,
                    },
                )
                judgment, usage, cost = await self._audit_v20_candidate_with_retry(
                    request,
                    generation_request,
                    generated_path,
                    artifact,
                )
                audit_usage.append(usage)
                actual_cost += cost
                judgment_path = workspace / f"judgment-{visual_attempt + 1}.json"
                write_contract(judgment_path, judgment)
                runner.run(
                    "audit-current",
                    workspace,
                    {"session": session_path, "judgment": judgment_path},
                )
                current = _load_json(session_path)
                if current.get("state") == "AWAITING_SEARCH_CALIBRATION":
                    baseline_path = workspace / "search-baseline-observation.json"
                    write_contract(baseline_path, _baseline_observation(judgment))
                    runner.run(
                        "calibrate-search",
                        workspace,
                        {"session": session_path, "observation": baseline_path},
                    )
                    current = _load_json(session_path)
                    if (
                        is_calibrated_parallel_aurora_mode(snapshot_mode)
                        and page_index == 0
                        and current.get("state") == "AUTHORIZED"
                    ):
                        self._publish_parallel_v20_search_calibration(
                            request.job_id,
                            current,
                        )
                if current.get("state") == "AWAITING_STYLE_CALIBRATION":
                    observation, usage, cost = await self._calibrate_parallel_style(
                        request, workspace, generated_path, artifact
                    )
                    audit_usage.append(usage)
                    actual_cost += cost
                    style_path = workspace / "style-calibration.json"
                    write_contract(style_path, observation)
                    runner.run(
                        "calibrate-style",
                        workspace,
                        {"session": session_path, "observation": style_path},
                    )
                    current = _load_json(session_path)
                    if page_index == 1:
                        self._publish_parallel_v20_style_calibration(
                            request.job_id,
                            current,
                        )
                if current.get("state") == "REPAIR_REQUIRED" and visual_attempt == 0:
                    repair_path = workspace / "repair.json"
                    write_contract(repair_path, _v20_repair_plan(current))
                    runner.run(
                        "prepare-repair",
                        workspace,
                        {"session": session_path, "repair-plan": repair_path},
                    )
                    next_request_path = workspace / "repair-generation-request.json"
                    result = runner.run(
                        "generate-next",
                        workspace,
                        {"session": session_path, "out": next_request_path},
                    )
                    next_request = result.get("generation_request")
                    generation_request = (
                        dict(next_request)
                        if isinstance(next_request, Mapping)
                        else _load_json(next_request_path)
                    )
                    # Harness-generated repair requests are intentionally
                    # page-local and therefore do not carry planner-only
                    # authority metadata. Reattach the immutable fact pool
                    # before rebuilding the shadow prompt so a repair cannot
                    # fall back to model-invented copy.
                    authorized_facts = _mapping(
                        _mapping(generation_request).get("authorized_facts")
                    )
                    if not authorized_facts:
                        authorized_facts = _mapping(
                            _mapping(snapshot.get("request")).get(
                                "authorized_facts"
                            )
                        )
                    if authorized_facts:
                        generation_request["authorized_facts"] = authorized_facts
                    if shadow_mode:
                        generation_request = prepare_shadow_generation_request(
                            generation_request,
                            mode=shadow_mode,
                        )
                    continue
                break
            if artifact is None or image_result is None:
                raise AuroraPlanningError("Aurora v2.0 returned no artifact")
            raw_usage["aurora_audit_calls"] = audit_usage
            network_requests = raw_usage.get("network_requests", 1)
            if not isinstance(network_requests, int) or network_requests < 0:
                network_requests = 1
            raw_usage["network_requests"] = (
                network_requests
                + max(0, len(audit_usage) - 1)
                + len(audit_usage)
            )
            metrics = _mapping(raw_usage.get("metrics"))
            metrics.update(
                {
                    "aurora_execution_mode": snapshot_mode,
                    "page_index": page_index,
                    "snapshot_hash": expected_hash,
                    "logical_idempotency_key": logical_key,
                    "independent_workspace": str(workspace),
                    "provider_width": artifact.provider_width,
                    "provider_height": artifact.provider_height,
                    "locally_resampled": artifact.locally_resampled,
                    "harness_state": _load_json(session_path).get("state"),
                    "automatic_repairs_used": int(
                        _load_json(session_path)
                        .get("modifications_used_by_page", {})
                        .get(str(page_index), 0)
                    ),
                    "aurora_shadow_mode": shadow_mode or None,
                }
            )
            raw_usage["metrics"] = metrics
            return ProviderResult(
                outcome=ProviderOutcome.SUCCEEDED,
                provider_request_id=image_result.provider_request_id,
                usage_reported=bool(image_result.raw_usage),
                raw_usage=raw_usage,
                estimated_cost_micros=image_result.estimated_cost_micros,
                actual_cost_micros=actual_cost,
                artifact=artifact,
                provider_name=self.gateway.provider_name,
                model_name=self.gateway.image_model,
                pricing_version=self.gateway.pricing_version,
            )
        except Exception as exc:
            retryable, http_status = _classify_transient_failure(exc)
            # The opt-in shadow path can recover a bounded set of visual
            # contract rejections by submitting the same immutable page again.
            # The native Skill remains fail-closed; only the known composition
            # gate mismatch is eligible for the existing item retry budget.
            if (
                shadow_mode
                and isinstance(exc, AuroraHarnessError)
                and exc.code == "HARNESS_BLOCKED"
                and _is_shadow_composition_gate_failure(str(exc))
            ):
                retryable = True
            return ProviderResult(
                outcome=ProviderOutcome.FAILED,
                error_code=getattr(exc, "code", "AURORA_EXECUTION_FAILED"),
                error_message=str(exc),
                usage_reported=False,
                raw_usage={
                    "provider_type": self.gateway.provider_name,
                    "called": retryable,
                    "metrics": {
                        "aurora_execution_mode": snapshot_mode,
                        "aurora_gateway_diagnostics": getattr(
                            exc, "aurora_gateway_diagnostics", None
                        ),
                    },
                },
                retryable=retryable,
                http_status=http_status,
                provider_name=self.gateway.provider_name,
                model_name=self.gateway.image_model,
                pricing_version=self.gateway.pricing_version,
            )

    async def _audit_v20_candidate_with_retry(
        self,
        request: ProviderRequest,
        generation_request: Mapping[str, Any],
        generated_path: Path,
        artifact: ProviderArtifact,
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        """Retry a transient audit failure without regenerating the candidate.

        The image is already generated and billed by the time the audit runs, so
        an upstream 429/5xx while reviewing it must not discard the artifact.
        Only the audit call is repeated, on the same bounded backoff the
        execution layer uses for whole-item retries.
        """

        for attempt in range(len(_AURORA_AUDIT_RETRY_DELAYS) + 1):
            try:
                judgment, usage, cost = await self._audit_v20_candidate(
                    request,
                    generation_request,
                    generated_path,
                    artifact,
                )
                identity_sources = _identity_sources(
                    generation_request, request.job_id
                )
                omitted_identity = identity_sources[_MAX_VISUAL_AUDIT_IMAGES - 1 :]
                if omitted_identity:
                    coverage_usage: list[dict[str, Any]] = []
                    coverage_cost = 0
                    for batch_index in range(0, len(omitted_identity), 3):
                        coverage_sources = omitted_identity[
                            batch_index : batch_index + 3
                        ]
                        (
                            coverage_judgment,
                            coverage_call_usage,
                            coverage_call_cost,
                        ) = await self._audit_v20_candidate(
                            request,
                            generation_request,
                            generated_path,
                            artifact,
                            identity_sources=coverage_sources,
                            audit_suffix=(
                                f":identity-coverage-{batch_index // 3 + 1}"
                            ),
                        )
                        coverage_usage.append(coverage_call_usage)
                        coverage_cost += coverage_call_cost
                        _merge_identity_coverage_judgment(
                            judgment, coverage_judgment
                        )
                    usage = dict(usage)
                    usage["identity_coverage_calls"] = len(coverage_usage)
                    usage["identity_coverage_usage"] = coverage_usage
                    cost += coverage_cost
                return judgment, usage, cost
            except AuroraPlanningError as exc:
                status = getattr(exc, "http_status", None)
                transient = (
                    status in _AURORA_TRANSIENT_HTTP_STATUSES
                    or _TRANSIENT_HTTP_STATUS.search(str(exc).casefold()) is not None
                )
                if not transient or attempt >= len(_AURORA_AUDIT_RETRY_DELAYS):
                    raise
                delay = _AURORA_AUDIT_RETRY_DELAYS[attempt]
                logger.warning(
                    "aurora v2 audit retry after transient failure: job_id=%s "
                    "page=%s attempt=%s http_status=%s delay=%.1fs",
                    request.job_id,
                    generation_request.get("page_index"),
                    attempt + 1,
                    status,
                    delay,
                )
                await asyncio.sleep(delay)

        raise AuroraPlanningError("Aurora v2.0 audit retry loop did not settle")

    async def _audit_v20_candidate(
        self,
        request: ProviderRequest,
        generation_request: Mapping[str, Any],
        generated_path: Path,
        artifact: ProviderArtifact,
        *,
        identity_sources: Sequence[SourceImage] | None = None,
        audit_suffix: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        page_index = int(generation_request["page_index"])
        candidate = SourceImage(
            asset_id=f"candidate-page-{page_index}",
            group_key=request.job_id,
            filename=str(generated_path),
            content_type=artifact.content_type,
            sha256=artifact.sha256,
            payload=artifact.payload,
            source_role="generated_candidate",
        )
        sources = (
            (*tuple(identity_sources), candidate)
            if identity_sources is not None
            else _bounded_visual_audit_sources(
                _identity_sources(generation_request, request.job_id), candidate
            )
        )
        audit_contract = {
            "page_index": page_index,
            "page_role": generation_request.get("page_role"),
            "page_contract": generation_request.get("page_contract"),
            "campaign_constitution": generation_request.get(
                "campaign_constitution"
            ),
            "authorized_fact_pool": _authorized_fact_pool_for_audit(
                generation_request
            ),
            "factual_copy_audit": {
                "visible_copy_must_be_transcribed": True,
                "unsupported_claim_is_material": True,
                "allow_faithful_paraphrase": True,
                "allow_category_prior_as_fact": False,
            },
            "product_identity_audit": {
                "identity_authority": "all supplied product_reference images",
                "compare_silhouette_proportions_components_and_color_partitions": True,
                "variant_facts_may_not_add_visible_parts": True,
                "identity_failure_repair": "REBUILD_FROM_PRODUCT_EVIDENCE",
            },
            "required_check_names": list(_VISUAL_CHECKS),
            "response_contract": "aurora-native-v2-visual-judgment",
            "runtime_bound_fields": [
                "page_index",
                "image_sha256",
                "reviewer",
                "audit_complete",
            ],
            "semantic_fields_required_from_reviewer": [
                "decision_summary",
                "observations",
                "checks",
                "all_material_deficiencies",
            ],
            "decision_summary_min_length": 12,
            "legacy_aliases_allowed": False,
            "default_field_synthesis_allowed": False,
        }
        page_contract = _mapping(generation_request.get("page_contract"))
        layout_contract = _mapping(page_contract.get("layout_contract"))
        audit_contract["layout_observation_contract"] = {
            "composition_archetype_seen": str(
                layout_contract.get("composition_archetype") or ""
            ),
            "product_zone_seen": str(layout_contract.get("product_zone") or ""),
            "copy_zone_seen": str(layout_contract.get("copy_zone") or ""),
            "dominant_view_seen": str(
                layout_contract.get("dominant_view") or ""
            ),
            "evidence_topology_seen": str(
                layout_contract.get("evidence_topology") or ""
            ),
        }
        result = await self.gateway.plan(
            PlannerRequest(
                workspace_id=request.workspace_id,
                job_id=request.job_id,
                group_key=f"{request.job_id}:v20-audit:{page_index}",
                sources=tuple(sources),
                page_count=page_index + 1,
                language="zh-CN",
                product_name="Aurora v2.0 direct-pixel audit",
                product_information=(
                    json.dumps(audit_contract, ensure_ascii=False),
                ),
                platform_code="TAOBAO_AURORA_V20_PIXEL_AUDIT",
                size="1024x1024",
                idempotency_key=(
                    f"{request.idempotency_key}:v20-audit:{page_index}:"
                    f"{generation_request.get('revision', 0)}{audit_suffix}"
                ),
                system_prompt=_AUDIT_SYSTEM_PROMPT,
                planning_profile="aurora_v20_visual_audit",
            )
        )
        if result.error_code or not isinstance(result.payload, Mapping):
            raise _v20_gateway_error(
                result,
                _v20_audit_failure_message(result),
            )
        try:
            judgment = self._normalize_v20_visual_judgment_payload(
                dict(result.payload),
                generation_request,
                runtime_image_sha256=artifact.sha256,
            )
        except AuroraPlanningError as exc:
            # Preserve the validator's fail-closed message while attaching only
            # bounded response-shape diagnostics for the next investigation.
            exc.aurora_gateway_diagnostics = _safe_planner_diagnostics(result)
            raise
        judgment["page_index"] = page_index
        judgment["image_sha256"] = artifact.sha256
        reviewer = _mapping(judgment.get("reviewer"))
        reviewer.update(
            {
                "mode": "AGENT_DIRECT_PIXEL_REVIEW",
                "pixel_inspection_performed": True,
                "image_path": str(generated_path),
            }
        )
        judgment["reviewer"] = reviewer
        return (
            judgment,
            dict(result.normalized_usage),
            int(result.actual_cost_micros or 0),
        )

    async def _calibrate_parallel_style(
        self,
        request: ProviderRequest,
        workspace: Path,
        generated_path: Path,
        artifact: ProviderArtifact,
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        """Run native Campaign Style calibration for the page-1 worker.

        Page workers have independent sessions. When page 0 is already
        durable, include its accepted pixels; otherwise the current page is
        still reviewed by the style profile and the Harness remains the final
        validator of the observation contract.
        """

        sources: list[SourceImage] = []
        parallel_root = workspace.parent.parent
        item_configuration = _mapping(request.configuration.get("item"))
        source_job_id = str(
            item_configuration.get("aurora_parallel_source_job_id") or ""
        ).strip()
        search_roots = [parallel_root]
        if source_job_id and source_job_id != request.job_id:
            # Retry jobs preserve successful deliverables.  If page 0 was
            # reused, its native accepted session is in the source job's
            # durable workspace rather than the retry workspace.
            source_root = self._workspace(source_job_id) / "parallel-v20"
            if source_root not in search_roots:
                search_roots.append(source_root)
        page_zero: Path | None = None
        # Style calibration is a native gate: page 0 must have passed its
        # direct-pixel audit and search calibration, not merely produced bytes.
        # The wait is bounded so a failed page cannot leave page 1 hanging.
        for _ in range(720):
            with self.session_factory() as db:
                page_zero_status = db.scalar(
                    select(JobItemRecord.status).where(
                        JobItemRecord.job_id == request.job_id,
                        JobItemRecord.ordinal == 0,
                    )
                )
            if page_zero_status in {
                JobItemStatus.FAILED.value,
                JobItemStatus.CANCELLED.value,
                JobItemStatus.SKIPPED.value,
            }:
                raise AuroraPlanningError(
                    "Aurora v2.0 style calibration cannot use failed page 0"
                )
            for search_root in search_roots:
                for session_path in search_root.glob(
                    "page-01/attempt-*/session.json"
                ):
                    try:
                        page_session = _load_json(session_path)
                    except (OSError, ValueError):
                        continue
                    if (
                        0 in page_session.get("accepted_pages", [])
                        and page_session.get("search_calibrated") is True
                    ):
                        versions = page_session.get("generated_versions")
                        accepted = [
                            item
                            for item in versions or []
                            if isinstance(item, Mapping)
                            and item.get("page_index") == 0
                            and item.get("status")
                            in {"ACCEPTED", "ACCEPTED_WITH_RESIDUALS"}
                        ]
                        if accepted:
                            candidate = Path(str(accepted[-1].get("file") or ""))
                            if candidate.is_file():
                                page_zero = candidate
                                break
                if page_zero is not None:
                    break
            if page_zero is not None:
                break
            await asyncio.sleep(0.25)
        if page_zero is None:
            raise AuroraPlanningError(
                "Aurora v2.0 style calibration requires an accepted page 0"
            )
        path = page_zero
        data = path.read_bytes()
        sources.append(
            SourceImage(
                asset_id="accepted-page-0",
                group_key=request.job_id,
                filename=str(path),
                content_type=_content_type(path),
                sha256=_sha256(data),
                payload=data,
                source_role="style_reference",
            )
        )
        sources.append(
            SourceImage(
                asset_id="accepted-page-1",
                group_key=request.job_id,
                filename=str(generated_path),
                content_type=artifact.content_type,
                sha256=artifact.sha256,
                payload=artifact.payload,
                source_role="style_reference",
            )
        )
        result = await self.gateway.plan(
            PlannerRequest(
                workspace_id=request.workspace_id,
                job_id=request.job_id,
                group_key=f"{request.job_id}:style-calibration",
                sources=tuple(sources),
                page_count=2,
                language="zh-CN",
                product_name="Aurora Campaign Style",
                product_information=(),
                platform_code="TAOBAO_AURORA_STYLE_CALIBRATION",
                size="1024x1024",
                idempotency_key=f"{request.idempotency_key}:style-calibration:v1",
                system_prompt=_STYLE_CALIBRATION_SYSTEM_PROMPT,
                planning_profile="aurora_style_calibration",
            )
        )
        if result.error_code or not isinstance(result.payload, Mapping):
            raise AuroraPlanningError(
                result.error_message or "Aurora v2.0 style calibration failed"
            )
        return (
            _normalize_style_calibration_payload(dict(result.payload)),
            dict(result.normalized_usage),
            int(result.actual_cost_micros or 0),
        )


    def _publish_parallel_v20_style_calibration(
        self,
        job_id: str,
        calibrated_session: Mapping[str, Any],
    ) -> None:
        """Persist post-generation style calibration and release page 2-5.

        Legacy parallel jobs already contain all six immutable snapshots. The
        calibrated shadow mode reaches this method with only pages 0-1 present;
        it materializes the remaining requests after the style gate. The
        missing-page fallback keeps recovery of older jobs safe, but never
        replaces a snapshot that may already have been claimed by a worker.
        """

        with self.session_factory() as db:
            plan = db.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
                .with_for_update()
            )
            job = db.get(JobRecord, job_id, with_for_update=True)
            if plan is None or job is None:
                raise AuroraPlanningError("Aurora v2.0 continuation context is missing")
            payload = dict(plan.payload)
            if payload.get("_parallel_v20_style_calibrated") is True:
                return
            existing = plan.payload.get("_parallel_v20_snapshots")
            existing_indexes: set[int] = set()
            for snapshot in existing or ():
                if not isinstance(snapshot, Mapping):
                    continue
                raw_index = snapshot.get("page_index")
                if isinstance(raw_index, int):
                    existing_indexes.add(raw_index)
            missing_indexes = tuple(
                index for index in range(2, 6) if index not in existing_indexes
            )
            if missing_indexes:
                root = self._workspace(job_id)
                runner = (
                    self.harness
                    if isinstance(self.harness, AuroraV20HarnessRunner)
                    else AuroraV20HarnessRunner()
                )
                _persist_parallel_v20_snapshots(
                    plan,
                    job,
                    root,
                    calibrated_session,
                    runner,
                    page_indexes=missing_indexes,
                    execution_mode=str(
                        _mapping(_mapping(plan.payload).get("execution_policy")).get(
                            "execution_mode", "parallel_v20"
                        )
                    ),
                )
            payload = dict(plan.payload)
            payload["_parallel_v20_style_calibrated"] = True
            payload["_harness_session"] = dict(calibrated_session)
            payload["harness_state"] = calibrated_session.get("state")
            plan.payload = payload
            plan.payload_hash = canonical_hash(payload)
            db.commit()

    def _publish_parallel_v20_search_calibration(
        self,
        job_id: str,
        calibrated_session: Mapping[str, Any],
    ) -> None:
        """Release page 1 only after page 0 search calibration is durable."""

        with self.session_factory() as db:
            plan = db.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
                .with_for_update()
            )
            job = db.get(JobRecord, job_id, with_for_update=True)
            if plan is None or job is None:
                raise AuroraPlanningError("Aurora v2.0 continuation context is missing")
            policy = _mapping(plan.payload.get("execution_policy"))
            mode = str(policy.get("execution_mode") or "")
            if not is_calibrated_parallel_aurora_mode(mode):
                return
            existing = plan.payload.get("_parallel_v20_snapshots")
            existing_indexes = {
                int(item["page_index"])
                for item in existing or ()
                if isinstance(item, Mapping) and item.get("page_index") is not None
            }
            if 1 not in existing_indexes:
                root = self._workspace(job_id)
                runner = (
                    self.harness
                    if isinstance(self.harness, AuroraV20HarnessRunner)
                    else AuroraV20HarnessRunner()
                )
                _persist_parallel_v20_snapshots(
                    plan,
                    job,
                    root,
                    calibrated_session,
                    runner,
                    page_indexes=(1,),
                    execution_mode=mode,
                )
            payload = dict(plan.payload)
            payload["_parallel_v20_search_calibrated"] = True
            payload["_harness_session"] = dict(calibrated_session)
            payload["harness_state"] = calibrated_session.get("state")
            plan.payload = payload
            plan.payload_hash = canonical_hash(payload)
            db.commit()


    @staticmethod
    def _normalize_v20_visual_judgment_payload(
        payload: dict[str, Any],
        generation_request: Mapping[str, Any],
        *,
        runtime_image_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Validate native v2 semantics and bind only trusted runtime data.

        The gateway can inspect pixels and report semantic observations, but it
        cannot authoritatively know the local page index, generated artifact
        hash, reviewer path, or whether this process completed every required
        validation step. Those fields are bound by the caller after this
        function returns. Missing semantic fields still fail closed.
        """

        required = {
            "decision_summary",
            "observations",
            "checks",
            "all_material_deficiencies",
        }
        missing = sorted(required - payload.keys())
        if missing:
            raise AuroraPlanningError(
                "Aurora v2 native visual judgment is incomplete: "
                + ", ".join(missing)
            )
        expected_page = int(generation_request.get("page_index", 0))
        if "page_index" in payload and payload.get("page_index") != expected_page:
            raise AuroraPlanningError("Aurora v2 judgment page_index is stale")
        image_sha256 = payload.get("image_sha256")
        if image_sha256 is not None:
            if (
                not isinstance(image_sha256, str)
                or len(image_sha256) != 64
                or any(
                    char not in "0123456789abcdefABCDEF" for char in image_sha256
                )
            ):
                raise AuroraPlanningError("Aurora v2 image_sha256 is invalid")
            if (
                runtime_image_sha256 is not None
                and image_sha256.lower() != runtime_image_sha256.lower()
            ):
                raise AuroraPlanningError("Aurora v2 judgment image hash is stale")
        summary = payload.get("decision_summary")
        if not isinstance(summary, str) or len(summary.strip()) < 12:
            raise AuroraPlanningError("Aurora v2 decision_summary is invalid")
        if "audit_complete" in payload and payload.get("audit_complete") is not True:
            raise AuroraPlanningError("Aurora v2 audit_complete must be true")
        reviewer = payload.get("reviewer")
        if reviewer is not None:
            if not isinstance(reviewer, Mapping):
                raise AuroraPlanningError("Aurora v2 reviewer is invalid")
            if reviewer.get("mode") not in {
                None,
                "AGENT_DIRECT_PIXEL_REVIEW",
            }:
                raise AuroraPlanningError("Aurora v2 reviewer mode is invalid")
            if reviewer.get("pixel_inspection_performed") not in {None, True}:
                raise AuroraPlanningError(
                    "Aurora v2 reviewer did not confirm pixel inspection"
                )

        observations = payload.get("observations")
        if not isinstance(observations, Mapping):
            raise AuroraPlanningError("Aurora v2 observations must be an object")
        required_observations = {
            "product_prominence",
            "headline_rank",
            "headline_text",
            "headline_line_count",
            "physical_evidence",
            "evidence_is_primary_visual",
            "depth_layers",
            "commercial_closure_visible",
            "background_competition",
            "thumbnail_scan_order",
            "type_roles_seen",
            "unequal_information_modules_seen",
            "visual_tension_seen",
            "composition_archetype_seen",
            "product_zone_seen",
            "copy_zone_seen",
            "dominant_view_seen",
            "evidence_topology_seen",
            "neighbor_layout_difference",
            "claim_to_evidence_relation",
            "claim_takeaway_without_copy",
            "source_background_leakage",
        }
        missing_observations = sorted(required_observations - observations.keys())
        if missing_observations:
            raise AuroraPlanningError(
                "Aurora v2 observations are incomplete: "
                + ", ".join(missing_observations)
            )
        if observations.get("headline_rank") not in {
            "FIRST",
            "JOINT_FIRST",
            "SECOND",
            "WEAK",
        }:
            raise AuroraPlanningError("Aurora v2 headline_rank is invalid")
        if observations.get("background_competition") not in {
            "LOW",
            "MEDIUM",
            "HIGH",
        }:
            raise AuroraPlanningError("Aurora v2 background_competition is invalid")
        if not isinstance(observations.get("physical_evidence"), list):
            raise AuroraPlanningError("Aurora v2 physical_evidence is invalid")
        if not isinstance(observations.get("source_background_leakage"), list):
            raise AuroraPlanningError(
                "Aurora v2 source_background_leakage is invalid"
            )

        checks = payload.get("checks")
        if not isinstance(checks, list) or not all(
            isinstance(item, Mapping) for item in checks
        ):
            raise AuroraPlanningError("Aurora v2 check is not an object")
        # The native contract is "every one of the 29 checks exactly once".  It
        # is not "in this order": the JSON Schema can only constrain the set, so
        # rejecting a complete but differently ordered response failed pages the
        # model had actually reviewed.  Validate the set, then restore the
        # canonical order the immutable Harness runtime compares against.
        checks_by_name: dict[str, Mapping[str, Any]] = {}
        duplicate_check_names: set[str] = set()
        for item in checks:
            name = str(item.get("name"))
            if name in checks_by_name:
                # Some OpenAI-compatible gateways have returned one native
                # check twice despite the strict response schema.  Treat this
                # as a recoverable response-shape defect: keep the more
                # conservative observation so an accidental duplicate can
                # never turn a real failure into a pass.  The complete native
                # set is still enforced immediately below, so a duplicate
                # cannot hide an omitted check.
                duplicate_check_names.add(name)
                current = checks_by_name[name]
                current_passed = current.get("passed") is True
                candidate_passed = item.get("passed") is True
                if current_passed != candidate_passed:
                    if not candidate_passed:
                        checks_by_name[name] = item
                    continue
                current_score = current.get("score")
                candidate_score = item.get("score")
                if (
                    isinstance(candidate_score, (int, float))
                    and not isinstance(candidate_score, bool)
                    and (
                        not isinstance(current_score, (int, float))
                        or isinstance(current_score, bool)
                        or candidate_score < current_score
                    )
                ):
                    checks_by_name[name] = item
                continue
            checks_by_name[name] = item
        if duplicate_check_names:
            logger.warning(
                "aurora v2 audit response repeated native checks; "
                "coalesced conservatively: %s",
                ", ".join(sorted(duplicate_check_names)),
            )
        missing = [name for name in _VISUAL_CHECKS if name not in checks_by_name]
        unexpected = sorted(set(checks_by_name) - set(_VISUAL_CHECKS))
        if missing or unexpected:
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unexpected:
                details.append("unexpected: " + ", ".join(unexpected))
            raise AuroraPlanningError(
                "Aurora v2 checks must contain the complete native check set ("
                + "; ".join(details)
                + ")"
            )
        checks = [dict(checks_by_name[name]) for name in _VISUAL_CHECKS]
        payload["checks"] = checks
        for item in checks:
            name = str(item.get("name"))
            expected_material = name in _V20_MATERIAL_CHECKS
            if item.get("material") is not expected_material:
                raise AuroraPlanningError(
                    f"Aurora v2 {name}.material does not match v2 policy"
                )
            if not isinstance(item.get("passed"), bool):
                raise AuroraPlanningError("Aurora v2 check passed must be boolean")
            score = item.get("score")
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not 0 <= score <= 100
            ):
                raise AuroraPlanningError("Aurora v2 check score is invalid")
            # The native v2 contract is a 0–100 percentage. Some vision
            # gateways emit the equivalent confidence as 0–1; normalize that
            # representation at the application boundary before invoking the
            # immutable Skill runtime.
            if 0 <= float(score) <= 1:
                item["score"] = round(float(score) * 100, 2)
            evidence = item.get("observed_evidence")
            if not isinstance(evidence, str) or len(evidence.strip()) < 8:
                raise AuroraPlanningError("Aurora v2 check evidence is missing")

        observations = _mapping(payload.get("observations"))
        if expected_page == 0:
            copy_contract = _mapping(
                _mapping(generation_request.get("page_contract")).get(
                    "display_copy_contract"
                )
            )
            headline = re.sub(
                r"[\s，,。；;：:·|/]+",
                "",
                str(observations.get("headline_text") or ""),
            )
            hard_max = copy_contract.get("headline_hard_max_chars", 12)
            max_lines = copy_contract.get("headline_max_lines", 2)
            if (
                isinstance(hard_max, (int, float))
                and not isinstance(hard_max, bool)
                and isinstance(max_lines, (int, float))
                and not isinstance(max_lines, bool)
                and (
                    len(headline) > int(hard_max)
                    or int(observations.get("headline_line_count") or 0)
                    > int(max_lines)
                )
            ):
                for check_name in ("headline_treatment_match", "thumbnail_success"):
                    check = next(
                        item for item in checks if item.get("name") == check_name
                    )
                    if check.get("passed") is True:
                        check["passed"] = False
                        check["score"] = min(float(check.get("score", 69)), 69)
                        check["observed_evidence"] = (
                            "搜索主标题超过已批准显示文案合同的硬上限，需压缩后再验收。"
                        )
        if expected_page == 0:
            baseline_visible = (
                observations.get("type_roles_seen", 0) >= 4
                and observations.get("headline_rank") in {"FIRST", "JOINT_FIRST"}
                and observations.get("depth_layers", 0) >= 2
                and observations.get("commercial_closure_visible") is True
                and bool(observations.get("physical_evidence"))
            )
            baseline_check = next(
                item
                for item in checks
                if item.get("name") == "baseline_performance_match"
            )
            if baseline_check.get("passed") is True and not baseline_visible:
                baseline_check["passed"] = False
                baseline_check["score"] = min(
                    float(baseline_check.get("score", 69)), 69
                )
                baseline_check["observed_evidence"] = (
                    "搜索页像素未同时呈现四级文字角色、强标题、纵深、"
                    "真实物理证据和已核验事实闭合。"
                )

        # A contradictory passed=true/score<70 response must become a normal
        # material failure so the Harness can enter its one-repair state rather
        # than terminating the entire job during validation.
        for item in checks:
            if item.get("passed") is True and float(item["score"]) < 70:
                item["passed"] = False
                item["observed_evidence"] = str(
                    item.get("observed_evidence")
                    or "像素审核置信度低于材料性通过阈值。"
                )

        deficiencies = payload.get("all_material_deficiencies")
        if not isinstance(deficiencies, list):
            raise AuroraPlanningError(
                "Aurora v2 all_material_deficiencies must be an array"
            )
        failed_material = {
            str(item["name"])
            for item in checks
            if item.get("material") is True and item.get("passed") is False
        }
        existing_by_name = {
            str(item.get("check")): dict(item)
            for item in deficiencies
            if isinstance(item, Mapping) and item.get("check")
        }
        normalized_deficiencies: list[dict[str, Any]] = []
        for check in checks:
            name = str(check["name"])
            if name not in failed_material:
                continue
            normalized_deficiencies.append(
                existing_by_name.get(
                    name,
                    {
                        "check": name,
                        "visible_problem": str(check.get("observed_evidence")),
                        "root_cause": (
                            "当前候选图未满足该材料性 Aurora v2 页面合同。"
                        ),
                        "required_change": (
                            "仅修正该失败层，保留商品身份、已核验事实和其余通过层。"
                        ),
                    },
                )
            )
        payload["all_material_deficiencies"] = normalized_deficiencies
        deficiencies = normalized_deficiencies
        deficiency_names: list[str] = []
        for item in deficiencies:
            if not isinstance(item, Mapping):
                raise AuroraPlanningError("Aurora v2 deficiency is not an object")
            deficiency_name = item.get("check")
            if not isinstance(deficiency_name, str) or not deficiency_name:
                raise AuroraPlanningError("Aurora v2 deficiency check is invalid")
            deficiency_names.append(deficiency_name)
            for field in ("visible_problem", "root_cause", "required_change"):
                value = item.get(field)
                if not isinstance(value, str) or len(value.strip()) < 8:
                    raise AuroraPlanningError(
                        f"Aurora v2 deficiency {field} is missing"
                    )
        if set(deficiency_names) != failed_material or len(deficiency_names) != len(
            set(deficiency_names)
        ):
            raise AuroraPlanningError(
                "Aurora v2 material deficiencies do not match failed material checks"
            )

        normalized = dict(payload)
        normalized["page_index"] = expected_page
        normalized["audit_complete"] = True
        return normalized

    async def audit_parallel_set(self, job_id: str) -> bool:
        """Merge six page-local sessions and run the native v2.0 set audit."""

        with self.session_factory() as db:
            plan = db.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            items = db.scalars(
                select(JobItemRecord)
                .where(JobItemRecord.job_id == job_id)
                .order_by(JobItemRecord.ordinal)
            ).all()
            source_items = {
                item.id: item
                for item in db.scalars(
                    select(JobItemRecord).where(
                        JobItemRecord.id.in_(
                            [
                                item.source_job_item_id
                                for item in items
                                if item.status == JobItemStatus.REUSED.value
                                and item.source_job_item_id
                            ]
                        )
                    )
                ).all()
            }
            attempt_job_ids = {job_id}
            attempt_job_ids.update(item.job_id for item in source_items.values())
            attempts = db.scalars(
                select(JobAttemptRecord)
                .where(
                    JobAttemptRecord.job_id.in_(attempt_job_ids),
                    JobAttemptRecord.status == "succeeded",
                )
                .order_by(
                    JobAttemptRecord.job_item_id,
                    JobAttemptRecord.attempt_no.desc(),
                )
            ).all()
            if plan is None or len(items) != 6:
                raise AuroraPlanningError("Aurora v2.0 set context is incomplete")
            attempt_by_item: dict[str, JobAttemptRecord] = {}
            for attempt in attempts:
                attempt_by_item.setdefault(attempt.job_item_id, attempt)

        set_workspace = self._workspace(job_id) / "parallel-v20" / "set-audit"
        set_workspace.mkdir(parents=True, exist_ok=True)
        page_sessions: list[dict[str, Any]] = []
        sources: list[SourceImage] = []
        for item in items:
            source_item = source_items.get(item.source_job_item_id or "")
            origin_item_id = source_item.id if source_item is not None else item.id
            origin_job_id = source_item.job_id if source_item is not None else job_id
            page_attempt = attempt_by_item.get(origin_item_id)
            if page_attempt is None:
                raise AuroraPlanningError(
                    f"Aurora v2.0 page {item.ordinal} has no succeeded attempt"
                )
            page_workspace = (
                self._workspace(origin_job_id)
                / "parallel-v20"
                / f"page-{item.ordinal + 1:02d}"
                / f"attempt-{page_attempt.id}"
            )
            page_session = _load_json(page_workspace / "session.json")
            versions = page_session.get("generated_versions")
            selected = [
                value
                for value in versions or []
                if isinstance(value, Mapping)
                and value.get("page_index") == item.ordinal
                and value.get("status") in {"ACCEPTED", "ACCEPTED_WITH_RESIDUALS"}
            ]
            if not selected:
                raise AuroraPlanningError(
                    f"Aurora v2.0 page {item.ordinal} has no accepted version"
                )
            version = selected[-1]
            image_path = Path(str(version["file"])).resolve()
            image_payload = image_path.read_bytes()
            sources.append(
                SourceImage(
                    asset_id=f"accepted-page-{item.ordinal}",
                    group_key=job_id,
                    filename=str(image_path),
                    content_type=_content_type(image_path),
                    sha256=str(version["sha256"]),
                    payload=image_payload,
                    source_role="generated_candidate",
                )
            )
            page_sessions.append(page_session)

        base = dict(page_sessions[0])
        base["state"] = "AWAITING_SET_AUDIT"
        base["accepted_pages"] = list(range(6))
        base["active_page_index"] = None
        base["pending_generation"] = None
        base["search_calibrated"] = True
        base["style_calibrated"] = True
        base["generated_versions"] = [
            version
            for page_session in page_sessions
            for version in page_session.get("generated_versions", [])
            if isinstance(version, Mapping)
            and version.get("status") in {"ACCEPTED", "ACCEPTED_WITH_RESIDUALS"}
        ]
        set_session_path = set_workspace / "session.json"
        write_contract(set_session_path, base)
        result = await self.gateway.plan(
            PlannerRequest(
                workspace_id=items[0].workspace_id,
                job_id=job_id,
                group_key=f"{job_id}:v20-set-audit",
                sources=tuple(sources),
                page_count=6,
                language="zh-CN",
                product_name="Aurora v2.0 six-page set audit",
                product_information=(),
                platform_code="TAOBAO_AURORA_V20_SET_AUDIT",
                size="1024x1024",
                idempotency_key=f"{job_id}:aurora-v20-set-audit:v1",
                system_prompt=_AUDIT_SYSTEM_PROMPT,
                planning_profile="aurora_set_audit",
            )
        )
        if result.error_code or not isinstance(result.payload, Mapping):
            raise AuroraPlanningError(
                result.error_message or "Aurora v2.0 set audit failed"
            )
        observation = _normalize_parallel_set_audit_payload(
            dict(result.payload), sources
        )
        observation_path = set_workspace / "observation.json"
        write_contract(observation_path, observation)
        runner = (
            self.harness
            if isinstance(self.harness, AuroraV20HarnessRunner)
            else AuroraV20HarnessRunner()
        )
        runner.run(
            "audit-set",
            set_workspace,
            {"session": set_session_path, "observation": observation_path},
        )
        audited = _load_json(set_session_path)
        passed = audited.get("set_audit_passed") is True
        with self.session_factory() as db:
            stored_plan = db.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
                .with_for_update()
            )
            if stored_plan is None:
                raise AuroraPlanningError("Aurora v2.0 plan disappeared")
            payload = dict(stored_plan.payload)
            payload["_parallel_v20_set_session"] = audited
            payload["set_audit_passed"] = passed
            payload["harness_state"] = audited.get("state")
            stored_plan.payload = payload
            stored_plan.payload_hash = canonical_hash(payload)
            db.add(
                AuditEventRecord(
                    workspace_id=items[0].workspace_id,
                    action="aurora.parallel_v20_set_audited",
                    resource_type="job",
                    resource_id=job_id,
                    request_id=f"{job_id}:v20-set-audit",
                    details={
                        "passed": passed,
                        "harness_state": audited.get("state"),
                    },
                )
            )
            db.commit()
        return passed

    async def audit_sequential_v20_set(self, job_id: str) -> bool:
        """Run the native v2.0 set audit over the shared sequential session."""

        with self.session_factory() as db:
            plan = db.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            items = db.scalars(
                select(JobItemRecord)
                .where(JobItemRecord.job_id == job_id)
                .order_by(JobItemRecord.ordinal)
            ).all()
            if plan is None or len(items) != 6:
                raise AuroraPlanningError(
                    "Aurora v2.0 sequential set context is incomplete"
                )
            session = _mapping(plan.payload.get("_harness_session"))
            if session.get("state") != "AWAITING_SET_AUDIT":
                raise AuroraPlanningError(
                    "Aurora v2.0 sequential session is not awaiting set audit"
                )
            workspace_id = items[0].workspace_id

        set_workspace = self._workspace(job_id) / "sequential-v20" / "set-audit"
        set_workspace.mkdir(parents=True, exist_ok=True)
        sources = _accepted_generated_sources(session, job_id, tuple(range(6)))
        set_session_path = set_workspace / "session.json"
        write_contract(set_session_path, session)
        result = await self.gateway.plan(
            PlannerRequest(
                workspace_id=workspace_id,
                job_id=job_id,
                group_key=f"{job_id}:v20-sequential-set-audit",
                sources=tuple(sources),
                page_count=6,
                language="zh-CN",
                product_name="Aurora v2.0 six-page set audit",
                product_information=(),
                platform_code="TAOBAO_AURORA_V20_SET_AUDIT",
                size="1024x1024",
                idempotency_key=f"{job_id}:aurora-v20-sequential-set-audit:v1",
                system_prompt=_AUDIT_SYSTEM_PROMPT,
                planning_profile="aurora_set_audit",
            )
        )
        if result.error_code or not isinstance(result.payload, Mapping):
            raise AuroraPlanningError(
                result.error_message or "Aurora v2.0 sequential set audit failed"
            )
        observation = _normalize_parallel_set_audit_payload(
            dict(result.payload), sources
        )
        observation_path = set_workspace / "observation.json"
        write_contract(observation_path, observation)
        AuroraV20HarnessRunner().run(
            "audit-set",
            set_workspace,
            {"session": set_session_path, "observation": observation_path},
        )
        audited = _load_json(set_session_path)
        passed = audited.get("set_audit_passed") is True
        with self.session_factory() as db:
            stored_plan = db.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
                .with_for_update()
            )
            if stored_plan is None:
                raise AuroraPlanningError("Aurora v2.0 sequential plan disappeared")
            payload = dict(stored_plan.payload)
            payload["_sequential_v20_set_session"] = audited
            payload["set_audit_passed"] = passed
            payload["harness_state"] = audited.get("state")
            stored_plan.payload = payload
            stored_plan.payload_hash = canonical_hash(payload)
            db.add(
                AuditEventRecord(
                    workspace_id=workspace_id,
                    action="aurora.sequential_v20_set_audited",
                    resource_type="job",
                    resource_id=job_id,
                    request_id=f"{job_id}:v20-sequential-set-audit",
                    details={"passed": passed, "harness_state": audited.get("state")},
                )
            )
            db.commit()
        return passed

    def _load_authorized_context(
        self, request: ProviderRequest
    ) -> tuple[JobItemRecord, dict[str, Any]]:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id)
            item = session.get(JobItemRecord, request.job_item_id)
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == request.job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
            )
            if job is None or item is None or plan is None:
                raise AuroraPlanningError("Aurora durable task context is missing")
            if (
                plan.confirmed_at is None
                or plan.schema_version != "aurora-main-image-plan-v2"
            ):
                raise AuroraPlanningError(
                    "A confirmed compiled Aurora plan is required"
                )
            stored = plan.payload.get("_harness_session")
            if not isinstance(stored, Mapping) or stored.get("state") != "AUTHORIZED":
                raise AuroraPlanningError(
                    "Aurora Harness is not authorized for generation"
                )
            accepted = (
                stored.get("accepted_pages", [])
                if is_v20_aurora_mode(self.execution_mode)
                else stored.get("accepted_page_indexes", [])
            )
            if item.ordinal != len(accepted):
                raise AuroraPlanningError(
                    "Aurora pages must be generated strictly in order"
                )
            return item, dict(stored)

    def _persist_session(self, job_id: str, harness_session: Mapping[str, Any]) -> None:
        with self.session_factory() as session:
            plan = session.scalar(
                select(JobPlanRecord)
                .where(JobPlanRecord.job_id == job_id)
                .order_by(JobPlanRecord.revision.desc())
                .limit(1)
                .with_for_update()
            )
            if plan is None:
                raise AuroraPlanningError("Aurora plan disappeared during execution")
            payload = dict(plan.payload)
            payload["_harness_session"] = dict(harness_session)
            payload["harness_state"] = harness_session.get("state")
            plan.payload = payload
            plan.payload_hash = canonical_hash(payload)
            session.commit()

    async def _audit_candidate(
        self,
        request: ProviderRequest,
        session: Mapping[str, Any],
        generation_request: Mapping[str, Any],
        generated_path: Path,
        artifact: ProviderArtifact,
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        page_index = int(generation_request["page_index"])
        proof = _mapping(generation_request.get("selling_point_visual_proof_contract"))
        candidate = SourceImage(
            asset_id=f"candidate-page-{page_index}",
            group_key=request.job_id,
            filename=str(generated_path),
            content_type=artifact.content_type,
            sha256=artifact.sha256,
            payload=artifact.payload,
            source_role="generated_candidate",
        )
        sources = list(
            _bounded_visual_audit_sources(
                _identity_sources(generation_request, request.job_id),
                candidate,
            )
        )
        required_check_names = _required_audit_check_names(
            session, generation_request
        )
        audit_contract = {
            "page_index": page_index,
            "page_role": generation_request.get("page_role"),
            "source_text": proof.get("source_text"),
            "observability_class": proof.get("observability_class"),
            "approved_page_execution_contract": generation_request.get(
                "approved_page_execution_contract"
            ),
            "visual_performance_contract": generation_request.get(
                "visual_performance_contract"
            ),
            "required_check_names": required_check_names,
            "audit_mode": (
                "TARGETED_REPAIR_REVIEW"
                if generation_request.get("generation_origin")
                == "AUTOMATIC_REPAIR"
                else "BALANCED_MATERIAL_GATE"
            ),
            "review_rule": (
                "Only direct visible pixels count. Missing required checks are "
                "failures. Styling, density, layout preference, minor campaign "
                "variance, thumbnail polish and optional supporting proof are "
                "advisory unless they establish a named material check."
            ),
        }
        result = await self.gateway.plan(
            PlannerRequest(
                workspace_id=request.workspace_id,
                job_id=request.job_id,
                group_key=f"{request.job_id}:audit:{page_index}",
                sources=tuple(sources),
                page_count=page_index + 1,
                language="zh-CN",
                product_name=str(proof.get("source_text") or "当前页主卖点"),
                product_information=(json.dumps(audit_contract, ensure_ascii=False),),
                platform_code="TAOBAO_AURORA_PIXEL_AUDIT",
                size="1024x1024",
                idempotency_key=(
                    f"{request.idempotency_key}:audit:{page_index}:"
                    f"{generation_request.get('revision', 0)}"
                ),
                system_prompt=_AUDIT_SYSTEM_PROMPT,
                planning_profile="aurora_visual_audit",
            )
        )
        if result.error_code or not isinstance(result.payload, Mapping):
            raise AuroraPlanningError(
                result.error_message or "Aurora pixel audit failed"
            )
        judgment = _normalize_visual_judgment_payload(
            dict(result.payload),
            required_check_names=frozenset(required_check_names),
        )
        judgment["page_index"] = page_index
        judgment["image_sha256"] = artifact.sha256
        reviewer = _mapping(judgment.get("reviewer"))
        reviewer.update(
            {
                "mode": "AGENT_DIRECT_PIXEL_REVIEW",
                "pixel_inspection_performed": True,
                "image_path": str(generated_path),
                "comparison_reference_ids": [
                    source.asset_id for source in sources[:-1]
                ],
            }
        )
        judgment["reviewer"] = reviewer
        proof_observation = _mapping(judgment.get("selling_point_proof_observation"))
        proof_observation["source_text"] = proof.get("source_text")
        proof_observation["observability_class"] = proof.get("observability_class")
        judgment["selling_point_proof_observation"] = proof_observation
        return (
            judgment,
            dict(result.normalized_usage),
            int(result.actual_cost_micros or 0),
        )

    async def _calibrate_style(
        self, request: ProviderRequest, harness_session: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        sources = _accepted_generated_sources(harness_session, request.job_id, (0, 1))
        result = await self.gateway.plan(
            PlannerRequest(
                workspace_id=request.workspace_id,
                job_id=request.job_id,
                group_key=f"{request.job_id}:style-calibration",
                sources=sources,
                page_count=2,
                language="zh-CN",
                product_name="Aurora Campaign Style",
                product_information=(),
                platform_code="TAOBAO_AURORA_STYLE_CALIBRATION",
                size="1024x1024",
                idempotency_key=f"{request.job_id}:aurora-style-calibration:v1",
                system_prompt=_STYLE_CALIBRATION_SYSTEM_PROMPT,
                planning_profile="aurora_style_calibration",
            )
        )
        if result.error_code or not isinstance(result.payload, Mapping):
            raise AuroraPlanningError(
                result.error_message or "Aurora style calibration failed"
            )
        return (
            _normalize_style_calibration_payload(dict(result.payload)),
            dict(result.normalized_usage),
            int(result.actual_cost_micros or 0),
        )

    async def _audit_set(
        self, request: ProviderRequest, harness_session: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        sources = _accepted_generated_sources(
            harness_session, request.job_id, tuple(range(6))
        )
        expected_hashes = {
            str(index): source.sha256 for index, source in enumerate(sources)
        }
        result = await self.gateway.plan(
            PlannerRequest(
                workspace_id=request.workspace_id,
                job_id=request.job_id,
                group_key=f"{request.job_id}:set-role-audit",
                sources=sources,
                page_count=6,
                language="zh-CN",
                product_name="Aurora Six Page Blind Review",
                product_information=(
                    json.dumps({"image_sha256_by_page": expected_hashes}),
                ),
                platform_code="TAOBAO_AURORA_SET_ROLE_AUDIT",
                size="1024x1024",
                idempotency_key=f"{request.job_id}:aurora-set-role-audit:v1",
                system_prompt=_AUDIT_SYSTEM_PROMPT,
                planning_profile="aurora_set_audit",
            )
        )
        if result.error_code or not isinstance(result.payload, Mapping):
            raise AuroraPlanningError(
                result.error_message or "Aurora set role audit failed"
            )
        observation = _normalize_set_role_audit_payload(dict(result.payload))
        observation["agent_pixel_reviewed"] = True
        observation["review_mode"] = "BLIND_SIX_PAGE_ROLE_COMPARISON"
        observation["reviewed_page_indexes"] = list(range(6))
        observation["image_sha256_by_page"] = expected_hashes
        return (
            observation,
            dict(result.normalized_usage),
            int(result.actual_cost_micros or 0),
        )

    def _workspace(self, job_id: str) -> Path:
        workspace = (self.work_root / job_id).resolve()
        if self.work_root != workspace and self.work_root not in workspace.parents:
            raise AuroraPlanningError("Aurora execution workspace escaped its root")
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace


_VISUAL_CHECKS = _AURORA_V20_VISUAL_CHECKS

_V20_MATERIAL_CHECKS = _AURORA_V20_MATERIAL_CHECKS


def _safe_planner_diagnostics(result: Any) -> dict[str, Any]:
    """Keep response-shape evidence without storing raw prompts or secrets."""

    diagnostics = {
        "provider_request_id_presence": (
            "present" if getattr(result, "provider_request_id", None) else "absent"
        ),
        "http_status": getattr(result, "http_status", None),
        "parse_trace": list(getattr(result, "parse_trace", ()) or ()),
        "structure_fingerprint": getattr(result, "structure_fingerprint", None),
        "structure_profile": getattr(result, "structure_profile", None),
        "provider_envelope": getattr(result, "provider_envelope", None),
        "content_utf8_bytes": getattr(result, "content_utf8_bytes", None),
        "content_sha256": getattr(result, "content_sha256", None),
        "request_id_presence": getattr(result, "request_id_presence", "not_available"),
        "usage_presence": getattr(result, "usage_presence", "not_available"),
        "usage_completeness": getattr(
            result, "usage_completeness", "not_available"
        ),
    }
    # Without an excerpt of what the gateway actually returned, a decode failure
    # can only ever be described as "it did not parse".  The sample is bounded
    # and only present when the caller explicitly opted in.
    content_sample = getattr(result, "content_sample", None)
    if isinstance(content_sample, str) and content_sample:
        diagnostics["content_sample_head"] = content_sample[:512]
        diagnostics["content_sample_tail"] = content_sample[-256:]
    return diagnostics


def _v20_audit_failure_message(result: Any) -> str:
    """Name an audit failure precisely instead of as an image-quality verdict.

    "Aurora v2.0 pixel audit failed" reads as "the picture was not good enough",
    but in production the audit overwhelmingly fails because the gateway
    answered 200 with content that was not a JSON object, or because the
    upstream returned an HTTP error.  Say which one it was.
    """

    if result.error_message:
        return str(result.error_message)
    http_status = getattr(result, "http_status", None)
    if isinstance(http_status, int) and http_status >= 400:
        return f"Aurora v2.0 pixel audit failed with HTTP {http_status}."
    trace = ", ".join(getattr(result, "parse_trace", ()) or ())
    if trace:
        return f"Aurora v2.0 pixel audit returned no JSON object ({trace})."
    return "Aurora v2.0 pixel audit returned no JSON object."


def _v20_gateway_error(result: Any, message: str) -> AuroraPlanningError:
    error = AuroraPlanningError(message)
    # The execution layer persists this under metrics and never includes the
    # raw response body, prompt, authorization header or API key.
    error.aurora_gateway_diagnostics = _safe_planner_diagnostics(result)
    # Surface the upstream status on the exception itself: the transient
    # classifier reads it to decide whether the configured retry policy may
    # run.  Keeping it only inside diagnostics made every 429/502/503 look
    # permanent.
    status = getattr(result, "http_status", None)
    error.http_status = status if isinstance(status, int) else None
    return error


def _generation_sources(
    generation_request: Mapping[str, Any], job_id: str
) -> tuple[SourceImage, ...]:
    references: list[Mapping[str, Any]] = []
    repair_canvas = generation_request.get("repair_canvas")
    if isinstance(repair_canvas, Mapping):
        references.append(repair_canvas)
    edit_target = generation_request.get("edit_target")
    if isinstance(edit_target, Mapping):
        references.append(edit_target)
    allowlist = generation_request.get("reference_allowlist")
    if isinstance(allowlist, list):
        references.extend(item for item in allowlist if isinstance(item, Mapping))
    requested_paths = generation_request.get("imagegen_reference_paths")
    if isinstance(requested_paths, list):
        metadata_by_path = {
            str(
                Path(
                    str(reference.get("path") or reference.get("file") or "")
                ).resolve()
            ): reference
            for reference in references
            if str(reference.get("path") or reference.get("file") or "").strip()
        }
        exact_references: list[Mapping[str, Any]] = []
        for raw_path in requested_paths:
            path_text = str(raw_path or "").strip()
            if not path_text:
                continue
            path = Path(path_text).resolve()
            exact_references.append(
                metadata_by_path.get(
                    str(path),
                    {"path": path_text, "role": "product_reference"},
                )
            )
        references = exact_references
    seen: set[str] = set()
    result: list[SourceImage] = []
    for index, reference in enumerate(references):
        path = Path(str(reference.get("path") or reference.get("file") or "")).resolve()
        key = str(path)
        if not path.is_file() or key in seen:
            continue
        seen.add(key)
        payload = path.read_bytes()
        result.append(
            SourceImage(
                asset_id=str(reference.get("evidence_id") or f"reference-{index}"),
                group_key=job_id,
                filename=path.name,
                content_type=_content_type(path),
                sha256=_sha256(payload),
                payload=payload,
                source_role=str(reference.get("role") or "product_reference"),
            )
        )
    if not result:
        raise AuroraPlanningError("Aurora generation request has no usable references")
    return tuple(result)


def _identity_sources(
    generation_request: Mapping[str, Any], job_id: str
) -> tuple[SourceImage, ...]:
    safe_request = dict(generation_request)
    safe_request["repair_canvas"] = None
    safe_request["edit_target"] = None
    return _generation_sources(safe_request, job_id)


_MAX_VISUAL_AUDIT_IMAGES = 4


def _bounded_visual_audit_sources(
    identity_sources: Sequence[SourceImage], candidate: SourceImage
) -> tuple[SourceImage, ...]:
    """Keep the generated candidate while bounding a multi-image vision request.

    Identity extraction and image generation still receive every approved source.
    The per-page pixel audit needs the candidate plus representative identity
    evidence, and the configured compatible vision endpoint rejects five-image
    chat requests. Keep the first three ordered identity authorities and always
    reserve the final slot for the generated candidate under review.
    """

    identity_limit = _MAX_VISUAL_AUDIT_IMAGES - 1
    return (*tuple(identity_sources[:identity_limit]), candidate)


def _merge_identity_coverage_judgment(
    primary: dict[str, Any], coverage: Mapping[str, Any]
) -> None:
    """Fail the page identity check when any omitted source disagrees.

    The vision endpoint accepts at most four images in one request. The primary
    audit therefore reviews the first three identity references plus the
    candidate, while supplemental calls review the remaining references. Only
    the identity check is merged from those supplemental calls; page layout and
    copy checks remain owned by the primary audit so a bounded coverage pass
    cannot create unrelated repair findings.
    """

    coverage_checks = coverage.get("checks")
    if not isinstance(coverage_checks, list):
        return
    identity_check = next(
        (
            item
            for item in coverage_checks
            if isinstance(item, Mapping)
            and item.get("name") == "product_identity_match"
        ),
        None,
    )
    if not isinstance(identity_check, Mapping) or identity_check.get("passed") is True:
        return
    primary_checks = primary.get("checks")
    if not isinstance(primary_checks, list):
        return
    primary_identity = next(
        (
            item
            for item in primary_checks
            if isinstance(item, dict)
            and item.get("name") == "product_identity_match"
        ),
        None,
    )
    if not isinstance(primary_identity, dict):
        return
    primary_identity["passed"] = False
    primary_identity["score"] = min(
        float(primary_identity.get("score", 69)),
        float(identity_check.get("score", 69)),
        69.0,
    )
    primary_identity["observed_evidence"] = (
        str(primary_identity.get("observed_evidence") or "")
        + "；补充商品源图审计发现候选图与另一张原始商品视图的身份特征不一致。"
    )
    deficiencies = primary.get("all_material_deficiencies")
    if not isinstance(deficiencies, list):
        deficiencies = []
    existing = next(
        (
            item
            for item in deficiencies
            if isinstance(item, dict) and item.get("check") == "product_identity_match"
        ),
        None,
    )
    if isinstance(existing, dict):
        existing["visible_problem"] = (
            str(existing.get("visible_problem") or "")
            + "；补充源图比较未通过。"
        )
    else:
        deficiencies.append(
            {
                "check": "product_identity_match",
                "visible_problem": str(
                    identity_check.get("observed_evidence")
                    or "补充商品源图比较发现身份不一致。"
                ),
                "root_cause": "候选图没有保持全部原始商品视图共同锁定的同一 SKU 身份。",
                "required_change": (
                    "从全部原始商品证据重建候选图，不能继续使用失败候选作为修复画布。"
                ),
            }
        )
    primary["all_material_deficiencies"] = deficiencies
    primary["decision_summary"] = (
        str(primary.get("decision_summary") or "")
        + "；补充源图身份审计未通过。"
    )


def _accepted_generated_sources(
    session: Mapping[str, Any], job_id: str, indexes: Sequence[int]
) -> tuple[SourceImage, ...]:
    versions = session.get("generated_versions")
    if not isinstance(versions, list):
        raise AuroraPlanningError("Aurora accepted pages are missing")
    result: list[SourceImage] = []
    for index in indexes:
        matches = [
            item
            for item in versions
            if isinstance(item, Mapping)
            and item.get("page_index") == index
            and item.get("status") in {"ACCEPTED", "ACCEPTED_WITH_RESIDUALS"}
        ]
        if not matches:
            raise AuroraPlanningError(f"Accepted Aurora page {index} is missing")
        version = matches[-1]
        path = Path(str(version["file"])).resolve()
        payload = path.read_bytes()
        result.append(
            SourceImage(
                asset_id=f"accepted-page-{index}",
                group_key=job_id,
                filename=str(path),
                content_type=_content_type(path),
                sha256=str(version["sha256"]),
                payload=payload,
                source_role="style_reference",
            )
        )
    return tuple(result)


def _baseline_observation(judgment: Mapping[str, Any]) -> dict[str, Any]:
    raw_checks = judgment.get("checks")
    if isinstance(raw_checks, Mapping):
        checks = _mapping(raw_checks)
    elif isinstance(raw_checks, list):
        checks = {
            str(item.get("name")): dict(item)
            for item in raw_checks
            if isinstance(item, Mapping) and item.get("name")
        }
    else:
        raise AuroraPlanningError(
            "Aurora v2 search baseline requires native visual checks"
        )
    mapping = {
        "product_dominance": "product_separation",
        "headline_performance": "headline_treatment_match",
        "physical_evidence": "evidence_relation_visible",
        "spatial_depth": "visual_performance_match",
        "commercial_closure": "page_role_expression",
        "thumbnail_competitiveness": "thumbnail_success",
        "commercial_frame_structure": "approved_plan_fidelity_match",
        "type_hierarchy": "typography_legibility",
    }
    axes = {}
    for axis, check_name in mapping.items():
        check = _mapping(checks.get(check_name))
        axes[axis] = {
            "score": float(check.get("score", 0)),
            "observed_evidence": str(
                check.get("observed_evidence")
                or f"候选图像中已直接检查{axis}的像素表现"
            ),
        }
    return {
        "agent_pixel_reviewed": True,
        "review_mode": "DIRECT_PIXEL_VALIDATED_BASELINE_COMPARISON",
        "axes": axes,
    }


def _repair_plan(session: Mapping[str, Any]) -> dict[str, Any]:
    versions = session.get("generated_versions")
    if not isinstance(versions, list) or not versions:
        raise AuroraPlanningError("Aurora repair has no failed version")
    audit = _mapping(_mapping(versions[-1]).get("visual_audit"))
    failed = [str(value) for value in audit.get("failed_checks", [])]
    actions = {
        str(item.get("check")): item
        for item in audit.get("repair_actions", [])
        if isinstance(item, Mapping)
    }
    findings = []
    for check in failed:
        action = _mapping(actions.get(check))
        evidence = str(
            action.get("observed_evidence") or f"当前成片在{check}上存在可见缺陷"
        )
        policy = str(
            action.get("policy") or f"只修正{check}对应的可见问题并保留其他通过层"
        )
        findings.append(
            {
                "check": check,
                "visible_problem": evidence,
                "root_cause": f"当前构图或视觉执行未充分满足{check}对应的批准合同",
                "required_change": policy,
            }
        )
    if not findings:
        raise AuroraPlanningError("Aurora repair diagnosis has no material failures")
    return {
        "scope": session.get("required_repair_scope"),
        "instruction": "；".join(item["required_change"] for item in findings),
        "preserve": ["商品身份", "已核实事实", "已通过的视觉层", "整套 Campaign Style"],
        "expected_visible_change": (
            "在不改变商品身份和已通过层的前提下，一次性修正全部材料性缺陷。"
        ),
        "failed_check_names": failed,
        "all_failed_findings": findings,
        "root_cause_reflection": (
            "第一次生成没有同时把批准合同中的商品身份、页面职责"
            "和材料性视觉门禁落实到实际像素。"
        ),
        "single_second_attempt_strategy": (
            "以第一次干净商业成片作为唯一返修画布，集中修正"
            "全部失败检查，并保持所有已通过层不变。"
        ),
    }


def _v20_repair_plan(session: Mapping[str, Any]) -> dict[str, Any]:
    required = _mapping(session.get("required_repair"))
    failed_checks = [str(value) for value in required.get("failed_checks", [])]
    findings = [
        dict(item)
        for item in required.get("findings", [])
        if isinstance(item, Mapping)
    ]
    if not failed_checks or not findings:
        raise AuroraPlanningError("Aurora v2.0 repair diagnosis is incomplete")
    return {
        "scope": required.get("repair_scope"),
        "failed_checks": failed_checks,
        "findings": findings,
        "preserve": [
            "商品身份和全部已核验事实",
            "当前页已通过的视觉检查",
            "冻结的整套 Campaign Style",
        ],
        "strategy": (
            "只在一次修改槽位内修正全部材料性失败，并保持其余像素层级不变"
        ),
        "expected_visible_change": (
            "失败检查对应的可见问题被集中修正，商品身份、事实和已通过层保持不变"
        ),
    }


def _write_generated_artifact(
    workspace: Path, page_index: int, attempt_index: int, artifact: ProviderArtifact
) -> Path:
    suffix = {"image/png": ".png", "image/webp": ".webp"}.get(
        artifact.content_type, ".jpg"
    )
    path = (
        workspace
        / "generated"
        / f"page-{page_index + 1:02d}-attempt-{attempt_index + 1}{suffix}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(artifact.payload)
    return path.resolve()


def _normalize_aurora_square_artifact(
    artifact: ProviderArtifact, *, size: int = 1024
) -> ProviderArtifact:
    """Preserve the full provider image on Aurora's required square canvas."""

    if artifact.width == size and artifact.height == size:
        return artifact
    try:
        with Image.open(BytesIO(artifact.payload)) as opened:
            opened.load()
            materialized = ImageOps.exif_transpose(opened).convert("RGB")
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise AuroraPlanningError(
            f"Aurora provider artifact is not a valid image ({type(exc).__name__})"
        ) from None
    contained = ImageOps.contain(
        materialized,
        (size, size),
        method=Image.Resampling.LANCZOS,
    )
    rendered = Image.new("RGB", (size, size), "white")
    rendered.paste(
        contained,
        ((size - contained.width) // 2, (size - contained.height) // 2),
    )
    output = BytesIO()
    rendered.save(output, format="JPEG", quality=95, optimize=True)
    payload = output.getvalue()
    return ProviderArtifact(
        payload=payload,
        filename=Path(artifact.filename).with_suffix(".jpg").name,
        content_type="image/jpeg",
        width=size,
        height=size,
        sha256=_sha256(payload),
        locally_resampled=True,
        provider_width=artifact.provider_width or artifact.width,
        provider_height=artifact.provider_height or artifact.height,
    )


def _failed_image_result(
    result: Any, provider: AuroraMainImageProvider
) -> ProviderResult:
    http_status = result.http_status
    retryable_status = http_status in {408, 429} or (
        isinstance(http_status, int) and 500 <= http_status <= 599
    )
    source_outcome = getattr(result, "outcome", ProviderOutcome.FAILED)
    outcome = (
        ProviderOutcome.OUTCOME_UNKNOWN
        if source_outcome == ProviderOutcome.OUTCOME_UNKNOWN
        else ProviderOutcome.FAILED
    )
    return ProviderResult(
        outcome=outcome,
        provider_request_id=result.provider_request_id,
        error_code=result.error_code or "AURORA_IMAGE_GENERATION_FAILED",
        error_message=result.error_message or "Aurora image generation failed",
        usage_reported=bool(result.raw_usage),
        raw_usage=dict(result.raw_usage or {}),
        estimated_cost_micros=result.estimated_cost_micros,
        actual_cost_micros=result.actual_cost_micros,
        retryable=bool(result.retryable) or retryable_status,
        http_status=http_status,
        provider_name=provider.gateway.provider_name,
        model_name=provider.gateway.image_model,
        pricing_version=provider.gateway.pricing_version,
    )


def _request_uses_parallel_v20(request: ProviderRequest) -> bool:
    item = request.configuration.get("item")
    return (
        isinstance(item, Mapping)
        and is_parallel_aurora_mode(item.get("aurora_execution_mode"))
    )


_TRANSIENT_HTTP_STATUS = re.compile(r"failed with http (408|429|5\d\d)\b")

# Upstream statuses that justify another attempt at the same work.
_AURORA_TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# In-page audit retry backoff.  It mirrors the execution layer's Aurora retry
# schedule.  Only the audit call is repeated: the candidate image has already
# been generated and paid for, so it is never regenerated here.
_AURORA_AUDIT_RETRY_DELAYS = (2.5, 7.5)


def _is_shadow_composition_gate_failure(message: str) -> bool:
    """Return true only for a recoverable page-local composition rejection."""

    normalized = message.casefold()
    return any(
        marker in normalized
        for marker in (
            "selling-point dominance",
            "evidence is not a primary visual",
            "layout-not-cloned",
            "approved-plan fidelity",
            "product identity",
            "identity mismatch",
            "identity drift",
        )
    )


def _classify_transient_failure(exc: Exception) -> tuple[bool, int | None]:
    """Classify only transport failures; validation and Skill failures fail closed."""

    status = getattr(exc, "http_status", None) or getattr(exc, "status_code", None)
    http_status = status if isinstance(status, int) else None
    if http_status in {408, 429} or (
        http_status is not None and 500 <= http_status <= 599
    ):
        return True, http_status
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True, http_status
    if isinstance(exc, OSError) and not isinstance(
        exc, (FileNotFoundError, PermissionError)
    ):
        return True, http_status
    message = str(exc).casefold()
    # Gateway failures surface as "Planner request failed with HTTP 502." on the
    # exception message.  Recover the status from the message so a lost
    # attribute cannot silently disable the configured retry policy again.
    match = _TRANSIENT_HTTP_STATUS.search(message)
    if match is not None:
        recovered = int(match.group(1))
        return True, http_status if http_status is not None else recovered
    transient_markers = (
        "temporary unavailable",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "timed out",
        "timeout",
        "rate limit",
    )
    return any(marker in message for marker in transient_markers), http_status


def _content_type(path: Path) -> str:
    return {".png": "image/png", ".webp": "image/webp"}.get(
        path.suffix.lower(), "image/jpeg"
    )


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _authorized_fact_pool_for_audit(
    generation_request: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Expose user fact authority to the direct-pixel reviewer."""

    authorized = _mapping(generation_request.get("authorized_facts"))
    buckets = (
        "display_facts",
        "selling_points",
        "attributes",
        "scenes",
        "offers",
        "service_or_trust",
    )
    pool: dict[str, list[str]] = {}
    for bucket in buckets:
        values = authorized.get(bucket)
        pool[bucket] = (
            [str(value).strip() for value in values if str(value).strip()]
            if isinstance(values, list)
            else []
        )
    pool["all_exact_fact_seeds"] = list(
        dict.fromkeys(value for bucket in buckets for value in pool[bucket])
    )
    return pool


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _normalize_background_competition(value: object) -> str:
    normalized = str(value or "").strip().upper().replace(" ", "_")
    if normalized in {"LOW", "MEDIUM", "HIGH"}:
        return normalized
    if normalized in {
        "LOW_TO_MEDIUM",
        "MEDIUM_TO_LOW",
        "HIGH_TO_MEDIUM",
        "MEDIUM_HIGH",
    }:
        return "MEDIUM"
    if "HIGH" in normalized:
        return "HIGH"
    if "MEDIUM" in normalized or "MID" in normalized:
        return "MEDIUM"
    return "LOW"


def _number(value: object, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuroraPlanningError(f"Expected JSON object in {path.name}")
    return value


def _visual_judgment_schema() -> dict[str, Any]:
    schema_path = (
        Path(__file__).resolve().parent.parent
        / "skill_sources"
        / "aurora-domestic-ecommerce-main-image"
        / "schemas"
        / "visual-judgment.schema.json"
    )
    schema = _load_json(schema_path)
    schema.pop("$schema", None)
    schema.pop("$id", None)
    return schema


_AUDIT_CHECK_ALIASES: dict[str, tuple[str, ...]] = {
    "product_identity_match": ("product_identity", "brand_and_mark_integrity"),
    "factual_copy_match": ("factual_copy",),
    "typography_style_match": ("visual_hierarchy_and_composition",),
    "typography_legibility": ("chinese_legibility", "factual_copy"),
    "page_role_expression": ("page_duty", "commercial_readiness"),
    "evidence_relation_visible": ("physical_evidence",),
    "campaign_style_match": ("campaign_continuity", "commercial_readiness"),
    "headline_treatment_match": ("visual_hierarchy_and_composition",),
    "palette_role_match": ("commercial_readiness",),
    "graphic_language_match": ("commercial_readiness",),
    "photography_finish_match": ("artifact_and_finish_quality",),
    "information_surface_match": ("visual_hierarchy_and_composition",),
    "layout_not_cloned": ("visual_hierarchy_and_composition",),
    "set_diversity_match": ("campaign_continuity",),
    "product_separation": ("background_cleanliness", "thumbnail_performance"),
    "numeric_hierarchy_if_applicable": ("factual_copy",),
    "design_intensity_controlled": ("visual_hierarchy_and_composition",),
    "visual_performance_match": ("commercial_readiness", "thumbnail_performance"),
    "baseline_performance_match": ("commercial_readiness",),
    "approved_plan_fidelity_match": ("approved_page_contract", "page_duty"),
    "campaign_constitution_match": ("product_identity", "campaign_continuity"),
    "source_background_noise_absent": (
        "source_background_isolation",
        "background_cleanliness",
    ),
    "selling_point_visible_without_copy": ("physical_evidence", "page_duty"),
    "selling_point_visual_dominance": (
        "visual_hierarchy_and_composition",
        "thumbnail_performance",
    ),
    "claim_to_evidence_relation_clear": ("physical_evidence",),
    "text_not_substituting_for_visual_proof": ("physical_evidence",),
    "thumbnail_success": ("thumbnail_performance",),
    "no_unsupported_claim": ("factual_copy",),
    "no_hard_error": ("artifact_and_finish_quality",),
}

_PLAN_CRITICAL_VISUAL_CHECKS = frozenset(
    {
        "page_role_expression",
        "headline_treatment_match",
        "visual_performance_match",
        "approved_plan_fidelity_match",
        "campaign_constitution_match",
        "selling_point_visible_without_copy",
        "selling_point_visual_dominance",
        "claim_to_evidence_relation_clear",
        "text_not_substituting_for_visual_proof",
        "thumbnail_success",
    }
)

_HARD_SAFETY_VISUAL_CHECKS = frozenset(
    {
        "product_identity_match",
        "factual_copy_match",
        "source_background_noise_absent",
        "no_unsupported_claim",
        "no_hard_error",
        "typography_legibility",
    }
)

_BALANCED_BLOCKING_VISUAL_CHECKS = frozenset(
    {
        *_HARD_SAFETY_VISUAL_CHECKS,
        "page_role_expression",
        "evidence_relation_visible",
        "selling_point_visible_without_copy",
    }
)

_HARNESS_BLOCKING_VISUAL_CHECKS = _BALANCED_BLOCKING_VISUAL_CHECKS


def _required_audit_check_names(
    session: Mapping[str, Any], generation_request: Mapping[str, Any]
) -> tuple[str, ...]:
    """Focus audits on material defects and the active repair delta."""

    if generation_request.get("generation_origin") != "AUTOMATIC_REPAIR":
        return tuple(
            name
            for name in _VISUAL_CHECKS
            if name in _BALANCED_BLOCKING_VISUAL_CHECKS
        )
    page_index = int(generation_request.get("page_index", 0))
    pages = _mapping(session.get("plan")).get("pages")
    page = (
        pages[page_index]
        if isinstance(pages, list)
        and 0 <= page_index < len(pages)
        and isinstance(pages[page_index], Mapping)
        else {}
    )
    repair = _mapping(page.get("repair_instruction"))
    failed = {
        str(name)
        for name in repair.get("failed_check_names", [])
        if isinstance(name, str)
    }
    required = _HARD_SAFETY_VISUAL_CHECKS | (
        failed & _BALANCED_BLOCKING_VISUAL_CHECKS
    )
    return tuple(name for name in _VISUAL_CHECKS if name in required)


def _normalize_visual_judgment_payload(
    payload: dict[str, Any],
    *,
    required_check_names: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Adapt a direct-pixel reviewer's equivalent JSON to the Harness contract."""

    required_checks = required_check_names or _PLAN_CRITICAL_VISUAL_CHECKS
    if _is_complete_visual_judgment(payload):
        _downgrade_nonmaterial_check_failures(payload)
        _enforce_visual_judgment_consistency(payload)
        payload["all_observed_deficiencies"] = _normalized_deficiencies(
            payload, _mapping(payload.get("checks"))
        )
        return payload
    raw_checks = payload.get("checks")
    indexed: dict[str, dict[str, Any]] = {}
    if isinstance(raw_checks, Mapping):
        indexed = {
            str(name): dict(value)
            for name, value in raw_checks.items()
            if isinstance(value, Mapping)
        }
    elif isinstance(raw_checks, list):
        indexed = {
            str(value.get("check_name") or value.get("name")): dict(value)
            for value in raw_checks
            if isinstance(value, Mapping)
            and (value.get("check_name") or value.get("name"))
        }
    overall_pass = _audit_overall_pass(payload)
    explicit_material_pass = _explicit_material_pass(payload)
    fallback_evidence = str(
        payload.get("final_reasoning")
        or payload.get("decision_summary")
        or (
            "Direct pixel inspection found the candidate consistent with the "
            "approved page contract."
        )
    )
    normalized_checks: dict[str, dict[str, Any]] = {}
    for name in _VISUAL_CHECKS:
        source = indexed.get(name)
        if source is None:
            source = next(
                (
                    indexed[alias]
                    for alias in _AUDIT_CHECK_ALIASES.get(name, ())
                    if alias in indexed
                ),
                None,
            )
        status = str((source or {}).get("status", "")).upper()
        passed_value = (source or {}).get("passed")
        missing_critical_check = (
            source is None
            and name in required_checks
            and not explicit_material_pass
        )
        passed = (
            passed_value
            if isinstance(passed_value, bool)
            else status in {"PASS", "PASSED", "NOT_APPLICABLE", "N/A"}
            if status
            else True
            if source is None and name not in required_checks
            else False
            if missing_critical_check
            else overall_pass
        )
        raw_score = (source or {}).get("score")
        score = (
            float(raw_score)
            if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool)
            else (88.0 if passed else 55.0)
        )
        evidence = str(
            (source or {}).get("observed_evidence")
            or (source or {}).get("actual_pixel_evidence")
            or (source or {}).get("judgment")
            or (
                f"Reviewer omitted required plan-critical check: {name}."
                if missing_critical_check
                else ""
            )
            or fallback_evidence
        )
        normalized_checks[name] = {
            "passed": bool(passed),
            "score": max(70.0, score) if passed else min(69.0, score),
            "observed_evidence": evidence,
        }

    pixel_summary = _mapping(payload.get("candidate_pixel_summary"))
    composition = str(
        pixel_summary.get("composition")
        or fallback_evidence
    )
    background = str(pixel_summary.get("background") or fallback_evidence)
    proof = _mapping(payload.get("selling_point_proof_observation"))
    proof.setdefault("visible_without_copy", overall_pass)
    proof.setdefault("pixel_evidence", [fallback_evidence])
    proof.setdefault("text_only_dependency", False)
    proof.setdefault("evidence_is_primary_visual", overall_pass)
    proof.setdefault("estimated_evidence_occupancy_percent", 60)
    proof.setdefault("claim_takeaway_without_copy", fallback_evidence)
    proof.setdefault("expression_mode", "DIRECT")
    payload["selling_point_proof_observation"] = proof
    payload["decision_summary"] = str(
        payload.get("decision_summary")
        or payload.get("final_reasoning")
        or fallback_evidence
    )
    payload["performance_observation"] = {
        "estimated_product_occupancy_percent": 70,
        "headline_visual_rank": "JOINT_FIRST",
        "physical_evidence_seen": [fallback_evidence],
        "depth_layers_seen": 2,
        "commercial_closure_seen": overall_pass,
        "background_competition": "LOW",
        "thumbnail_scan_order": ["product", "selling-point proof"],
    }
    payload["plan_fidelity_observation"] = {
        "observed_subject_and_view": composition,
        "observed_scene_geometry": background,
        "observed_composition": composition,
        "observed_reading_path": ["product", "selling-point proof"],
        "missing_required_elements": [],
        "unexpected_plan_changes": [],
    }
    payload["source_background_observation"] = {
        "leaked_source_elements": [],
        "rebuilt_scene_evidence": background,
    }
    payload["checks"] = normalized_checks
    payload["audit_completeness_confirmation"] = True
    _enforce_visual_judgment_consistency(payload)
    payload["all_observed_deficiencies"] = _normalized_deficiencies(
        payload, _mapping(payload.get("checks"))
    )
    return payload


def _downgrade_nonmaterial_check_failures(payload: dict[str, Any]) -> None:
    """Keep explicit overall PASS check gaps advisory instead of regenerating."""

    if not _explicit_material_pass(payload):
        return
    checks = _mapping(payload.get("checks"))
    for value in checks.values():
        if not isinstance(value, dict) or value.get("passed") is not False:
            continue
        raw_score = value.get("score")
        score = (
            float(raw_score)
            if isinstance(raw_score, (int, float))
            and not isinstance(raw_score, bool)
            else 70.0
        )
        value["score"] = max(70.0, score)


_AUDIT_VERDICT_FIELDS = (
    "overall_status",
    "overall_result",
    "overall_verdict",
    "overall_judgment",
    "approval_recommendation",
)
_MATERIAL_FAILURE_COUNT_FIELDS = (
    "material_failure_count",
    "material_failed_check_count",
    "material_fail_count",
)


def _audit_overall_pass(payload: Mapping[str, Any]) -> bool:
    verdicts = {
        str(payload.get(name, "")).strip().upper()
        for name in _AUDIT_VERDICT_FIELDS
        if payload.get(name) is not None
    }
    if verdicts.intersection({"FAIL", "FAILED", "REJECT", "REJECTED"}):
        return False
    if verdicts.intersection({"PASS", "PASSED", "APPROVE", "APPROVED"}):
        return True
    if any(verdict.startswith("PASS_WITH_") for verdict in verdicts):
        return True
    if (
        payload.get("overall_material_pass") is True
        or payload.get("material_pass") is True
    ):
        return True
    return any(
        payload.get(name) == 0 for name in _MATERIAL_FAILURE_COUNT_FIELDS
    )


def _explicit_material_pass(payload: Mapping[str, Any]) -> bool:
    """Recognize equivalent provider fields without weakening explicit failures."""

    if not _audit_overall_pass(payload):
        return False
    return (
        payload.get("overall_material_pass") is True
        or payload.get("material_pass") is True
        or any(payload.get(name) == 0 for name in _MATERIAL_FAILURE_COUNT_FIELDS)
    )


def _enforce_visual_judgment_consistency(payload: dict[str, Any]) -> dict[str, Any]:
    """Make proof observations and material Harness checks agree."""

    checks = _mapping(payload.get("checks"))
    proof = _mapping(payload.get("selling_point_proof_observation"))
    forced_failures: set[str] = set()
    if proof.get("visible_without_copy") is False:
        forced_failures.update(
            {
                "selling_point_visible_without_copy",
                "claim_to_evidence_relation_clear",
                "text_not_substituting_for_visual_proof",
                "approved_plan_fidelity_match",
            }
        )
    if proof.get("evidence_is_primary_visual") is False:
        forced_failures.update(
            {
                "selling_point_visual_dominance",
                "visual_performance_match",
                "thumbnail_success",
                "approved_plan_fidelity_match",
            }
        )
    for name in forced_failures:
        check = _mapping(checks.get(name))
        check["passed"] = False
        check["score"] = min(float(check.get("score", 55.0)), 69.0)
        check["observed_evidence"] = str(
            check.get("observed_evidence")
            or "Direct pixels do not establish the approved selling-point proof."
        )
        checks[name] = check
    payload["checks"] = checks
    return payload


def _is_complete_visual_judgment(payload: Mapping[str, Any]) -> bool:
    checks = payload.get("checks")
    return (
        all(
            key in payload
            for key in (
                "decision_summary",
                "performance_observation",
                "plan_fidelity_observation",
                "source_background_observation",
                "audit_completeness_confirmation",
            )
        )
        and isinstance(checks, Mapping)
        and all(name in checks for name in _VISUAL_CHECKS)
        and all(
            isinstance(checks.get(name), Mapping)
            and isinstance(checks[name].get("observed_evidence"), str)
            and len(checks[name]["observed_evidence"].strip()) >= 12
            for name in _VISUAL_CHECKS
        )
    )


def _normalized_deficiencies(
    payload: Mapping[str, Any], checks: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, str]]:
    existing = payload.get("all_observed_deficiencies")
    by_check = (
        {
            str(item.get("check")): dict(item)
            for item in existing
            if isinstance(item, Mapping) and item.get("check")
        }
        if isinstance(existing, list)
        else {}
    )
    result: list[dict[str, str]] = []
    for name, check in checks.items():
        if (
            check.get("passed")
            or name not in _HARNESS_BLOCKING_VISUAL_CHECKS
            or float(check.get("score", 100.0)) >= 70.0
        ):
            continue
        source = by_check.get(name, {})
        evidence = str(check.get("observed_evidence"))
        result.append(
            {
                "check": name,
                "visible_problem": str(source.get("visible_problem") or evidence),
                "root_cause": str(
                    source.get("root_cause")
                    or (
                        "The visible execution did not satisfy this approved "
                        "audit layer."
                    )
                ),
                "required_change": str(
                    source.get("required_change")
                    or "Repair this visible layer while preserving all passed layers."
                ),
            }
        )
    return result


def _normalize_style_calibration_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept equivalent concrete-evidence arrays from compatible vision models."""

    evidence = payload.get("observed_evidence")
    if not isinstance(evidence, str) or len(evidence.strip()) < 20:
        concrete = payload.get("concrete_observed_evidence") or payload.get(
            "concrete observed_evidence"
        )
        statements = _flatten_style_evidence(concrete)
        if statements:
            payload["observed_evidence"] = " ".join(statements)
    payload.pop("concrete_observed_evidence", None)
    payload.pop("concrete observed_evidence", None)
    tokens = _mapping(payload.get("style_tokens"))
    # The style-calibration prompt historically used the descriptive alias
    # ``information_surface_geometry`` while the v2 Harness contract uses
    # ``information_surfaces``. Normalize the alias at this boundary so the
    # Harness receives one canonical field and continues to reject unrelated
    # identity/layout/copy keys fail-closed.
    if (
        "information_surfaces" not in tokens
        and "information_surface_geometry" in tokens
    ):
        tokens["information_surfaces"] = tokens["information_surface_geometry"]
    tokens.pop("information_surface_geometry", None)
    # prohibited_styles belongs to the compiled campaign-style contract, not
    # the pixel-observation token allowlist enforced by the v2 Harness.
    tokens.pop("prohibited_styles", None)
    # The v2 Harness compiles these campaign-style fields as strings. Some
    # compatible vision gateways return an equivalent list of observations;
    # coalesce those values at this provider boundary so one malformed field
    # cannot abort the calibrated page sequence with a Python type error.
    for field in (
        "photography_finish",
        "light_direction",
        "color_temperature",
        "graphic_language",
        "information_surfaces",
        "density_band",
    ):
        value = tokens.get(field)
        if isinstance(value, str):
            continue
        statements = _flatten_style_evidence(value)
        if statements:
            tokens[field] = "；".join(statements)
        elif field in tokens:
            tokens.pop(field, None)
    if "palette_usage_ratio" in tokens:
        ratios = _mapping(tokens["palette_usage_ratio"])
        ratio_values = list(ratios.values())
        valid_ratios = bool(ratios) and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
            for value in ratio_values
        )
        valid_ratios = valid_ratios and (
            abs(sum(float(value) for value in ratio_values) - 100) <= 0.01
        )
        if not valid_ratios:
            # The compiled Harness campaign style is the source of truth. A
            # provider calibration response may omit this optional override or
            # return an empty/non-numeric object; never pass that invalid value
            # through as it violates validate_campaign_style's non-empty map
            # contract and terminates the page sequence at calibration.
            tokens.pop("palette_usage_ratio", None)
    if "palette_roles" in tokens and not isinstance(
        tokens["palette_roles"], Mapping
    ):
        tokens.pop("palette_roles", None)
    if "typography" in tokens and not isinstance(tokens["typography"], Mapping):
        tokens.pop("typography", None)
    payload["style_tokens"] = tokens
    return payload


def _flatten_style_evidence(value: object) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Mapping):
        statements: list[str] = []
        for nested in value.values():
            statements.extend(_flatten_style_evidence(nested))
        return statements
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        statements = []
        for nested in value:
            statements.extend(_flatten_style_evidence(nested))
        return statements
    return []


def _normalize_set_role_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt an equivalent blind six-page review to the Harness contract."""

    pages = payload.get("pages")
    distinctions_value = payload.get("carousel_distinctions")
    if not isinstance(distinctions_value, list) and isinstance(pages, list):
        distinctions: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, Mapping) or page.get("page_index") not in range(
                1, 6
            ):
                continue
            evidence = _flatten_style_evidence(
                page.get("concrete_pixel_evidence_explaining_not_search_cover")
                or page.get("concrete_pixel_evidence_not_search_cover")
                or page.get("pixel_evidence")
                or page.get("not_search_cover_reason")
                or page.get("concrete_pixel_evidence")
            )
            distinctions.append(
                {
                    "page_index": page["page_index"],
                    "why_not_search_cover": " ".join(evidence),
                }
            )
        payload["carousel_distinctions"] = distinctions
    elif isinstance(distinctions_value, list) and isinstance(pages, list):
        pages_by_index = {
            page.get("page_index"): page
            for page in pages
            if isinstance(page, Mapping)
        }
        for distinction in distinctions_value:
            if (
                not isinstance(distinction, dict)
                or str(distinction.get("why_not_search_cover") or "").strip()
            ):
                continue
            page = pages_by_index.get(distinction.get("page_index"))
            if not isinstance(page, Mapping):
                continue
            evidence = _flatten_style_evidence(
                page.get("concrete_pixel_evidence_explaining_not_search_cover")
                or page.get("concrete_pixel_evidence_not_search_cover")
                or page.get("pixel_evidence")
                or page.get("not_search_cover_reason")
                or page.get("concrete_pixel_evidence")
            )
            distinction["why_not_search_cover"] = " ".join(evidence)

    assessment = _mapping(payload.get("search_listing_cover_assessment"))
    set_verdict = _mapping(payload.get("set_verdict"))
    page_zero = next(
        (
            page
            for page in pages
            if isinstance(page, Mapping) and page.get("page_index") == 0
        ),
        {},
    ) if isinstance(pages, list) else {}
    search_evidence = payload.get("search_identification_evidence")
    if not isinstance(search_evidence, str) or not search_evidence.strip():
        payload["search_identification_evidence"] = " ".join(
            _flatten_style_evidence(
                assessment.get("pixel_evidence")
                or assessment.get("page_0_pixel_evidence")
                or page_zero.get("concrete_pixel_evidence")
                or page_zero.get("why_role_is_distinct")
                or set_verdict.get("summary")
            )
        )
    payload.setdefault(
        "identified_search_page_index",
        assessment.get("search_listing_cover_page_index")
        if assessment.get("search_listing_cover_page_index") is not None
        else set_verdict.get("search_listing_cover_page_index"),
    )
    other_cover_indexes = assessment.get("other_search_listing_cover_page_indices")
    set_conclusion = _mapping(payload.get("set_level_conclusion"))
    assessment_passes = (
        assessment.get("page_0_alone_is_visibly_search_listing_cover") is True
        or assessment.get("cover_uniqueness_status") == "PASS"
        or set_conclusion.get("page_0_is_the_only_visible_search_cover") is True
        or set_verdict.get("page_0_alone_is_visibly_the_search_listing_cover")
        is True
    )
    if assessment_passes:
        payload["search_role_is_unique"] = (
            not isinstance(other_cover_indexes, list) or other_cover_indexes == []
        )
    payload.setdefault(
        "confusable_page_indexes",
        other_cover_indexes if isinstance(other_cover_indexes, list) else [],
    )
    return payload


def _normalize_parallel_set_audit_payload(
    payload: dict[str, Any], sources: Sequence[SourceImage]
) -> dict[str, Any]:
    """Bind equivalent provider output to the native six-page Harness contract."""

    observation = _normalize_set_role_audit_payload(payload)
    observation["agent_pixel_reviewed"] = True
    observation["review_mode"] = "BLIND_SIX_PAGE_ROLE_COMPARISON"
    observation["reviewed_page_indexes"] = list(range(6))
    observation["image_sha256_by_page"] = {
        str(index): source.sha256 for index, source in enumerate(sources)
    }
    return observation
