"""Independent one-pass domestic six-image planning; native Aurora stays frozen."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from sqlalchemy import select

from .aurora_main_image_planner import PAGE_ROLES, AuroraPlanningError
from .config import default_skill_root
from .domain import JobStatus, PlanValidationStatus
from .errors import ApiError
from .models import JobPlanRecord, JobRecord, utc_now
from .new_detail_skill_provider import BundledSkillManifestValidator
from .services import canonical_hash, get_ready_assets, new_id, transition_job
from .xinghe_agent import PlannerRequest, SourceImage

VERSION = "aurora-main-image-fast-v1"
SCHEMA_VERSION = "aurora-main-image-fast-plan-v1"
SKILL_ID = "aurora-domestic-ecommerce-main-image-fast"
PROFILE = "aurora_main_image_fast_suite"
CONCURRENCY = 6


def selected(configuration: Any) -> bool:
    return isinstance(configuration, dict) and (
        configuration.get("settings") or {}
    ).get("main_image_speed") in {"fast", "native_fast"}


def response_schema() -> dict[str, Any]:
    text = {"type": "string", "minLength": 1, "maxLength": 2400}

    def obj(properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    return obj(
        {
            "product_identity_lock": text,
            "confirmed_facts": {"type": "array", "items": text, "maxItems": 64},
            "style_lock": text,
            "pages": {
                "type": "array",
                "minItems": 6,
                "maxItems": 6,
                "items": obj(
                    {
                        "page_index": {"type": "integer", "minimum": 0, "maximum": 5},
                        "page_role": {"type": "string", "enum": list(PAGE_ROLES)},
                        "visual_plan": {
                            "type": "string",
                            "minLength": 30,
                            "maxLength": 3000,
                        },
                        "visible_copy": {
                            "type": "array",
                            "items": text,
                            "maxItems": 12,
                        },
                        "forbidden_elements": text,
                    }
                ),
            },
        }
    )


def validate_plan(plan: JobPlanRecord, job: JobRecord) -> None:
    if job.configuration.get("settings", {}).get("main_image_speed") == "native_fast":
        from .aurora_main_image_native_fast import validate_plan as validate_native

        validate_native(plan, job)
        return
    payload = plan.payload
    authored = {key: payload.get(key) for key in response_schema()["properties"]}
    if (
        not selected(job.configuration)
        or plan.schema_version != SCHEMA_VERSION
        or payload.get("version") != VERSION
        or payload.get("skill_id") != SKILL_ID
        or canonical_hash(payload) != plan.payload_hash
        or not Draft202012Validator(response_schema()).is_valid(authored)
        or [page["page_index"] for page in payload["pages"]] != list(range(6))
        or [page["page_role"] for page in payload["pages"]] != list(PAGE_ROLES)
        or payload.get("canvas") != [1024, 1024]
        or payload.get("product_asset_ids")
        != job.configuration["input"]["product_asset_ids"]
    ):
        raise AuroraPlanningError("Fast main-image plan is incomplete or has changed")
    if BundledSkillManifestValidator().validate(SKILL_ID) != payload.get(
        "skill_content_hash"
    ):
        raise AuroraPlanningError(
            "Fast main-image Skill has changed; create a new task"
        )


def authorize(plan: JobPlanRecord, job: JobRecord) -> None:
    validate_plan(plan, job)
    # Authorization is recorded by the existing confirmation endpoint/submission
    # policy. There is no native Harness session or visual PASS in this mode.


def require_recoverable_plan(session: Any, job: JobRecord) -> None:
    """Reject paid recovery with missing plans or unresolved submissions."""
    if job.type != "domestic_main_image" or not selected(job.configuration):
        return
    plan = session.scalar(
        select(JobPlanRecord)
        .where(JobPlanRecord.job_id == job.id)
        .order_by(JobPlanRecord.revision.desc())
        .limit(1)
    )
    if plan is None or plan.confirmed_at is None:
        raise ApiError(
            409,
            "FAST_MAIN_IMAGE_PLAN_UNAVAILABLE",
            "原任务缺少已确认的完整六图方案，请核对原规划结果后新建任务。",
        )
    try:
        validate_plan(plan, job)
    except AuroraPlanningError as error:
        raise ApiError(409, "FAST_MAIN_IMAGE_PLAN_CHANGED", str(error)) from error
    receipts = job.configuration.get("_main_image_fast_invocations", {})
    if any(
        receipt.get("state") in {"invoked", "unknown"} for receipt in receipts.values()
    ):
        raise ApiError(
            409,
            "PROVIDER_OUTCOME_UNKNOWN",
            "原生图结果尚未核实，请先找回原任务，避免重复生成。",
        )


class FastMainImagePlanner:
    def __init__(self, session_factory: Any, gateway: Any, storage: Any) -> None:
        self.session_factory, self.gateway, self.storage = (
            session_factory,
            gateway,
            storage,
        )

    async def plan(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            existing = session.scalar(
                select(JobPlanRecord).where(JobPlanRecord.job_id == job.id)
            )
            if existing is not None:
                return JobStatus(job.status)
            if job.configuration.get("settings", {}).get("main_image_speed") != "fast":
                raise AuroraPlanningError("Fast main-image requires explicit opt-in")
            config = dict(job.configuration)
            ids = config["input"]["product_asset_ids"]
            if config.get("_main_image_fast_planning"):
                # Duplicate dispatch must not invalidate the active planner.
                return JobStatus(job.status)
            job.configuration = {
                **config,
                "_main_image_fast_planning": {"state": "preparing"},
            }
            session.commit()
            assets = get_ready_assets(session, job.workspace_id, ids)
            records = {
                key: (
                    a.original_filename,
                    a.content_type,
                    a.sha256,
                    a.object_key,
                    a.width,
                    a.height,
                )
                for key, a in assets.items()
            }
            workspace_id = job.workspace_id
        bindings = [
            {
                "asset_id": key,
                "sha256": records[key][2],
                "width": records[key][4],
                "height": records[key][5],
            }
            for key in ids
        ]
        sources = []
        for key in ids:
            name, mime, sha, object_key, _, _ = records[key]
            data = await self.storage.read_bytes(object_key)
            if hashlib.sha256(data).hexdigest() != sha:
                raise AuroraPlanningError("Fast main-image original source SHA changed")
            sources.append(
                SourceImage(
                    asset_id=key,
                    group_key=job_id,
                    filename=name,
                    content_type=mime,
                    sha256=sha,
                    payload=data,
                    source_role="product_reference",
                )
            )
        content_hash = BundledSkillManifestValidator().validate(SKILL_ID)
        root = default_skill_root(SKILL_ID)
        system = (root / "SKILL.md").read_text(encoding="utf-8")
        system += "\n" + "\n".join(
            (root / "references" / name).read_text(encoding="utf-8")
            for name in (
                "six-page-strategy.md",
                "visual-strategy.md",
                "commercial-performance.md",
            )
        )
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            if job.status != JobStatus.PLANNING.value or job.cancel_requested_at:
                return JobStatus(job.status)
            job.configuration = {
                **job.configuration,
                "_main_image_fast_planning": {"state": "invoked"},
            }
            session.commit()
        result = await self.gateway.plan(
            PlannerRequest(
                workspace_id=workspace_id,
                job_id=job_id,
                group_key=job_id,
                sources=tuple(sources),
                page_count=6,
                language="zh-CN",
                product_name=config["settings"]["product_name"],
                platform_code="TAOBAO_DOMESTIC_MAIN_IMAGE",
                size="1024x1024",
                idempotency_key=f"{job_id}:{VERSION}:plan",
                planning_profile=PROFILE,
                system_prompt=system,
                product_information=(
                    json.dumps(
                        {
                            "user_input": config["settings"],
                            "primary_product_asset_id": ids[0],
                            "source_bindings": bindings,
                            "required_page_roles": list(PAGE_ROLES),
                            "response_schema": response_schema(),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        )
        receipt = {
            "state": "returned",
            "provider_request_id": result.provider_request_id,
            "usage": result.normalized_usage,
            "elapsed_seconds": result.api_latency_seconds,
            "actual_cost_micros": result.actual_cost_micros,
            "error_code": result.error_code,
        }
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            job.configuration = {
                **job.configuration,
                "_main_image_fast_planning": receipt,
            }
            if result.error_code or not Draft202012Validator(
                response_schema()
            ).is_valid(result.payload):
                session.commit()
                raise AuroraPlanningError(
                    result.error_message or "Fast six-image plan is incomplete"
                )
            if (
                job.status != JobStatus.PLANNING.value
                or job.cancel_requested_at is not None
            ):
                session.commit()
                return JobStatus(job.status)
            payload = {
                **result.payload,
                "version": VERSION,
                "skill_id": SKILL_ID,
                "skill_content_hash": content_hash,
                "source_bindings": bindings,
                "product_asset_ids": ids,
                "canvas": [1024, 1024],
                "user_facts": config["settings"],
                "execution_policy": {
                    "execution_mode": VERSION,
                    "concurrency": CONCURRENCY,
                    "automatic_redraw_enabled": False,
                },
                "validation": {
                    "status": "STRUCTURALLY_VALIDATED",
                    "visual_review": "NOT_REVIEWED",
                },
                "usage": {
                    "planner": [
                        {
                            "provider": getattr(
                                self.gateway, "provider_name", "unknown"
                            ),
                            "raw_usage": result.raw_usage,
                            "normalized_usage": result.normalized_usage,
                            "reported": bool(
                                (result.normalized_usage or {}).get("reported")
                            ),
                            "provider_request_id_presence": (
                                "present" if result.provider_request_id else "absent"
                            ),
                            "actual_cost_micros": result.actual_cost_micros,
                            "provider_request_id": result.provider_request_id,
                            "api_latency_seconds": result.api_latency_seconds,
                        }
                    ]
                },
            }
            plan = JobPlanRecord(
                id=new_id("plan"),
                workspace_id=workspace_id,
                job_id=job_id,
                revision=1,
                schema_version=SCHEMA_VERSION,
                payload=payload,
                payload_hash=canonical_hash(payload),
                validation_status=PlanValidationStatus.VALID.value,
                validation_errors=[],
                validator_version=VERSION,
                validated_at=utc_now(),
            )
            validate_plan(plan, job)
            session.add(plan)
            transition_job(
                job,
                JobStatus.AWAITING_CONFIRMATION,
                stage="awaiting_plan_confirmation",
                progress=15,
            )
            from .main_image_auto_start import confirm_validated_plan

            confirm_validated_plan(session, job, plan)
            session.commit()
            return JobStatus(job.status)


class MainImagePlannerRouter:
    def __init__(
        self,
        session_factory: Any,
        standard: Any,
        fast: FastMainImagePlanner,
        native_fast: Any = None,
    ) -> None:
        self.session_factory, self.standard, self.fast = session_factory, standard, fast
        self.work_root = standard.work_root
        self.native_fast = native_fast

    async def plan(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            fast = job is not None and selected(job.configuration)
            native = (
                job is not None
                and job.configuration.get("settings", {}).get("main_image_speed")
                == "native_fast"
            )
        return await (
            self.native_fast if native else self.fast if fast else self.standard
        ).plan(job_id)


class MainImageProviderRouter:
    def __init__(self, session_factory: Any, standard: Any, fast: Any) -> None:
        self.session_factory, self.standard, self.fast = session_factory, standard, fast
        self.name, self.model, self.pricing_version = (
            standard.name,
            standard.model,
            standard.pricing_version,
        )

    async def execute(self, request: Any) -> Any:
        with self.session_factory() as session:
            job = session.get(JobRecord, request.job_id)
            fast = job is not None and selected(job.configuration)
        return await (self.fast if fast else self.standard).execute(request)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.standard, name)
