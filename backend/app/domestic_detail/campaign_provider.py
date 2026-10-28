"""Execute one source-bound optional campaign model review under public leases."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..aurora_detail_runtime import (
    AuroraDetailHarnessRunner,
    detail_workspace,
)
from ..models import AssetRecord, JobRecord
from ..provider import ProviderOutcome, ProviderRequest, ProviderResult
from ..provider_quota import require_provider_job_quota
from ..storage import ImageStorage
from .campaign_durability import (
    JOURNAL,
    bytes_once,
    json_once,
    load_result,
    recover_response,
    save_result,
)
from .campaign_jobs import CAMPAIGN_JOB_VERSION, CAMPAIGN_PROFILE, source_scope
from .campaign_materialization import (
    materialize_campaign_review,
    validate_campaign_preview,
)
from .campaign_protocol import CampaignObservationError
from .delivery import _json
from .generation import runtime_source_hash
from .planner_dispatch import (
    result_with_dispatch,
    select_route,
    validate_dispatch_result,
)
from .plans import canonical_hash
from .review import ReviewStore, approved_review_authority
from .review_packet import _metadata


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _bytes_once(path: Path, payload: bytes) -> None:
    bytes_once(path, payload)


def _once(path: Path, value: dict[str, Any]) -> None:
    json_once(path, value)


class DomesticCampaignReviewProvider:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: Any,
        storage: ImageStorage,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.storage = storage
        self.name = gateway.provider_name
        self.model = gateway.planner_model
        self.pricing_version = gateway.pricing_version

    def describe_route(self) -> dict[str, Any]:
        return select_route(self.gateway)

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        if (
            request.configuration.get("job", {}).get("version")
            == "campaign-saved-result-v1"
        ):
            from .campaign_recovery import SavedCampaignReviewProvider

            return await SavedCampaignReviewProvider(
                self.session_factory, self.storage
            ).execute(request)
        from ..xinghe_agent import PlannerCallResult, PlannerRequest, SourceImage

        root = detail_workspace(request.job_id)
        store: ReviewStore | None = None
        intent: dict[str, Any] = {}
        result: PlannerCallResult | None = None
        route: dict[str, Any] | None = None
        reserve = 0
        report_available = False
        journal_sha256: str | None = None
        model_call_pending = False
        started = any(
            (detail_workspace(request.job_id) / "reviews").glob("*.request.json")
        )
        try:
            with self.session_factory() as db:
                job = db.get(JobRecord, request.job_id)
                if (
                    job is None
                    or job.workspace_id != request.workspace_id
                    or job.type != "domestic_detail_review"
                    or job.status != "processing"
                    or job.cancel_requested_at is not None
                ):
                    raise ValueError(
                        "The original review execution is no longer active"
                    )
                intent = dict(job.configuration)
                truth = {k: v for k, v in intent.items() if k != "intent_sha256"}
                if (
                    intent.get("version") != CAMPAIGN_JOB_VERSION
                    or intent.get("intent_sha256") != canonical_hash(truth)
                    or canonical_hash(request.configuration.get("job"))
                    != canonical_hash(intent)
                    or intent.get("runtime_source_sha256") != runtime_source_hash()
                    or intent.get("authorized_model_requests") != 1
                    or intent.get("authorized_image_requests") != 0
                ):
                    raise ValueError("Original optional-review authorization changed")
                reserve = int(intent["authorized_reserve_micros"])
                route = self.describe_route()
                if route["sha256"] != intent["dispatch_route_sha256"]:
                    raise ValueError("The explicitly quoted review route changed")
                if route["kind"] == "http":
                    require_provider_job_quota(db, job)
                source = db.get(JobRecord, intent["source_job_id"])
                if source is None or source.workspace_id != request.workspace_id:
                    raise ValueError("The original source task is unavailable")
                plan, context, state, source_root, _ = await source_scope(
                    db,
                    source,
                    self.storage,
                    intent["final_review_sha256"],
                )
                if (
                    plan.id != intent["source_plan_id"]
                    or plan.revision != intent["source_plan_revision"]
                    or canonical_hash(context) != intent["source_context_sha256"]
                ):
                    raise ValueError(
                        "The accepted source campaign changed before dispatch"
                    )
                authority = approved_review_authority(state)
                if canonical_hash(authority) != intent["approved_authority_sha256"]:
                    raise ValueError("Original approved review authority changed")
                references = []
                source_rows = plan.payload["source_asset_manifest"][0]["assets"]
                for row in source_rows:
                    if row["role"] in {"style_reference", "style"}:
                        continue
                    asset = db.get(AssetRecord, row["asset_id"])
                    if (
                        asset is None
                        or asset.workspace_id != request.workspace_id
                        or asset.deleted_at is not None
                        or asset.lifecycle_status != "ready"
                    ):
                        raise ValueError("Original product reference is unavailable")
                    payload, stored = await self.storage.read_stored_image(
                        asset.object_key, asset.object_version
                    )
                    if (
                        _sha(payload) != row["sha256"]
                        or asset.sha256 != row["sha256"]
                        or _metadata(stored)
                        != {name: getattr(asset, name) for name in _metadata(stored)}
                    ):
                        raise ValueError(
                            "Original product-reference bytes or metadata changed"
                        )
                    references.append(
                        SourceImage(
                            asset_id=asset.id,
                            group_key="campaign-review",
                            filename=asset.original_filename,
                            content_type=asset.content_type,
                            sha256=asset.sha256,
                            payload=payload,
                            source_role="product_reference",
                        )
                    )
                    if len(references) == 2:
                        break
            root = detail_workspace(request.job_id)
            root.mkdir(parents=True, exist_ok=True)
            source_snapshot = root / "source-session.json"
            if not source_snapshot.exists():
                _bytes_once(
                    source_snapshot, (source_root / "session.json").read_bytes()
                )
            original = _json(source_snapshot)
            if (
                canonical_hash(
                    {
                        k: v
                        for k, v in original.items()
                        if k not in {"state", "events", "updated_at", "final"}
                    }
                )
                != context["native_session_truth_sha256"]
            ):
                raise ValueError(
                    "The original accepted snapshot differs from this review intent"
                )
            for name in (
                "input.json",
                "identity.json",
                "plan.json",
                "generation-binding.json",
                "native-contract-binding.json",
            ):
                _bytes_once(root / name, (source_root / name).read_bytes())
            runner = AuroraDetailHarnessRunner()
            runner.run(
                "fork-campaign-review",
                root,
                {
                    "session": root / "session.json",
                    "source-session": source_snapshot,
                    "source-session-sha256": _sha(source_snapshot.read_bytes()),
                },
            )
            target = _json(root / "session.json")
            if not target.get("assembly"):
                runner.run(
                    "assemble",
                    root,
                    {
                        "session": root / "session.json",
                        "output": root / "long-page.png",
                    },
                )
                target = _json(root / "session.json")
            assembly = target["assembly"]
            if (
                Path(assembly["long_page"]).resolve()
                != (root / "long-page.png").resolve()
            ):
                raise ValueError("Optional preview escaped its owned review workspace")
            preview = (root / "long-page.png").read_bytes()
            model_sources = list(references)
            for screen in context["screens"]:
                payload, stored = await self.storage.read_stored_image(
                    screen["object_key"], screen["object_version"]
                )
                if _sha(payload) != screen["sha256"]:
                    raise ValueError(
                        "Accepted source screen changed before model review"
                    )
                model_sources.append(
                    SourceImage(
                        asset_id=screen["asset_id"],
                        group_key="campaign-review",
                        filename=screen["module_id"] + ".png",
                        content_type=stored.content_type,
                        sha256=screen["sha256"],
                        payload=payload,
                        source_role="accepted_detail_screen",
                    )
                )
            model_sources.append(
                SourceImage(
                    asset_id="campaign-preview-" + request.job_id,
                    group_key="campaign-review",
                    filename="long-page.png",
                    content_type="image/png",
                    sha256=_sha(preview),
                    payload=preview,
                    source_role="assembled_campaign_preview",
                )
            )
            validate_campaign_preview(
                preview,
                [
                    frame.payload
                    for frame in model_sources
                    if frame.source_role == "accepted_detail_screen"
                ],
            )
            frame_inventory = []
            suffixes = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
            }
            for index, frame in enumerate(model_sources, 1):
                if _sha(frame.payload) != frame.sha256:
                    raise ValueError("Model-seen frame digest differs from its bytes")
                relative = f"model-inputs/{index:02d}{suffixes[frame.content_type]}"
                _bytes_once(root / relative, frame.payload)
                frame_inventory.append(
                    {
                        "asset_id": frame.asset_id,
                        "role": frame.source_role,
                        "sha256": frame.sha256,
                        "bytes": len(frame.payload),
                        "content_type": frame.content_type,
                        "file": relative,
                    }
                )
            contract = runner.run(
                "inspect-campaign-review-contract",
                root,
                {"session": root / "session.json"},
            )
            if [
                (row["module_id"], row["image_sha256"]) for row in contract["modules"]
            ] != [(row["module_id"], row["sha256"]) for row in context["screens"]]:
                raise ValueError("Native accepted screen bytes changed before review")
            contract["modules"] = [
                {k: v for k, v in row.items() if k != "image"}
                for row in contract["modules"]
            ]
            saved_context_path = root / "campaign-request-context.json"
            saved = _json(saved_context_path) if saved_context_path.exists() else None
            audited_at = (
                saved["artifact_binding"]["audited_at"]
                if saved
                else datetime.now(UTC).isoformat()
            )
            model_context = {
                "profile": CAMPAIGN_PROFILE,
                "source_job_id": intent["source_job_id"],
                "source_context_sha256": intent["source_context_sha256"],
                "native_contract": contract,
                "approved_authority": authority,
                "approved_plan": state["plan"],
                "source_inventory": frame_inventory,
                "artifact_binding": {
                    "image_sha256": _sha(preview),
                    "auditor": route["model"],
                    "audited_at": audited_at,
                },
            }
            if saved is None:
                _once(saved_context_path, model_context)
            elif canonical_hash(saved) != canonical_hash(model_context):
                raise ValueError("Original model input or assembled preview changed")
            call = PlannerRequest(
                workspace_id=request.workspace_id,
                job_id=request.job_id,
                group_key="campaign-review",
                sources=tuple(model_sources),
                page_count=len(context["screens"]),
                language="zh-CN",
                product_name=authority["product_name"],
                product_information=(json.dumps(model_context, ensure_ascii=False),),
                platform_code="domestic-detail",
                size=f"{assembly['width_px']}x{assembly['height_px']}",
                idempotency_key="campaign-review-" + request.job_id,
                domestic_protocol=True,
                planning_profile=CAMPAIGN_PROFILE,
                native_dispatch=route,
            )
            store = ReviewStore(
                root / "reviews",
                {
                    "phase": CAMPAIGN_PROFILE,
                    "render_token": request.job_id,
                    "native_request_sha256": intent["intent_sha256"],
                    "candidate_sha256": _sha(preview),
                    "request_context_sha256": canonical_hash(model_context),
                    "dispatch_route": route,
                },
            )
            started = started or store.request_path.exists()
            completed: dict[str, Any] | None
            if (root / JOURNAL).exists():
                load_result(store)
                journal_sha256 = _sha((root / JOURNAL).read_bytes())
                completed = recover_response(store)
            else:
                completed = store.completed()
            if completed is None:
                store.begin()
                started = True
                model_call_pending = True
                result = await self.gateway.plan(call)
                model_call_pending = False
                result = replace(result, phase=CAMPAIGN_PROFILE)
                try:
                    checked = result_with_dispatch(asdict(result), route)
                except Exception:
                    store.finish(asdict(result))
                    raise
                result = replace(result, provider_envelope=checked["provider_envelope"])
                journal_sha256 = save_result(store, asdict(result))
                store.finish(asdict(result))
            else:
                started = True
                validate_dispatch_result(completed, route)
                result = PlannerCallResult(**completed)
            if result.error_code:
                raise CampaignObservationError(
                    result.error_message or result.error_code
                )
            _, gate_error = materialize_campaign_review(
                root,
                request.job_id,
                intent,
                model_context,
                result,
                store,
                journal_sha256,
                publish=_once,
                publish_bytes=_bytes_once,
            )
            return self._result(
                result,
                route,
                reserve,
                error=gate_error,
                report_available=True,
                journal_sha256=journal_sha256,
            )
        except Exception as exc:
            if store is not None and store.request_path.exists():
                try:
                    failure = {
                        "version": CAMPAIGN_JOB_VERSION,
                        "review_job_id": request.job_id,
                        "source_job_id": intent.get("source_job_id"),
                        "intent_sha256": intent.get("intent_sha256"),
                        "source_context_sha256": intent.get("source_context_sha256"),
                        "native_gate_accepted": False,
                        "stage": "REVIEW_NOT_ACCEPTED",
                        "request_context_sha256": store.binding[
                            "request_context_sha256"
                        ],
                        "preview_sha256": store.binding["candidate_sha256"],
                        "error_code": str(
                            getattr(
                                exc, "code", "NATIVE_DETAIL_CAMPAIGN_REVIEW_INVALID"
                            )
                        ),
                        "error_message": str(exc),
                        "request_file_sha256": _sha(store.request_path.read_bytes()),
                        "response_file_sha256": _sha(store.response_path.read_bytes())
                        if store.response_path.exists()
                        else None,
                        "original_message_sha256": _sha(
                            result.audit_raw_content.encode("utf-8")
                        )
                        if result is not None
                        and isinstance(result.audit_raw_content, str)
                        else None,
                        "judgment": result.payload if result is not None else None,
                    }
                    if isinstance(exc, OSError) and not model_call_pending:
                        interrupted = {**failure, "stage": "LOCAL_WRITE_INTERRUPTION"}
                        _once(
                            root
                            / "interruptions"
                            / (canonical_hash(interrupted) + ".json"),
                            {**interrupted, "sha256": canonical_hash(interrupted)},
                        )
                    else:
                        path = root / "campaign-report.json"
                        if not path.exists():
                            _once(path, {**failure, "sha256": canonical_hash(failure)})
                            report_available = True
                except (OSError, ValueError, TypeError):
                    pass
            return self._result(
                result,
                route,
                reserve,
                error=str(exc),
                code=str(getattr(exc, "code", "NATIVE_DETAIL_CAMPAIGN_REVIEW_INVALID")),
                started=started,
                report_available=report_available,
                journal_sha256=journal_sha256,
            )

    def _result(
        self,
        result: Any,
        route: dict[str, Any] | None,
        reserve: int,
        *,
        error: str | None = None,
        code: str = "NATIVE_DETAIL_CAMPAIGN_GATE_REJECTED",
        started: bool = True,
        report_available: bool = False,
        journal_sha256: str | None = None,
    ) -> ProviderResult:
        dispatch = (
            (result.provider_envelope or {}).get("native_planner_dispatch")
            if result is not None
            else None
        )
        requests = (
            dispatch.get("transport_attempts")
            if isinstance(dispatch, dict)
            else (1 if started else 0)
        )
        if route is not None and route["kind"] == "synthetic":
            requests = 0
        normalized = dict(result.normalized_usage) if result is not None else {}
        reported = (
            normalized.get("reported") is True if result is not None else not started
        )
        actual = (
            result.actual_cost_micros
            if result is not None
            else (None if started else 0)
        )
        raw = {
            **normalized,
            "network_requests": requests,
            "output_images": 0,
            "campaign_review": {
                "report_available": report_available,
                "failure": error,
                "original_result_journal_sha256": journal_sha256,
            },
        }
        uncertain = bool(
            error
            and started
            and (
                result is None
                or (
                    isinstance(dispatch, dict)
                    and dispatch.get("outcome")
                    in {"outcome_unknown", "response_invalid"}
                )
            )
        )
        if actual is not None and (type(actual) is not int or actual < 0):
            actual = None
        return ProviderResult(
            outcome=ProviderOutcome.OUTCOME_UNKNOWN
            if uncertain
            else (ProviderOutcome.FAILED if error else ProviderOutcome.SUCCEEDED),
            provider_request_id=result.provider_request_id
            if result is not None
            else None,
            error_code=code if error else None,
            error_message=error,
            retryable=False,
            usage_reported=reported,
            raw_usage=raw,
            estimated_cost_micros=reserve if started else 0,
            actual_cost_micros=actual,
            provider_name=self.gateway.provider_name,
            model_name=route["model"] if route else self.model,
            pricing_version=self.pricing_version,
        )
