"""Aurora v0.1.17 identity lock and six-page compilation workflow."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session, sessionmaker

from .aurora_execution_modes import (
    is_calibrated_parallel_aurora_mode,
    is_parallel_aurora_mode,
    is_v20_aurora_mode,
)
from .aurora_generation_shadow import (
    CONTRACT_FIRST_REBUILD_V1,
    EXECUTION_SEMANTICS_V1,
)
from .aurora_main_image_runtime import (
    AURORA_MAIN_IMAGE_V20_VERSION,
    AURORA_MAIN_IMAGE_VERSION,
    AuroraHarnessRunner,
    AuroraV20HarnessRunner,
    write_contract,
)
from .config import default_aurora_work_root
from .domain import JobStatus, PlanValidationStatus
from .models import (
    AssetRecord,
    AuditEventRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    utc_now,
)
from .services import canonical_hash, new_id, transition_job
from .storage import ImageStorage
from .xinghe_agent import PlannerRequest, SourceImage, XingheGateway

PAGE_ROLES = (
    "SEARCH_MAIN_IMAGE_LISTING_COVER",
    "CAROUSEL_IDENTITY_HERO",
    "CAROUSEL_CORE_FEATURE_PROOF",
    "CAROUSEL_SCENE_OR_OPERATION",
    "CAROUSEL_DETAIL_OR_TRUST",
    "CAROUSEL_OFFER_OR_SPEC_CLOSURE",
)

_IDENTITY_SYSTEM_PROMPT = """
You are the product-identity inspection stage of the bundled Aurora domestic
e-commerce main-image Skill. Inspect every attached original image as
complementary evidence of one exact product. Return only the requested JSON.
Describe only visible identity facts. Never infer performance, material,
certification, offer, sales, or service claims from pixels. Source backgrounds,
desks, walls, props, lighting and capture clutter are not product identity.
Preserve silhouette, proportions, color blocks, component count, connections,
opening structure, packaging layout and visible label placement. Do not use a
generated image as evidence. Keep profile01_semantics_read false.
""".strip()


class AuroraPlanningError(RuntimeError):
    # Optional bounded response-shape evidence attached by provider boundary
    # failures.  Raw prompts, response bodies and credentials are never stored.
    aurora_gateway_diagnostics: dict[str, Any] | None = None
    # Upstream HTTP status, when the failure came from a gateway response.
    # Without this the transient-failure classifier cannot tell a retryable
    # 429/502/503 apart from a permanent contract violation, so the configured
    # retry policy never engages.
    http_status: int | None = None


class _PlannerGateway(Protocol):
    async def plan(self, request: PlannerRequest) -> Any: ...


class AuroraMainImagePlanner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        harness: AuroraHarnessRunner | None = None,
        *,
        storage: ImageStorage | None = None,
        gateway: XingheGateway | _PlannerGateway | None = None,
        work_root: Path | None = None,
        execution_mode: str = "sequential_v20",
    ) -> None:
        self.session_factory = session_factory
        self.execution_mode = execution_mode
        self.harness = harness or (
            AuroraV20HarnessRunner()
            if is_v20_aurora_mode(execution_mode)
            else AuroraHarnessRunner()
        )
        self.storage = storage
        self.gateway = gateway
        self.work_root = (work_root or default_aurora_work_root()).resolve()

    async def plan(self, job_id: str) -> JobStatus | None:
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            existing = session.scalar(
                select(JobPlanRecord.id).where(JobPlanRecord.job_id == job.id).limit(1)
            )
            if existing is not None:
                return JobStatus(job.status)
            configuration = (
                job.configuration if isinstance(job.configuration, dict) else {}
            )
            input_value = configuration.get("input", {})
            settings = configuration.get("settings", {})
            if not isinstance(input_value, Mapping) or not isinstance(
                settings, Mapping
            ):
                raise AuroraPlanningError("Aurora job configuration is malformed")
            asset_ids = [
                str(value) for value in input_value.get("product_asset_ids", [])
            ]
            assets = session.scalars(
                select(AssetRecord).where(
                    AssetRecord.workspace_id == job.workspace_id,
                    AssetRecord.id.in_(asset_ids),
                    AssetRecord.deleted_at.is_(None),
                )
            ).all()
            asset_by_id = {asset.id: asset for asset in assets}
            if set(asset_ids) != set(asset_by_id):
                raise AuroraPlanningError(
                    "One or more product identity assets are missing"
                )
            workspace_id = job.workspace_id

        sources, evidence_paths = await self._materialize_sources(
            workspace_id, job_id, asset_ids, asset_by_id
        )
        raw = _build_raw(job_id, settings, evidence_paths)
        identity = await self._inspect_identity(job_id, workspace_id, settings, sources)
        identity = _bind_identity_evidence(identity, sources, evidence_paths)
        if is_v20_aurora_mode(self.execution_mode):
            identity.pop("profile01_semantics_read", None)
        workspace = self.job_workspace(job_id)
        input_path = workspace / "input.json"
        identity_path = workspace / "identity.json"
        session_path = workspace / "session.json"
        compiled_path = workspace / (
            "compiled-v20.json"
            if is_v20_aurora_mode(self.execution_mode)
            else "compiled-v013.json"
        )
        write_contract(input_path, raw)
        write_contract(identity_path, identity)

        harness_session = _load_json(session_path) if session_path.exists() else None
        if harness_session is None:
            self.harness.run(
                "start", workspace, {"input": input_path, "session": session_path}
            )
            harness_session = _load_json(session_path)
        _require_session(job_id, harness_session)
        if harness_session.get("state") == "AWAITING_IDENTITY":
            self.harness.run(
                "lock-identity",
                workspace,
                {"session": session_path, "identity": identity_path},
            )
            harness_session = _load_json(session_path)
        if harness_session.get("state") == "IDENTITY_LOCKED":
            if is_v20_aurora_mode(self.execution_mode):
                self.harness.run(
                    "compile",
                    workspace,
                    {"session": session_path},
                )
            else:
                self.harness.run(
                    "compile-v013",
                    workspace,
                    {"session": session_path, "output": compiled_path},
                )
            harness_session = _load_json(session_path)
        if harness_session.get("state") != "AWAITING_AUTHORIZATION":
            raise AuroraPlanningError(
                f"Aurora compilation ended in {harness_session.get('state')}"
            )

        payload = (
            _public_v20_plan_payload(
                settings,
                identity,
                harness_session,
                execution_mode=self.execution_mode,
            )
            if is_v20_aurora_mode(self.execution_mode)
            else _public_plan_payload(settings, identity, harness_session)
        )
        payload["_harness_session"] = harness_session
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job is None:
                return None
            existing = session.scalar(
                select(JobPlanRecord.id).where(JobPlanRecord.job_id == job.id).limit(1)
            )
            if existing is not None:
                return JobStatus(job.status)
            plan = JobPlanRecord(
                id=new_id("plan"),
                workspace_id=job.workspace_id,
                job_id=job.id,
                revision=1,
                schema_version="aurora-main-image-plan-v2",
                payload=payload,
                payload_hash=canonical_hash(payload),
                validation_status=PlanValidationStatus.VALID.value,
                validation_errors=[],
                validator_version=(
                    f"aurora-harness-{AURORA_MAIN_IMAGE_V20_VERSION}"
                    if is_v20_aurora_mode(self.execution_mode)
                    else f"aurora-harness-{AURORA_MAIN_IMAGE_VERSION}"
                ),
                validated_at=utc_now(),
            )
            session.add(plan)
            session.add(
                AuditEventRecord(
                    workspace_id=job.workspace_id,
                    action=(
                        "aurora.plan_compiled_v20"
                        if is_v20_aurora_mode(self.execution_mode)
                        else "aurora.plan_compiled_v013"
                    ),
                    resource_type="job",
                    resource_id=job.id,
                    request_id=plan.id,
                    details={
                        "page_count": 6,
                        "harness_state": harness_session["state"],
                        "compiled_hash": harness_session.get("compiled_hash"),
                        "campaign_style_id": harness_session.get("campaign_style_id"),
                    },
                )
            )
            transition_job(
                job,
                JobStatus.AWAITING_CONFIRMATION,
                stage="awaiting_plan_confirmation",
                progress=15,
            )
            from .main_image_auto_start import confirm_validated_plan

            confirm_validated_plan(session, job, plan, work_root=self.work_root)
            session.commit()
            return JobStatus(job.status)

    def job_workspace(self, job_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
            raise AuroraPlanningError("Invalid Aurora job identifier")
        workspace = (self.work_root / job_id).resolve()
        if self.work_root != workspace and self.work_root not in workspace.parents:
            raise AuroraPlanningError("Aurora workspace escaped its configured root")
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    async def _materialize_sources(
        self,
        workspace_id: str,
        job_id: str,
        asset_ids: Sequence[str],
        assets: Mapping[str, AssetRecord],
    ) -> tuple[tuple[SourceImage, ...], list[str]]:
        if self.storage is None:
            raise AuroraPlanningError(
                "Aurora identity inspection requires image storage"
            )
        evidence_root = self.job_workspace(job_id) / "evidence"
        evidence_root.mkdir(parents=True, exist_ok=True)
        sources: list[SourceImage] = []
        paths: list[str] = []
        for ordinal, asset_id in enumerate(asset_ids):
            asset = assets[asset_id]
            payload = await self.storage.read_bytes(asset.object_key)
            if hashlib.sha256(payload).hexdigest() != asset.sha256:
                raise AuroraPlanningError(
                    "Product identity asset integrity check failed"
                )
            suffix = Path(asset.filename).suffix.lower() or _suffix_for_type(
                asset.content_type
            )
            path = (evidence_root / f"source-{ordinal + 1:02d}{suffix}").resolve()
            if evidence_root.resolve() not in path.parents:
                raise AuroraPlanningError("Invalid evidence path")
            path.write_bytes(payload)
            sources.append(
                SourceImage(
                    asset_id=asset.id,
                    group_key=job_id,
                    filename=asset.filename,
                    content_type=asset.content_type,
                    sha256=asset.sha256,
                    payload=payload,
                    source_role="product_reference",
                )
            )
            paths.append(str(path))
        return tuple(sources), paths

    async def _inspect_identity(
        self,
        job_id: str,
        workspace_id: str,
        settings: Mapping[str, Any],
        sources: tuple[SourceImage, ...],
    ) -> dict[str, Any]:
        if self.gateway is None:
            raise AuroraPlanningError(
                "Aurora identity inspection gateway is unavailable"
            )
        facts = _all_facts(settings)
        result = await self.gateway.plan(
            PlannerRequest(
                workspace_id=workspace_id,
                job_id=job_id,
                group_key=job_id,
                sources=sources,
                page_count=6,
                language="zh-CN",
                product_name=_text(settings.get("product_name"), "当前商品"),
                product_information=tuple(facts),
                platform_code="TAOBAO_DOMESTIC_MAIN_IMAGE",
                size="1024x1024",
                idempotency_key=f"{job_id}:aurora-identity:v1",
                system_prompt=_IDENTITY_SYSTEM_PROMPT,
                planning_profile="aurora_identity",
            )
        )
        if result.error_code or not isinstance(result.payload, Mapping):
            raise AuroraPlanningError(
                result.error_message or "Aurora product identity inspection failed"
            )
        return dict(result.payload)


def authorize_aurora_plan(
    plan: JobPlanRecord,
    job: JobRecord,
    approval_text: str,
    *,
    harness: AuroraHarnessRunner | None = None,
    work_root: Path | None = None,
    execution_mode: str | None = None,
) -> None:
    """Record the first and only pre-generation user authorization in Harness."""

    from .aurora_main_image_native_fast import (
        SCHEMA_VERSION as NATIVE_FAST_SCHEMA,
    )
    from .aurora_main_image_native_fast import authorize as authorize_native

    if plan.schema_version == NATIVE_FAST_SCHEMA:
        authorize_native(plan, job, approval_text)
        return

    if plan.schema_version != "aurora-main-image-plan-v2":
        raise AuroraPlanningError("This task uses the retired local-rule plan")
    workspace = _job_workspace(job.id, work_root)
    session_path = workspace / "session.json"
    stored = plan.payload.get("_harness_session")
    if not isinstance(stored, Mapping):
        raise AuroraPlanningError("Aurora Harness session is missing")
    write_contract(session_path, dict(stored))
    policy = plan.payload.get("execution_policy")
    stored_mode = (
        policy.get("execution_mode") if isinstance(policy, Mapping) else None
    )
    mode = execution_mode or str(stored_mode or "sequential_v20")
    runner = harness or (
        AuroraV20HarnessRunner()
        if is_v20_aurora_mode(mode)
        else AuroraHarnessRunner()
    )
    runner.run(
        "authorize",
        workspace,
        {"session": session_path, "approval-text": approval_text},
    )
    harness_session = _load_json(session_path)
    payload = dict(plan.payload)
    payload["_harness_session"] = harness_session
    payload["harness_state"] = harness_session["state"]
    plan.payload = payload
    plan.payload_hash = canonical_hash(payload)
    if is_parallel_aurora_mode(mode):
        _persist_parallel_v20_snapshots(
            plan,
            job,
            workspace,
            harness_session,
            runner,
            page_indexes=(
                (0,)
                if is_calibrated_parallel_aurora_mode(mode)
                else tuple(range(6))
            ),
            execution_mode=mode,
        )


def public_aurora_plan_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep internal execution contracts durable without returning them to browsers."""

    public = {key: value for key, value in payload.items() if not key.startswith("_")}
    final_session = next(
        (
            value
            for key in ("_sequential_v20_set_session", "_parallel_v20_set_session")
            if isinstance(value := payload.get(key), Mapping)
        ),
        None,
    )
    if final_session is not None:
        public["final_review"] = {
            "set_audit_passed": final_session.get("set_audit_passed") is True,
            "residual_issues": [
                {
                    "page_index": issue.get("page_index"),
                    "decision_summary": str(
                        issue.get("decision_summary") or "当前页仍有未解决的审计问题。"
                    ),
                }
                for issue in _mapping_list(final_session.get("residual_issues"))
            ],
        }
    return public


