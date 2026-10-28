"""Zero-model queued recovery from the original paid campaign result journal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
from ..models import JobItemRecord, JobRecord, UsageEventRecord, utc_now
from ..provider import ProviderOutcome, ProviderRequest, ProviderResult
from ..services import enforce_active_job_limit, new_id
from ..services import reserve_image_credits as reserve_credits
from ..storage import ImageStorage
from .campaign_durability import (
    JOURNAL,
    bytes_once,
    json_once,
    load_result,
    recover_response,
    sha,
)
from .campaign_jobs import (
    CAMPAIGN_JOB_VERSION,
    CAMPAIGN_PROFILE,
    CAMPAIGN_RECOVERY_VERSION,
    source_scope,
)
from .campaign_materialization import (
    materialize_campaign_review,
    validate_campaign_preview,
)
from .delivery import _json, _reject, _stable_session
from .generation import runtime_source_hash
from .plans import canonical_hash
from .review import ReviewStore


@dataclass(frozen=True)
class SavedReviewEvidence:
    root: Path
    context: dict[str, Any]
    store: ReviewStore
    journal: dict[str, Any]
    journal_sha256: str


def owned(root: Path, relative: str) -> bytes:
    path = root / relative
    if (
        path.is_symlink()
        or path.parent.is_symlink()
        or path.parent.is_junction()
        or not path.resolve().is_relative_to(root.resolve())
    ):
        _reject("Original saved-result evidence escaped its job workspace")
    return path.read_bytes()


def saved_evidence(parent: JobRecord) -> SavedReviewEvidence:
    intent = parent.configuration
    if (
        parent.type != "domestic_detail_review"
        or intent.get("version") != CAMPAIGN_JOB_VERSION
        or intent.get("authorized_model_requests") != 1
        or intent.get("authorized_image_requests") != 0
        or canonical_hash({k: v for k, v in intent.items() if k != "intent_sha256"})
        != intent.get("intent_sha256")
    ):
        _reject("Recovery requires one original authorized paid review")
    root = detail_workspace(parent.id)
    context = _json(root / "campaign-request-context.json")
    original = _json(root / "source-session.json")
    requests = list((root / "reviews").glob("*.request.json"))
    if len(requests) != 1:
        _reject("A saved review must have its single original request")
    path = requests[0]
    binding = _json(path)["binding"]
    store = ReviewStore(path.parent, binding)
    if (
        store.request_path != path
        or binding.get("phase") != CAMPAIGN_PROFILE
        or binding.get("render_token") != parent.id
        or binding.get("native_request_sha256") != intent["intent_sha256"]
        or binding.get("request_context_sha256") != canonical_hash(context)
        or binding["dispatch_route"]["sha256"] != intent["dispatch_route_sha256"]
        or context.get("source_context_sha256") != intent["source_context_sha256"]
        or _stable_session(original) != intent["source_native_truth_sha256"]
        or canonical_hash(context.get("approved_authority"))
        != intent["approved_authority_sha256"]
        or canonical_hash(context.get("approved_plan"))
        != canonical_hash(original["plan"])
    ):
        _reject("Saved result source, original request or approved authority changed")
    inventory = context["source_inventory"]
    for frame in inventory:
        payload = owned(root, frame["file"])
        if len(payload) != frame["bytes"] or sha(payload) != frame["sha256"]:
            _reject("An original model-seen frame changed")
    preview = owned(root, "long-page.png")
    if (
        sha(preview) != binding["candidate_sha256"]
        or sha(preview) != context["artifact_binding"]["image_sha256"]
    ):
        _reject("The original model-seen preview changed")
    validate_campaign_preview(
        preview,
        [
            owned(root, r["file"])
            for r in inventory
            if r["role"] == "accepted_detail_screen"
        ],
    )
    journal = load_result(store)
    result = journal["result"]
    dispatch = result["provider_envelope"]["native_planner_dispatch"]
    if result.get("error_code") or dispatch["outcome"] not in {
        "succeeded",
        "synthetic_succeeded",
    }:
        _reject("An uncertain original dispatch cannot be replayed or invented")
    if (
        not isinstance(result.get("audit_raw_content"), str)
        or not result["audit_raw_content"].strip()
    ):
        _reject("A saved result requires its original model message")
    return SavedReviewEvidence(root, context, store, journal, sha(owned(root, JOURNAL)))


async def recovery_source(
    db: Session, parent: JobRecord, storage: ImageStorage
) -> SavedReviewEvidence:
    if parent.status not in {"failed", "completed", "cancelled"}:
        _reject("Wait for the original review to become terminal")
    evidence = saved_evidence(parent)
    source = db.get(JobRecord, parent.configuration["source_job_id"])
    if source is None or source.workspace_id != parent.workspace_id:
        _reject("The original detail source is unavailable")
    plan, context, _, _, _ = await source_scope(
        db, source, storage, parent.configuration["final_review_sha256"]
    )
    if (
        plan.id != parent.configuration["source_plan_id"]
        or plan.revision != parent.configuration["source_plan_revision"]
        or canonical_hash(context) != parent.configuration["source_context_sha256"]
    ):
        _reject("The accepted detail source changed after the original model call")
    frames = [
        (r["asset_id"], r["sha256"])
        for r in evidence.context["source_inventory"]
        if r["role"] == "accepted_detail_screen"
    ]
    if frames != [(r["asset_id"], r["sha256"]) for r in context["screens"]]:
        _reject(
            "The original model did not observe the currently accepted screen order"
        )
    for usage in db.scalars(
        select(UsageEventRecord).where(UsageEventRecord.job_id == parent.id)
    ).all():
        proof = (usage.raw_usage.get("campaign_review") or {}).get(
            "original_result_journal_sha256"
        )
        if proof is not None and proof != evidence.journal_sha256:
            _reject("The saved original result differs from its accounting receipt")
    return evidence


def recovery_quote(parent: JobRecord, evidence: SavedReviewEvidence) -> dict[str, Any]:
    value = {
        "version": CAMPAIGN_RECOVERY_VERSION,
        "original_review_job_id": parent.id,
        "source_job_id": parent.configuration["source_job_id"],
        "original_result_journal_sha256": evidence.journal_sha256,
        "original_request_context_sha256": canonical_hash(evidence.context),
        "model_requests": 0,
        "image_requests": 0,
        "reserve_micros": 0,
        "currency": "USD",
    }
    return {**value, "sha256": canonical_hash(value)}


async def create_recovery_job(
    db: Session,
    parent: JobRecord,
    storage: ImageStorage,
    *,
    actor_id: str,
    journal_sha256: str,
    quote_sha256: str,
) -> JobRecord:
    evidence = await recovery_source(db, parent, storage)
    quote = recovery_quote(parent, evidence)
    if evidence.journal_sha256 != journal_sha256 or quote["sha256"] != quote_sha256:
        _reject("Review the original saved-result fingerprint before recovery")
    for candidate in db.scalars(
        select(JobRecord).where(
            JobRecord.workspace_id == parent.workspace_id,
            JobRecord.type == "domestic_detail_review",
            JobRecord.configuration["original_review_job_id"].as_string() == parent.id,
        )
    ).all():
        if candidate.status in {"queued", "processing", "cancelling"}:
            _reject("An original-result recovery is already active")
        if candidate.status in {"completed", "failed"}:
            from .campaign_reports import load_campaign_report

            try:
                report, _ = load_campaign_report(candidate)
                if (
                    report["report"]["original_review"]["journal_file_sha256"]
                    == evidence.journal_sha256
                ):
                    return candidate
            except (ValueError, OSError, KeyError):
                pass
    enforce_active_job_limit(db, parent.workspace_id)
    intent = {
        **{
            k: parent.configuration[k]
            for k in (
                "source_job_id",
                "source_plan_id",
                "source_plan_revision",
                "source_context_sha256",
                "source_native_truth_sha256",
                "approved_authority_sha256",
                "generation_binding_sha256",
                "final_review_sha256",
                "dispatch_route_sha256",
                "planner_model",
                "source_native_version",
            )
        },
        "version": CAMPAIGN_RECOVERY_VERSION,
        "original_review_job_id": parent.id,
        "original_review_intent_sha256": parent.configuration["intent_sha256"],
        "original_result_journal_sha256": journal_sha256,
        "original_request_context_sha256": canonical_hash(evidence.context),
        "quote_sha256": quote_sha256,
        "authorized_model_requests": 0,
        "authorized_image_requests": 0,
        "authorized_reserve_micros": 0,
        "runtime_source_sha256": runtime_source_hash(),
        "actor_id": actor_id,
    }
    job = JobRecord(
        id=new_id("job"),
        workspace_id=parent.workspace_id,
        type="domestic_detail_review",
        status="queued",
        stage="queued",
        display_name=("恢复原审核结果 · " + (parent.display_name or parent.id))[:160],
        configuration={**intent, "intent_sha256": canonical_hash(intent)},
        configuration_schema=CAMPAIGN_RECOVERY_VERSION,
        prompt_version=CAMPAIGN_RECOVERY_VERSION,
        strategy_version=CAMPAIGN_RECOVERY_VERSION,
        release_version=parent.release_version,
        root_job_id=parent.root_job_id or parent.id,
        expected_item_count=1,
        estimated_cost_min_micros=0,
        estimated_cost_max_micros=0,
        estimated_credits=0,
        currency="USD",
        queued_at=utc_now(),
    )
    db.add(job)
    db.flush()
    db.add(
        JobItemRecord(
            id=new_id("item"),
            workspace_id=job.workspace_id,
            job_id=job.id,
            item_key="saved_campaign_result",
            ordinal=0,
            status="pending",
            stage="queued",
            configuration={"intent_sha256": job.configuration["intent_sha256"]},
        )
    )
    # There is no new supplier reservation: this executor has no model gateway.
    reserve_credits(db, job.workspace_id, job, 0)
    return job


class SavedCampaignReviewProvider:
    name = "local"
    model = "saved-campaign-observations"
    pricing_version = CAMPAIGN_RECOVERY_VERSION

    def __init__(
        self, session_factory: sessionmaker[Session], storage: ImageStorage
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage

    async def execute(self, request: ProviderRequest) -> ProviderResult:
        from ..xinghe_agent import PlannerCallResult

        root = detail_workspace(request.job_id)
        result: Any = None
        intent: dict[str, Any] = {}
        evidence: SavedReviewEvidence | None = None
        store: ReviewStore | None = None
        try:
            with self.session_factory() as db:
                job = db.get(JobRecord, request.job_id)
                if (
                    job is None
                    or job.type != "domestic_detail_review"
                    or job.workspace_id != request.workspace_id
                    or job.status != "processing"
                    or job.cancel_requested_at is not None
                ):
                    raise ValueError("Saved-result recovery is not active")
                intent = dict(job.configuration)
                if (
                    intent.get("version") != CAMPAIGN_RECOVERY_VERSION
                    or intent.get("authorized_model_requests") != 0
                    or intent.get("authorized_image_requests") != 0
                    or intent.get("runtime_source_sha256") != runtime_source_hash()
                    or intent.get("intent_sha256")
                    != canonical_hash(
                        {k: v for k, v in intent.items() if k != "intent_sha256"}
                    )
                    or canonical_hash(request.configuration.get("job"))
                    != canonical_hash(intent)
                ):
                    raise ValueError(
                        "Original zero-model recovery authorization changed"
                    )
                parent = db.get(JobRecord, intent["original_review_job_id"])
                if parent is None or parent.workspace_id != request.workspace_id:
                    raise ValueError("Original paid review is unavailable")
                evidence = await recovery_source(db, parent, self.storage)
                if (
                    evidence.journal_sha256 != intent["original_result_journal_sha256"]
                    or parent.configuration["intent_sha256"]
                    != intent["original_review_intent_sha256"]
                    or canonical_hash(evidence.context)
                    != intent["original_request_context_sha256"]
                ):
                    raise ValueError(
                        "The original saved result changed after recovery was requested"
                    )
            names = [
                "source-session.json",
                "input.json",
                "identity.json",
                "plan.json",
                "generation-binding.json",
                "native-contract-binding.json",
                "campaign-request-context.json",
                JOURNAL,
            ]
            names.extend(row["file"] for row in evidence.context["source_inventory"])
            names.append("reviews/" + evidence.store.request_path.name)
            for path in (
                evidence.store.response_path,
                evidence.store.stem.with_suffix(".reply.txt"),
            ):
                if path.exists():
                    names.append(path.relative_to(evidence.root).as_posix())
            for name in names:
                bytes_once(root / name, owned(evidence.root, name))
            runner = AuroraDetailHarnessRunner()
            runner.run(
                "fork-campaign-review",
                root,
                {
                    "session": root / "session.json",
                    "source-session": root / "source-session.json",
                    "source-session-sha256": sha(
                        (root / "source-session.json").read_bytes()
                    ),
                },
            )
            state = _json(root / "session.json")
            if not state.get("assembly"):
                runner.run(
                    "assemble",
                    root,
                    {
                        "session": root / "session.json",
                        "output": root / "long-page.png",
                    },
                )
            if (root / "long-page.png").read_bytes() != owned(
                evidence.root, "long-page.png"
            ):
                raise ValueError(
                    "Native reconstruction differs from the exact model-seen preview"
                )
            store = ReviewStore(root / "reviews", evidence.store.binding)
            result = PlannerCallResult(**recover_response(store))
            _, error = materialize_campaign_review(
                root,
                request.job_id,
                intent,
                evidence.context,
                result,
                store,
                evidence.journal_sha256,
            )
            return self._result(error)
        except Exception as exc:
            if result is not None and store is not None and evidence is not None:
                try:
                    failure = {
                        "version": CAMPAIGN_JOB_VERSION,
                        "review_job_id": request.job_id,
                        "source_job_id": intent["source_job_id"],
                        "intent_sha256": intent["intent_sha256"],
                        "source_context_sha256": intent["source_context_sha256"],
                        "request_context_sha256": canonical_hash(evidence.context),
                        "request_file_sha256": sha(store.request_path.read_bytes()),
                        "response_file_sha256": sha(store.response_path.read_bytes()),
                        "preview_sha256": evidence.store.binding["candidate_sha256"],
                        "model_result_journal_sha256": evidence.journal_sha256,
                        "native_gate_accepted": False,
                        "stage": "REVIEW_NOT_ACCEPTED",
                        "error_message": str(exc),
                        "judgment": result.payload,
                        "original_review": {
                            "job_id": intent["original_review_job_id"],
                            "intent_sha256": intent["original_review_intent_sha256"],
                            "journal_file_sha256": evidence.journal_sha256,
                        },
                    }
                    if isinstance(exc, OSError):
                        failure["stage"] = "LOCAL_WRITE_INTERRUPTION"
                        destination = (
                            root / "interruptions" / (canonical_hash(failure) + ".json")
                        )
                    else:
                        destination = root / "campaign-report.json"
                    if not destination.exists():
                        json_once(
                            destination, {**failure, "sha256": canonical_hash(failure)}
                        )
                except (ValueError, OSError):
                    pass
            return self._result(str(exc))

    def _result(self, error: str | None) -> ProviderResult:
        return ProviderResult(
            outcome=ProviderOutcome.FAILED if error else ProviderOutcome.SUCCEEDED,
            error_code="NATIVE_DETAIL_SAVED_RESULT_RECOVERY_FAILED" if error else None,
            error_message=error,
            retryable=False,
            usage_reported=True,
            actual_cost_micros=0,
            estimated_cost_micros=0,
            provider_name=self.name,
            model_name=self.model,
            pricing_version=self.pricing_version,
            raw_usage={
                "reported": True,
                "input_tokens": 0,
                "output_tokens": 0,
                "network_requests": 0,
                "output_images": 0,
                "saved_result_recovery": True,
            },
        )
