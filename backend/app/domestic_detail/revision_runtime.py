"""Validate a new revision's public lease and prepare its owned native fork."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
from ..config import Settings
from ..models import (
    AuditEventRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    UserRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
    utc_now,
)
from ..provider import ProviderRequest
from ..provider_quota import require_provider_job_quota
from ..storage import ImageStorage
from .campaign_durability import bytes_once, encoded, json_once
from .generation import bind_native_contract, bind_workspace, generation_binding
from .plans import canonical_hash
from .revision_authorization import (
    authorize_revision_quote,
    build_revision_quote,
    image_dispatch,
)
from .revision_jobs import REVISION_VERSION
from .revision_protocol import RevisionScopeError, scope_context
from .revision_scope import run_scope_check


def _reject(message: str) -> NoReturn:
    raise RevisionScopeError("NATIVE_DETAIL_REVISION_EXECUTION_CHANGED", message)


def checked_request(
    db: Session, request: ProviderRequest
) -> tuple[JobRecord, JobPlanRecord, dict[str, Any]]:
    job = db.get(JobRecord, request.job_id)
    item = db.get(JobItemRecord, request.job_item_id)
    attempt = db.get(JobAttemptRecord, request.attempt_id)
    if job is None or item is None or attempt is None:
        _reject("Original public revision execution is unavailable")
    assert job is not None and item is not None and attempt is not None
    if (
        job.workspace_id != request.workspace_id
        or job.type != "detail_pack"
        or job.status != "processing"
        or job.cancel_requested_at is not None
        or item.job_id != job.id
        or item.workspace_id != job.workspace_id
        or item.status != "running"
        or attempt.job_id != job.id
        or attempt.job_item_id != item.id
        or attempt.workspace_id != job.workspace_id
        or attempt.status != "running"
        or attempt.provider_idempotency_key != request.idempotency_key
        or attempt.operation != request.operation
        or request.operation != "detail_pack"
        or attempt.lease_expires_at.replace(
            tzinfo=attempt.lease_expires_at.tzinfo or UTC
        )
        <= utc_now()
        or canonical_hash(request.configuration.get("job"))
        != canonical_hash(job.configuration)
        or canonical_hash(request.configuration.get("item"))
        != canonical_hash(item.configuration)
    ):
        _reject("The original lease, cancellation state or public item changed")
    intent = job.configuration.get("local_revision")
    if (
        not isinstance(intent, dict)
        or intent.get("version") != REVISION_VERSION
        or intent.get("job_id") != job.id
        or intent.get("workspace_id") != job.workspace_id
        or intent.get("root_job_id") != job.root_job_id
        or intent.get("sha256")
        != canonical_hash({k: v for k, v in intent.items() if k != "sha256"})
        or intent.get("delivery_mode") != "USER_FINAL_REVIEW"
        or intent.get("maximum_new_image_requests") != 2
        or intent.get("maximum_new_model_requests") != 5
    ):
        _reject("The new revision owner or exact original human intent changed")
    assert isinstance(intent, dict)
    authority = intent["authority"]
    quote = authority["quote"]
    checked = authorize_revision_quote(
        quote,
        submitted_sha256=quote["sha256"],
        approval_text=intent["approval_text"],
        source_job_id=intent["source_job_id"],
        workspace_id=job.workspace_id,
        actor_id=intent["actor_id"],
    )
    if (
        canonical_hash(authority) != canonical_hash(checked)
        or intent["authority_sha256"] != checked["sha256"]
        or canonical_hash(intent["adjustment"]) != canonical_hash(quote["adjustment"])
    ):
        _reject("The exact local revision authority changed")
    if (
        intent["source_session_sha256"] != quote["source_native_session_sha256"]
        or intent["source_generation_binding_sha256"]
        != quote["source_generation_binding_sha256"]
    ):
        _reject("The source snapshot differs from the exact human quote")
    if (
        item.ordinal != quote["source_screens"][0]["ordinal"]
        or item.configuration.get("pair_position") != 1
        or item.configuration.get("pair_id") != quote["pair_id"]
        or item.configuration.get("local_revision_intent_sha256") != intent["sha256"]
    ):
        _reject("Scope preflight must own the upper public item of the quoted pair")
    user = db.get(UserRecord, intent["actor_id"])
    member = db.get(WorkspaceMemberRecord, (job.workspace_id, intent["actor_id"]))
    workspace = db.get(WorkspaceRecord, job.workspace_id)
    if (
        user is None
        or user.status != "active"
        or member is None
        or member.status != "active"
        or member.role not in {"owner", "admin", "member"}
        or workspace is None
        or workspace.status != "active"
        or workspace.deleted_at is not None
    ):
        _reject("The original human actor no longer has workspace write authority")
    plan = db.scalar(
        select(JobPlanRecord)
        .where(
            JobPlanRecord.job_id == job.id,
            JobPlanRecord.workspace_id == job.workspace_id,
        )
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    if (
        plan is None
        or plan.confirmed_at is None
        or plan.validation_status != "valid"
        or canonical_hash(plan.payload) != plan.payload_hash
        or canonical_hash(plan.payload.get("local_revision")) != canonical_hash(intent)
    ):
        _reject("The original confirmed child revision plan changed")
    assert plan is not None
    if (
        job.expected_item_count != quote["approved_screen_count"]
        or job.estimated_cost_max_micros != quote["maximum_cost_micros"]
    ):
        _reject("The new child count or quoted total reservation changed")
    if quote["dispatch_kind"] == "http":
        require_provider_job_quota(db, job)
        quota = db.scalars(
            select(AuditEventRecord).where(
                AuditEventRecord.workspace_id == job.workspace_id,
                AuditEventRecord.resource_id == job.id,
                AuditEventRecord.action == "provider.beta_job_reserved",
            )
        ).all()
        if (
            len(quota) != 1
            or quota[0].actor_user_id != intent["actor_id"]
            or any(
                quota[0].details.get(k) != value
                for k, value in {
                    "provider_request_count": 7,
                    "estimated_cost_micros": quote["maximum_cost_micros"],
                    "planner_reserve_micros": quote["review_reserve_micros"],
                    "image_budget_micros": 2 * quote["image_unit_micros"],
                }.items()
            )
        ):
            _reject("The exact new revision quota must exist before a real scope call")
    return job, plan, intent


def account_scope_result(
    factory: sessionmaker[Session],
    request: ProviderRequest,
    intent_sha256: str,
    result: Any,
    binding_sha256: str,
) -> None:
    from ..xinghe_agent import _planner_diagnostics_record, _planner_usage_record

    with factory() as db:
        job = db.get(JobRecord, request.job_id)
        plan = db.scalar(
            select(JobPlanRecord)
            .where(
                JobPlanRecord.job_id == request.job_id,
                JobPlanRecord.workspace_id == request.workspace_id,
            )
            .order_by(JobPlanRecord.revision.desc())
            .limit(1)
            .with_for_update()
        )
        if (
            job is None
            or plan is None
            or job.workspace_id != request.workspace_id
            or job.configuration.get("local_revision", {}).get("sha256")
            != intent_sha256
        ):
            _reject("Preserve the original scope journal; its billing owner changed")
        from .revision_scope import scope_billing_observation

        billing = scope_billing_observation(
            detail_workspace(request.job_id), binding_sha256, result
        )
        payload = deepcopy(plan.payload)
        usage = deepcopy(payload.get("usage") or {})
        existing = list(usage.get("planner") or [])
        prior_billing = payload.get("local_scope_billing_receipt")
        if prior_billing is not None and canonical_hash(
            prior_billing
        ) != canonical_hash(billing):
            _reject("The original scope billing outcome changed")
        if billing["state"] == "not_sent":
            if any(
                r.get("local_scope_binding_sha256") == binding_sha256 for r in existing
            ):
                _reject("A known not-sent request conflicts with an existing charge")
            if prior_billing is not None:
                return
            payload["local_scope_billing_receipt"] = billing
            payload["planner_diagnostics"] = [
                *(payload.get("planner_diagnostics") or []),
                {
                    **_planner_diagnostics_record(result),
                    "source": "native_scope_not_sent",
                    "local_scope_binding_sha256": binding_sha256,
                    "provider_request_count": 0,
                    "charged_cost_micros": 0,
                },
            ]
            plan.payload = payload
            plan.payload_hash = canonical_hash(payload)
            db.commit()
            return
        payload["local_scope_billing_receipt"] = billing
        record = {
            **_planner_usage_record(result),
            "local_scope_binding_sha256": binding_sha256,
        }
        matched = [
            r for r in existing if r.get("local_scope_binding_sha256") == binding_sha256
        ]
        if matched:
            if len(matched) != 1 or canonical_hash(matched[0]) != canonical_hash(
                record
            ):
                _reject(
                    "Original scope billing metadata conflicts with its paid result"
                )
            return
        if any(r.get("phase") == "aurora_detail_revision_scope" for r in existing):
            _reject("An original scope call is already accounted under another binding")
        usage["planner"] = [*existing, record]
        payload["usage"] = usage
        payload["planner_diagnostics"] = [
            *(payload.get("planner_diagnostics") or []),
            {
                **_planner_diagnostics_record(result),
                "local_scope_binding_sha256": binding_sha256,
            },
        ]
        plan.payload = payload
        plan.payload_hash = canonical_hash(payload)
        db.commit()


async def prepare_revision(
    *,
    factory: sessionmaker[Session],
    request: ProviderRequest,
    settings: Settings | None,
    storage: ImageStorage | None,
    gateway: Any,
    runner: AuroraDetailHarnessRunner,
    binding: Mapping[str, Any],
) -> bool:
    """No ordinary restore/start or paid image call may precede this preparation."""
    from .campaign_jobs import source_scope

    with factory() as db:
        original = db.get(JobRecord, request.job_id)
        if original is None or original.configuration.get("local_revision") is None:
            return False
        if settings is None or storage is None:
            _reject(
                "The explicit current revision runtime settings "
                "and storage are required"
            )
        job, plan, intent = checked_request(db, request)
        source = db.get(JobRecord, intent["source_job_id"])
        quote = intent["authority"]["quote"]
        if (
            source is None
            or source.workspace_id != job.workspace_id
            or (source.root_job_id or source.id) != intent["root_job_id"]
        ):
            _reject("The original source/root task changed")
        assert source is not None
        fresh = await build_revision_quote(
            db,
            source,
            storage,
            settings,
            gateway,
            fingerprint=quote["final_review_sha256"],
            adjustment=intent["adjustment"],
            actor_id=intent["actor_id"],
        )
        if canonical_hash(fresh) != canonical_hash(quote):
            _reject("The source, current routes, unit prices or frozen quote changed")
        source_plan, source_context, state, source_root, _ = await source_scope(
            db, source, storage, quote["final_review_sha256"]
        )
        expected_receipt = deepcopy(
            source_plan.payload["aurora_v4_3"]["approval_receipt"]
        )
        expected_receipt.pop("automatic_delivery_authorization", None)
        expected_receipt.update(
            approval_text=intent["approval_text"],
            actor_user_id=intent["actor_id"],
            plan_revision=1,
            local_revision_authority_sha256=intent["authority_sha256"],
        )
        if (
            canonical_hash(plan.payload["aurora_v4_3"]["approval_receipt"])
            != canonical_hash(expected_receipt)
            or plan.payload["native_approval"] != source_plan.payload["native_approval"]
            or canonical_hash(plan.payload["aurora_v4_3"]["contract_binding"])
            != canonical_hash(source_plan.payload["aurora_v4_3"]["contract_binding"])
            or canonical_hash(
                {k: v for k, v in job.configuration.items() if k != "local_revision"}
            )
            != canonical_hash(
                {k: v for k, v in source.configuration.items() if k != "local_revision"}
            )
        ):
            _reject(
                "Child original facts, settings, compiler approval or ne"
                "w manual receipt changed"
            )
        groups = plan.payload["source_asset_manifest"]
        expected_binding = generation_binding(
            plan.payload,
            job.configuration["settings"],
            groups[0]["assets"],
            group_key=groups[0]["group_key"],
            job_input=job.configuration["input"],
        )
        if canonical_hash(binding) != canonical_hash(expected_binding):
            _reject("The new child generation binding differs from its own authority")
        from .delivery import _json

        native_source_contract = _json(source_root / "native-contract-binding.json")
        source_files = {
            name: (source_root / name).read_bytes()
            for name in ("input.json", "identity.json", "plan.json")
        }
        if any(
            hashlib.sha256(data).hexdigest() != native_source_contract["files"][name]
            for name, data in source_files.items()
        ):
            _reject("Original compiled input bytes changed before semantic dispatch")
        source_session_bytes = (source_root / "session.json").read_bytes()
        if (
            hashlib.sha256(source_session_bytes).hexdigest()
            != quote["source_native_session_sha256"]
        ):
            _reject("Original session bytes changed before semantic dispatch")
        projection = runner.run(
            "inspect-pair-review-contracts",
            source_root,
            {"session": source_root / "session.json", "pair-id": quote["pair_id"]},
        )
        if (source_root / "session.json").read_bytes() != source_session_bytes:
            _reject("Original session changed while projecting effective contracts")
        context = scope_context(
            intent,
            state,
            effective_module_contracts=[
                {key: row[key] for key in ("module_id", "contract", "contract_sha256")}
                for row in projection["module_contracts"]
            ],
        )
        source_plan_id = source_plan.id
        source_context_sha256 = canonical_hash(source_context)
    root = detail_workspace(request.job_id)

    def before_dispatch() -> None:
        with factory() as db:
            current, _, current_intent = checked_request(db, request)
            current_source_plan = db.get(JobPlanRecord, source_plan_id)
            current_quote = current_intent["authority"]["quote"]
            if (
                current_source_plan is None
                or canonical_hash(current_source_plan.payload)
                != quote["source_plan_payload_sha256"]
                or current_intent["sha256"] != intent["sha256"]
                or current_quote["source_context_sha256"] != source_context_sha256
                or hashlib.sha256(
                    (source_root / "session.json").read_bytes()
                ).hexdigest()
                != quote["source_native_session_sha256"]
                or image_dispatch(gateway)["sha256"] != quote["image_dispatch_sha256"]
                or gateway.high_price_micros != quote["image_unit_micros"]
                or gateway.pricing_version != quote["pricing_version"]
                or settings.tengli_planner_reserve_micros
                != quote["review_reserve_micros"]
            ):
                _reject(
                    "Original source, quoted routes/prices or exact human in"
                    "tent changed"
                )
            from .planner_dispatch import select_route

            if select_route(gateway)["sha256"] != quote["review_dispatch_sha256"]:
                _reject("The quoted semantic review account changed")

    def before_new_dispatch() -> None:
        from .revision_accounts import configured_revision_accounts

        accounts = configured_revision_accounts(gateway)
        if (
            accounts["image_dispatch_sha256"] != quote["image_dispatch_sha256"]
            or accounts["review_dispatch_sha256"] != quote["review_dispatch_sha256"]
        ):
            _reject("The account configuration differs from the original quote")
        value = {
            "intent_sha256": intent["sha256"],
            "quote_sha256": quote["sha256"],
            "accounts": accounts,
        }
        json_once(
            root / "local-revision-account-configuration.json",
            {
                **value,
                "sha256": canonical_hash(value),
            },
        )

    await run_scope_check(
        root=root,
        workspace_id=request.workspace_id,
        job_id=request.job_id,
        context=context,
        gateway=gateway,
        before_dispatch=before_dispatch,
        before_new_dispatch=before_new_dispatch,
        account_result=lambda result, key: account_scope_result(
            factory, request, intent["sha256"], result, key
        ),
    )
    before_dispatch()
    with factory() as db:
        _, current_plan, _ = checked_request(db, request)
        model = [
            r
            for r in current_plan.payload["usage"]["planner"]
            if r.get("phase") == "aurora_detail_revision_scope"
        ]
        if len(model) != 1 or (
            model[0].get("actual_cost_micros") is not None
            and (
                type(model[0]["actual_cost_micros"]) is not int
                or model[0]["actual_cost_micros"] < 0
                or model[0]["actual_cost_micros"] > quote["review_reserve_micros"]
            )
        ):
            _reject(
                "Original scope usage exceeds its quoted single-call res"
                "erve; preserve it before more calls"
            )
    for name, data in source_files.items():
        bytes_once(root / name, data)
    bytes_once(root / "source-session.json", source_session_bytes)
    json_once(root / "local-revision-intent.json", intent)
    # Never copy the original generation binding over this child's new authority.
    bytes_once(root / "generation-binding.json", encoded(binding))
    bind_workspace(root, binding)
    runner.run(
        "fork-local-revision",
        root,
        {
            "session": root / "session.json",
            "source-session": root / "source-session.json",
            "source-session-sha256": intent["source_session_sha256"],
            "revision-intent": root / "local-revision-intent.json",
        },
    )
    bind_native_contract(root, binding, create=True)
    return True


def require_revision_review_budget(
    factory: sessionmaker[Session],
    request: ProviderRequest,
    native: Mapping[str, Any],
    gateway: Any,
    *,
    new_request: bool,
) -> None:
    """A local cycle owns one scope call and at most four original review calls."""
    from .revision_accounts import configured_revision_accounts
    from .revision_review import local_contract

    contract = local_contract(native)
    if contract is None:
        return
    with factory() as db:
        _, plan, intent = checked_request(db, request)
        quote = intent["authority"]["quote"]
        if (
            contract["intent_sha256"] != intent["sha256"]
            or contract["pair_id"] != quote["pair_id"]
            or contract["module_ids"] != quote["module_ids"]
            or contract["selected_module_id"] != quote["selected_module_id"]
            or quote["maximum_review_model_requests"] != 4
        ):
            _reject("Current review differs from the original local cycle authority")
        rows = (plan.payload.get("usage") or {}).get("planner") or []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            _reject("Current review usage records are malformed")
        scope = [r for r in rows if r.get("phase") == "aurora_detail_revision_scope"]
        reviews = [
            r
            for r in rows
            if r.get("phase")
            in {
                "aurora_detail_pair_audit:" + quote["pair_id"],
                "aurora_detail_identity_audit:" + quote["pair_id"],
            }
        ]
        keys = [
            r.get("local_scope_binding_sha256")
            if r in scope
            else r.get("review_binding_sha256")
            for r in rows
        ]
        if (
            len(scope) != 1
            or len(rows) != len(scope) + len(reviews)
            or len(reviews) > 4
            or (new_request and len(reviews) >= 4)
            or any(not isinstance(key, str) or not key for key in keys)
            or len(keys) != len(set(keys))
            or any(
                r.get("actual_cost_micros") is not None
                and (
                    type(r["actual_cost_micros"]) is not int
                    or r["actual_cost_micros"] < 0
                    or r["actual_cost_micros"] > quote["review_reserve_micros"]
                )
                for r in rows
            )
        ):
            raise RevisionScopeError(
                "NATIVE_DETAIL_REVISION_REVIEW_BUDGET_INVALID",
                "Preserve original review results; the bounded cycle usage differs "
                "or exceeds its quote",
            )
        if new_request:
            accounts = configured_revision_accounts(gateway)
            if (
                accounts["review_dispatch_sha256"] != quote["review_dispatch_sha256"]
                or accounts["image_dispatch_sha256"] != quote["image_dispatch_sha256"]
                or gateway.high_price_micros != quote["image_unit_micros"]
                or gateway.pricing_version != quote["pricing_version"]
            ):
                _reject("The selected accounts or prices changed before a new review")