def _persist_parallel_v20_snapshots(
    plan: JobPlanRecord,
    job: JobRecord,
    workspace: Path,
    authorized_session: Mapping[str, Any],
    runner: AuroraHarnessRunner,
    *,
    page_indexes: Sequence[int] = tuple(range(6)),
    execution_mode: str = "parallel_v20",
) -> None:
    """Freeze the requested page batch without changing native page contracts.

    Each raw v2.0 session clone is page-local. The calibrated shadow mode calls
    this helper for one barrier release at a time, while the legacy parallel
    mode still passes all six indexes at authorization.
    """

    db = object_session(plan)
    if db is None:
        raise AuroraPlanningError("Aurora plan is detached from its transaction")
    items = db.scalars(
        select(JobItemRecord)
        .where(JobItemRecord.job_id == job.id)
        .order_by(JobItemRecord.ordinal)
        .with_for_update()
    ).all()
    if len(items) != 6:
        raise AuroraPlanningError("Aurora parallel mode requires exactly six items")
    style_lock = _mapping(
        _mapping(authorized_session.get("plan")).get("campaign_style")
    )
    identity_hash = str(authorized_session.get("identity_digest") or "")
    plan_hash = str(authorized_session.get("plan_digest") or plan.payload_hash)
    source_hashes: dict[str, str] = {}
    for row in _mapping_list(
        _mapping(authorized_session.get("identity")).get("evidence")
    ):
        source_path = Path(str(row.get("path") or ""))
        if not source_path.is_file():
            raise AuroraPlanningError("Aurora identity evidence disappeared")
        source_hashes[str(row.get("evidence_id"))] = hashlib.sha256(
            source_path.read_bytes()
        ).hexdigest()
    job_configuration = (
        job.configuration if isinstance(job.configuration, Mapping) else {}
    )
    job_settings = job_configuration.get("settings", {})
    shadow_mode = (
        str(job_settings.get("aurora_shadow_mode") or "").strip()
        if isinstance(job_settings, Mapping)
        else ""
    )
    authorized_facts = (
        _normalize_commercial_fact_pool(job_settings)
        if isinstance(job_settings, Mapping)
        else {}
    )
    if shadow_mode not in {
        "",
        "off",
        CONTRACT_FIRST_REBUILD_V1,
        EXECUTION_SEMANTICS_V1,
    }:
        raise AuroraPlanningError("Unsupported Aurora generation shadow mode")
    requested_indexes = set(page_indexes)
    if not requested_indexes.issubset(set(range(6))):
        raise AuroraPlanningError("Aurora parallel snapshot page index is invalid")
    snapshots: list[dict[str, Any]] = []
    for item in items:
        if item.ordinal not in requested_indexes:
            continue
        page_workspace = workspace / "parallel-v20" / f"page-{item.ordinal + 1:02d}"
        page_workspace.mkdir(parents=True, exist_ok=True)
        page_session = dict(authorized_session)
        page_session["state"] = "AUTHORIZED"
        page_session["accepted_pages"] = list(range(item.ordinal))
        page_session["active_page_index"] = None
        page_session["pending_generation"] = None
        # Page 0 performs search-baseline calibration and page 1 performs the
        # first-two-page Campaign Style calibration after their pixels return.
        # Pages 2-5 use the immutable campaign style lock compiled during
        # planning. Marking that lock as frozen in their page-local sessions
        # lets all six requests be dispatched together; it does not fabricate
        # a calibration result or alter the native page contracts.
        style_ready = authorized_session.get("style_calibrated") is True
        page_session["search_calibrated"] = item.ordinal >= 1 or style_ready
        page_session["style_calibrated"] = style_ready or item.ordinal >= 2
        page_session["parallel_style_lock_frozen"] = item.ordinal >= 2
        session_path = page_workspace / "session.json"
        request_path = page_workspace / "generation-request.json"
        write_contract(session_path, page_session)
        result = runner.run(
            "generate-next",
            page_workspace,
            {"session": session_path, "out": request_path},
        )
        request_value = result.get("generation_request")
        request_payload = (
            dict(request_value)
            if isinstance(request_value, Mapping)
            else _load_json(request_path)
        )
        if int(request_payload.get("page_index", -1)) != item.ordinal:
            raise AuroraPlanningError("Aurora page snapshot ordinal mismatch")
        # Carry the user-entered fact buckets into the ephemeral generation request.
        # The image model may render only these exact copy seeds; planner prose is
        # a layout hint, never a new commercial fact authority.
        request_payload["authorized_facts"] = dict(authorized_facts)
        snapshot = {
            "schema_version": "aurora-parallel-page-snapshot-v1",
            "execution_mode": execution_mode,
            "page_index": item.ordinal,
            "group_key": job.id,
            "prompt": request_payload["prompt"],
            "imagegen_reference_paths": request_payload[
                "imagegen_reference_paths"
            ],
            "edit_target": request_payload.get("edit_target"),
            "generation_token": request_payload["generation_token"],
            "page_revision": int(request_payload.get("revision", 0)),
            "plan_revision": plan.revision,
            "plan_hash": plan_hash,
            "identity_hash": identity_hash,
            "style_lock": style_lock,
            "source_asset_hashes": source_hashes,
            "logical_idempotency_key": (
                f"{job.id}:{item.id}:plan-{plan.revision}:"
                f"page-{int(request_payload.get('revision', 0))}"
            ),
            "request": request_payload,
            "session": _load_json(session_path),
        }
        snapshot["snapshot_hash"] = canonical_hash(snapshot)
        configuration = dict(item.configuration or {})
        configuration.update(
            {
                "aurora_execution_mode": execution_mode,
                "aurora_snapshot": snapshot,
                "size": "1024x1024",
            }
        )
        if shadow_mode in {CONTRACT_FIRST_REBUILD_V1, EXECUTION_SEMANTICS_V1}:
            configuration["aurora_shadow_mode"] = shadow_mode
        item.configuration = configuration
        snapshots.append(snapshot)
    payload = dict(plan.payload)
    existing = payload.get("_parallel_v20_snapshots")
    existing_snapshots = (
        [dict(item) for item in existing if isinstance(item, Mapping)]
        if isinstance(existing, list)
        else []
    )
    by_page = {
        int(item["page_index"]): item
        for item in existing_snapshots
        if item.get("page_index") is not None
    }
    by_page.update({int(item["page_index"]): item for item in snapshots})
    payload["_parallel_v20_snapshots"] = [by_page[index] for index in sorted(by_page)]
    payload["snapshot_manifest_hash"] = canonical_hash(
        [snapshot["snapshot_hash"] for snapshot in payload["_parallel_v20_snapshots"]]
    )
    plan.payload = payload
    plan.payload_hash = canonical_hash(payload)


def _build_raw(
    job_id: str,
    settings: Mapping[str, Any],
    evidence_paths: Sequence[str],
) -> dict[str, Any]:
    fact_pool = _normalize_commercial_fact_pool(settings)
    return {
        "request_id": job_id,
        "profile01_semantics_read": False,
        "style_preferences": _text_list(settings.get("style_preferences")),
        "instruction": _text(settings.get("instruction"), ""),
        "product": {
            "name": _text(settings.get("product_name"), "当前商品"),
            "category": _text(settings.get("category"), "商品"),
            "original_references": list(evidence_paths),
            "user_provided_display_facts": fact_pool["display_facts"],
            "verified_selling_points": fact_pool["selling_points"],
            "verified_attributes": fact_pool["attributes"],
            "verified_scenes": fact_pool["scenes"],
            "verified_offers": fact_pool["offers"],
            "verified_service_or_trust_facts": fact_pool["service_or_trust"],
        },
    }


_SECTION_HEADING_TERMS = (
    "完整产品信息",
    "基础商品信息",
    "核心产品参数与功能",
    "核心制冷风力",
    "机身设计",
    "续航与供电",
    "款式定位与适配人群",
    "外观配色与版型",
    "商品信息",
)
_SERVICE_TERMS = (
    "发货",
    "物流",
    "免运费",
    "客服",
    "售后",
    "退货",
    "包退",
    "店铺评分",
    "好评率",
    "响应",
)
_OFFER_TERMS = ("到手价", "优惠券", "满减", "赠品", "折扣", "活动价")
_SCENE_TERMS = (
    "日常",
    "通勤",
    "出游",
    "慢跑",
    "居家",
    "办公",
    "户外",
    "旅行",
    "课堂",
    "上课",
    "宿舍",
    "图书馆",
    "桌面",
    "手机辅助散热",
    "使用场景",
)
_FIT_TERMS = (
    "适配人群",
    "学生党",
    "小个子",
    "宽胖脚",
    "高脚背",
    "男女均可",
    "情侣",
    "适用人群",
)
_BENEFIT_TERMS = (
    "透气",
    "清爽",
    "舒适",
    "轻量",
    "防滑",
    "抓地",
    "缓震",
    "耐磨",
    "柔软",
    "保暖",
    "防水",
    "便携",
)
_MATERIAL_OR_STRUCTURE_TERMS = (
    "材质",
    "面料",
    "翻毛皮",
    "皮革",
    "纹理",
    "结构",
    "剪裁",
    "流线",
    "鞋底",
    "鞋面",
    "做工",
)
_APPEARANCE_TERMS = (
    "配色",
    "撞色",
    "颜色",
    "复古",
    "风格",
    "设计",
    "版型",
    "品类",
    "款式",
)
_CHOICE_OR_SPEC_TERMS = (
    "男女同款",
    "情侣",
    "可选",
    "多色",
    "尺码",
    "规格",
    "季节",
    "夏季",
    "冬季",
)
_IDENTITY_ONLY_TERMS = ("商品全称", "品牌：", "品牌:", "别名")


def _normalize_commercial_fact_pool(
    settings: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Route pasted product dossiers into the Skill's factual authority buckets.

    Users often paste a complete product dossier into the selling-point field.
    Headings are not facts, and service/trust lines must never compete with
    product benefits for the first five page duties. Exact source lines remain
    intact inside their routed bucket.
    """

    pasted = _text_list(settings.get("selling_points"))
    display_facts = _text_list(settings.get("user_provided_display_facts"))
    explicit_attributes = _text_list(settings.get("product_attributes"))
    explicit_scenes = _text_list(settings.get("scenes"))
    explicit_offers = _text_list(settings.get("offers"))
    explicit_service = _text_list(settings.get("service_or_trust_facts"))
    product_claims: list[str] = []
    attributes: list[str] = list(explicit_attributes)
    scenes: list[str] = list(explicit_scenes)
    offers: list[str] = list(explicit_offers)
    service: list[str] = list(explicit_service)

    for fact in pasted:
        if _is_section_heading(fact):
            continue
        if _contains_any(fact, _SERVICE_TERMS):
            service.append(fact)
            continue
        if _contains_any(fact, _OFFER_TERMS):
            offers.append(fact)
            continue
        if _contains_any(fact, _SCENE_TERMS):
            scenes.append(fact)
            continue
        if _contains_any(fact, _IDENTITY_ONLY_TERMS):
            attributes.append(fact)
            continue
        product_claims.append(fact)

    # The compiler already consumes every authority bucket. Keep scenes and
    # attributes out of ``verified_selling_points`` so fact_records preserves
    # their source provenance and the page-role bonuses can select them for
    # the scene/detail duties instead of treating them as generic benefits.
    selling_points = _prioritize_for_six_page_duties(product_claims)
    return {
        "display_facts": list(dict.fromkeys(display_facts)),
        "selling_points": selling_points,
        "attributes": list(dict.fromkeys(attributes)),
        "scenes": list(dict.fromkeys(scenes)),
        "offers": list(dict.fromkeys(offers)),
        "service_or_trust": list(dict.fromkeys(service)),
    }


def _prioritize_for_six_page_duties(claims: Sequence[str]) -> list[str]:
    selectors = (
        _BENEFIT_TERMS,
        _FIT_TERMS,
        _APPEARANCE_TERMS,
        _MATERIAL_OR_STRUCTURE_TERMS,
        _SCENE_TERMS,
        _CHOICE_OR_SPEC_TERMS,
    )
    selected: list[str] = []
    for terms in selectors:
        candidates = [
            claim
            for claim in claims
            if claim not in selected and _contains_any(claim, terms)
        ]
        if candidates:
            selected.append(
                max(candidates, key=lambda claim: _claim_specificity(claim, terms))
            )
    selected.extend(claim for claim in claims if claim not in selected)
    return selected


def _claim_specificity(claim: str, terms: Sequence[str]) -> tuple[int, int, int]:
    matches = sum(term in claim for term in terms)
    physical = sum(term in claim for term in _MATERIAL_OR_STRUCTURE_TERMS)
    return matches, physical, min(len(claim), 80)


def _is_section_heading(value: str) -> bool:
    compact = re.sub(r"^[一二三四五六七八九十0-9]+[、.．]\s*", "", value).strip()
    return len(compact) <= 24 and any(
        term == compact or compact.endswith(term) for term in _SECTION_HEADING_TERMS
    )


def _contains_any(value: str, terms: Sequence[str]) -> bool:
    return any(term in value for term in terms)


def _public_plan_payload(
    settings: Mapping[str, Any],
    identity: Mapping[str, Any],
    harness_session: Mapping[str, Any],
) -> dict[str, Any]:
    plan = _mapping(harness_session.get("plan"))
    orchestration = _mapping(plan.get("commercial_orchestration"))
    campaign_style = _mapping(plan.get("campaign_style"))
    constitution = _mapping(harness_session.get("campaign_constitution"))
    matches = {
        int(row.get("page_index", -1)): row
        for row in _mapping_list(orchestration.get("demand_to_selling_point_match"))
    }
    rhythms = {
        int(row.get("page_index", -1)): row
        for row in _mapping_list(orchestration.get("visual_rhythm_plan"))
    }
    tasks = {
        int(row.get("page_index", -1)): row
        for row in _mapping_list(orchestration.get("page_task_table"))
    }
    compiled_pages = {
        int(row.get("index", -1)): row for row in _mapping_list(plan.get("pages"))
    }
    pages: list[dict[str, Any]] = []
    previous_claim = ""
    for index, role in enumerate(PAGE_ROLES):
        match = _mapping(matches.get(index))
        task = _mapping(tasks.get(index))
        rhythm = _mapping(rhythms.get(index))
        compiled_page = _mapping(compiled_pages.get(index))
        execution = _mapping(compiled_page.get("approved_page_execution_contract"))
        proof = _mapping(execution.get("selling_point_visual_proof_contract"))
        lead = _text(match.get("matched_selling_point"), "商品身份与核心购买理由")
        bundle = [
            value
            for value in _text_list(proof.get("bundle_source_texts"))
            if value != lead
        ]
        difference = (
            f"与上一页“{previous_claim}”采用不同证明对象与页面职责"
            if previous_claim
            else "唯一承担搜索点击入口，不与轮播身份页共用版式骨架"
        )
        pages.append(
            {
                "page_index": index,
                "page_role": role,
                "main_selling_point": lead,
                "supporting_selling_points": bundle,
                "visual_proof": _text(
                    match.get("visual_proof_method")
                    or proof.get("pixel_evidence_method"),
                    "以当前商品的真实结构、部件、材质、动作或场景证明",
                ),
                "composition_and_subject": _text(
                    execution.get("composition_blueprint")
                    or rhythm.get("layout_structure"),
                    "围绕当前页面职责建立独立构图",
                ),
                "reading_path_and_type_hierarchy": " → ".join(
                    _text_list(execution.get("reading_path"))
                )
                or _text(task.get("copy_direction"), "主结论 → 真实证据 → 短闭合"),
                "non_repetition_reason": difference,
                "commercial_closure": _text(
                    task.get("buyer_demand"),
                    _text(match.get("buyer_demand"), "完成当前购买问题的确认"),
                ),
                "buyer_question": _text(task.get("buyer_question"), ""),
                "visual_rhythm": {
                    "subject_scale": _text(rhythm.get("subject_scale"), ""),
                    "layout_structure": _text(rhythm.get("layout_structure"), ""),
                },
                "status": "awaiting_plan_approval",
            }
        )
        previous_claim = lead
    background = _mapping(constitution.get("background_style_lock"))
    return {
        "schema_version": "aurora-main-image-plan-v2",
        "skill_id": "aurora-domestic-ecommerce-main-image",
        "skill_version": AURORA_MAIN_IMAGE_VERSION,
        "compiler_lineage": harness_session.get("compiler_lineage"),
        "compiled_hash": harness_session.get("compiled_hash"),
        "harness_state": harness_session.get("state"),
        "approval_checkpoint": "six_page_plan",
        "product": {
            "name": settings.get("product_name"),
            "category": settings.get("category"),
        },
        "product_identity_lock": {
            "product_form": identity.get("product_form"),
            "identity_summary": identity.get("identity_summary"),
            "must_preserve": identity.get("must_preserve", []),
            "must_not_change": identity.get("must_not_change", []),
        },
        "campaign_style": campaign_style,
        "background_style_lock": background,
        "visual_rhythm_plan": list(rhythms.values()),
        "pages": pages,
        "execution_policy": {
            "sequence": "strictly_sequential",
            "max_automatic_repairs_per_page": 1,
            "plan_authorization_required": True,
            "final_set_approval_required": True,
        },
    }


def _public_v20_plan_payload(
    settings: Mapping[str, Any],
    identity: Mapping[str, Any],
    harness_session: Mapping[str, Any],
    *,
    execution_mode: str = "parallel_v20",
) -> dict[str, Any]:
    """Expose the native v2.0 plan without passing through v013 rewrites."""

    native_plan = _mapping(harness_session.get("plan"))
    native_pages = _mapping_list(native_plan.get("pages"))
    pages = [
        {
            "page_index": int(page.get("index", index)),
            "page_role": page.get("page_role", PAGE_ROLES[index]),
            "main_selling_point": page.get("primary_claim", ""),
            "supporting_selling_points": page.get("supporting_claims", []),
            "visual_proof": page.get("visual_proof", ""),
            "composition_and_subject": page.get("composition", ""),
            "reading_path_and_type_hierarchy": " → ".join(
                _text_list(page.get("reading_path"))
            ),
            "non_repetition_reason": page.get("non_repetition_reason", ""),
            "commercial_closure": page.get("commercial_closure", ""),
            "status": "awaiting_plan_approval",
        }
        for index, page in enumerate(native_pages)
    ]
    if len(pages) != 6:
        raise AuroraPlanningError("Native Aurora v2.0 plan must contain six pages")
    return {
        "schema_version": "aurora-main-image-plan-v2",
        "skill_id": "aurora-domestic-ecommerce-main-image",
        "skill_version": AURORA_MAIN_IMAGE_V20_VERSION,
        "compiler_lineage": "aurora-native-v2.0.0",
        "harness_state": harness_session.get("state"),
        "approval_checkpoint": "six_page_plan",
        "product": {
            "name": settings.get("product_name"),
            "category": settings.get("category"),
        },
        "product_identity_lock": {
            "product_form": identity.get("product_form"),
            "identity_summary": identity.get("identity_summary"),
            "must_preserve": identity.get("must_preserve", []),
            "must_not_change": identity.get("must_not_change", []),
        },
        "campaign_style": native_plan.get("campaign_style", {}),
        "pages": pages,
        "execution_policy": {
            "execution_mode": execution_mode,
            "sequence": (
                "strictly_sequential"
                if execution_mode == "sequential_v20"
                else (
                "parallel_calibrated_barrier"
                if is_calibrated_parallel_aurora_mode(execution_mode)
                else "parallel_frozen_snapshots"
                )
            ),
            "max_parallel_pages": 1 if execution_mode == "sequential_v20" else 6,
            "max_automatic_repairs_per_page": 1,
            "plan_authorization_required": True,
            "final_set_approval_required": True,
        },
    }


def _bind_identity_evidence(
    payload: Mapping[str, Any],
    sources: Sequence[SourceImage],
    paths: Sequence[str],
) -> dict[str, Any]:
    by_id = {
        str(row.get("evidence_id")): _text_list(row.get("view_tags"))
        for row in _mapping_list(payload.get("evidence"))
    }
    evidence = [
        {
            "evidence_id": source.asset_id,
            "path": paths[index],
            "view_tags": by_id.get(source.asset_id) or [f"原始商品视图 {index + 1}"],
        }
        for index, source in enumerate(sources)
    ]
    identity = {
        # request_id is runtime-owned state, not model-authored product content.
        # Binding it to the current source group prevents a provider from
        # returning a stale or hallucinated task id that the Harness must reject.
        "request_id": sources[0].group_key,
        "product_form": _text(payload.get("product_form"), "当前商品的可见完整形态"),
        "identity_summary": _text(
            payload.get("identity_summary"),
            "以全部原始商品素材共同锁定同一商品身份。",
        ),
        "evidence": evidence,
        "must_preserve": _text_list(payload.get("must_preserve"))
        or ["素材中可见的完整轮廓、比例、颜色分区、部件数量与相对位置"],
        "must_not_change": _text_list(payload.get("must_not_change"))
        or ["不得增加、删除或重设计素材中可见的商品结构"],
        "previous_generated_images_used": False,
        "profile01_semantics_read": False,
    }
    if not identity["request_id"]:
        raise AuroraPlanningError("Identity inspection omitted request_id")
    return identity


def _all_facts(settings: Mapping[str, Any]) -> list[str]:
    values = [
        f"商品类目：{_text(settings.get('category'), '商品')}",
        *_text_list(settings.get("user_provided_display_facts")),
        *_text_list(settings.get("selling_points")),
        *_text_list(settings.get("product_attributes")),
        *_text_list(settings.get("scenes")),
        *_text_list(settings.get("offers")),
        *_text_list(settings.get("service_or_trust_facts")),
        *_text_list(settings.get("style_preferences")),
    ]
    return list(dict.fromkeys(value for value in values if value))


def _job_workspace(job_id: str, work_root: Path | None = None) -> Path:
    root = (work_root or default_aurora_work_root()).resolve()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
        raise AuroraPlanningError("Invalid Aurora job identifier")
    workspace = (root / job_id).resolve()
    if root != workspace and root not in workspace.parents:
        raise AuroraPlanningError("Aurora workspace escaped its configured root")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _require_session(job_id: str, session: Mapping[str, Any]) -> None:
    if session.get("request_id") != job_id:
        raise AuroraPlanningError("Aurora Harness session belongs to another task")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuroraPlanningError(f"Expected a JSON object at {path.name}")
    return value


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _text(value: object, fallback: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or fallback


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _suffix_for_type(content_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type.lower(), ".img")
