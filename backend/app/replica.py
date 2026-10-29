"""Workspace-owned product imports and explicitly confirmed reference remixes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from urllib.request import Request as UrlRequest
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Request, UploadFile
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import (
    func,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .auth import WorkspaceContext, WorkspaceDependency, WorkspaceWriteDependency
from .database import get_session
from .domain import AssetMediaKind, AssetOriginKind
from .errors import ApiError
from .everyinfra import fetch_product
from .models import (
    JobItemRecord,
    JobRecord,
    ReplicaDocumentRecord,
    WorkspaceRecord,
    utc_now,
)
from .new_detail_skill_provider import BundledSkillManifestValidator
from .new_image_skill_provider import TARGET_DIMENSIONS
from .product_sources import SOURCES, ProductPlatform, image_url, parse_product_link
from .replica_native import (
    SKILLS,
    NativeReplicaRun,
    ReplicaAnalysisUnavailable,
    ReplicaInputTooLarge,
    ReplicaNativeError,
    ReplicaPlanAuditRejected,
    ReplicaSegmentationInvalid,
    native_accounting,
    read_json,
)
from .replica_progress import reconcile_document
from .replica_style_evidence import StyleEvidenceInvalid
from .schemas_v1 import ReferenceRemixJobCreate
from .services import get_ready_assets, lock_workspace_limits
from .skill_protocol import load_skill_protocol

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/replica", tags=["one-click-replica"]
)
SessionDependency = Annotated[Session, Depends(get_session)]
SKILL = "reference-visual-reverse-remix"
logger = logging.getLogger(__name__)


class ProductRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    platform: ProductPlatform | Literal["auto"] = "auto"


class ImportRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)


class ReferenceChoice(BaseModel):
    asset_id: str = Field(min_length=1, max_length=40)
    title: str = Field(default="参考图", max_length=80)
    aspect_ratio: Literal[
        "follow-reference", "1:1", "3:4", "4:3", "4:5", "9:16", "16:9"
    ] = "follow-reference"


class AnalysisRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=80)
    product_asset_ids: list[str] = Field(min_length=1, max_length=7)
    references: list[ReferenceChoice] = Field(min_length=1, max_length=20)
    platform_code: str = Field(default="tmall", min_length=1, max_length=32)
    language: str = Field(default="zh-CN", min_length=2, max_length=16)
    facts: str = Field(default="", max_length=4000)
    replication_method: Literal["exact", "style"] | None = None
    auto_start_after_plan: bool = False

    @model_validator(mode="after")
    def validate_roles(self) -> AnalysisRequest:
        if (
            (
                self.replication_method is not None
                and len({r.asset_id for r in self.references}) != len(self.references)
            )
            or len(set(self.product_asset_ids)) != len(self.product_asset_ids)
            or set(self.product_asset_ids) & {r.asset_id for r in self.references}
        ):
            raise ValueError("产品图和风格参考图需要分别上传，不能使用同一素材。")
        return self


class ConfirmRequest(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64)
    instructions: list[str] = Field(default_factory=list, max_length=20)


class ReplicaCreationRecordResponse(BaseModel):
    id: str
    status: str
    created_at: datetime
    updated_at: datetime
    method: Literal["exact", "style"] | None
    job_ids: list[str]
    current_job_ids: list[str]
    progress: int
    stage: str | None
    expected: int
    generated: int


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _wire(row: ReplicaDocumentRecord) -> dict:
    value: dict[str, Any] = {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "payload": {
            key: value
            for key, value in row.payload.items()
            if key
            not in {
                "native_snapshot",
                "native_executions",
                "_automatic_submission",
                "_exact_model_protocol",
                "_analysis_checkpoint",
                "_analysis_input_hash",
                "_analysis_revision",
                "_exact_plan_revision",
                "_analysis_lineage",
                "_segmentation_parent",
                "_segmentation_revision",
                "_style_revision_requested",
                "_style_analysis_revision",
                "_style_crop_policy_adoption",
                "_completion_requested",
                "_product_provider_state",
                "_product_fetch_attempt",
            }
        },
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
    from .replica_analysis_recovery import (
        segmentation_diagnostics,
        segmentation_repair_available,
    )

    repair = segmentation_repair_available(row, _analysis_input_hash(row.payload))
    value["payload"]["segmentation_repair_available"] = repair
    value["payload"]["segmentation_restart_available"] = bool(
        row.status == "failed"
        and row.payload.get("error_code") == "REPLICA_SEGMENTATION_INVALID"
        and not row.payload.get("job_ids")
        and not row.payload.get("_analysis_checkpoint")
    )
    diagnostic = segmentation_diagnostics(row.payload)
    if diagnostic:
        value["payload"]["segmentation_diagnostics"] = diagnostic
    value["payload"]["analysis_resume_available"] = (
        bool(row.payload.get("_analysis_checkpoint"))
        and row.status == "failed"
        and not row.payload.get("job_ids")
        and row.payload.get("error_code")
        not in {ReplicaPlanAuditRejected.code, "REPLICA_SEGMENTATION_INVALID"}
    )
    value["payload"]["analysis_revision_available"] = _revision_available(row)
    value["payload"]["execution_resume_available"] = (
        row.status in {"failed", "partial"}
        and (
            row.payload.get("replication_method") == "style"
            or (
                row.payload.get("replication_method") == "exact"
                and row.payload.get("native_snapshot", {})
                .get("model_protocol", {})
                .get("version")
                in {
                    "exact-compact-v3",
                    "exact-compact-v4",
                    "exact-compact-v5",
                    "exact-compact-v6",
                    "exact-compact-v7",
                    "exact-compact-v8",
                    "exact-compact-v9",
                    "exact-compact-v10",
                    "exact-compact-v11",
                    "exact-compact-v12",
                    "exact-compact-v13",
                    "exact-compact-v14",
                    "exact-compact-v15",
                }
            )
        )
        and bool(row.payload.get("native_executions"))
        and row.payload.get("error_code") != "PROVIDER_OUTCOME_UNKNOWN"
    )
    if (
        row.status == "failed"
        and row.payload.get("error_code") == ReplicaPlanAuditRejected.code
    ):
        value["payload"]["error"] = (
            "部分方案仍未通过校验，已完成的规划和素材已保留。"
            "请先调整未通过的方案，再继续处理。"
        )
    if (
        row.status == "failed"
        and row.payload.get("error_code") == "REPLICA_ANALYSIS_BUDGET_REACHED"
        and row.payload.get("job_ids")
    ):
        value["payload"]["error"] = (
            "本次处理已中断，已完成的图片和审图记录已保留。可继续处理剩余步骤。"
        )
    if row.payload.get("auto_start_after_plan") is True:
        value["payload"]["plans"] = [
            {"reference": plan["reference"], "analysis": {}, "prompt": ""}
            for plan in row.payload.get("plans", [])
        ]
    return value


def _revision_available(row: ReplicaDocumentRecord) -> bool:
    from .replica_analysis_recovery import (
        exact_revision_available,
        style_revision_available,
    )

    if style_revision_available(
        row, _analysis_input_hash(row.payload)
    ) or exact_revision_available(row, _analysis_input_hash(row.payload)):
        return True
    from .replica_exact_revision import V2_BINDING

    return (
        row.status == "failed"
        and not row.payload.get("job_ids")
        and row.payload.get("error_code") == ReplicaPlanAuditRejected.code
        and row.payload.get("_exact_model_protocol") == V2_BINDING["version"]
        and row.payload.get("_analysis_checkpoint", {}).get("model_protocol")
        == V2_BINDING
        and row.payload.get("_analysis_input_hash") == _analysis_input_hash(row.payload)
    )


def _analysis_input_hash(payload: dict[str, Any], protocol: str | None = None) -> str:
    values = {
        key: payload.get(key)
        for key in (
            "product_asset_ids",
            "references",
            "facts",
            "language",
            "platform_code",
            "replication_method",
            "_exact_model_protocol",
        )
    }
    if protocol is not None:
        values["_exact_model_protocol"] = protocol
    return _hash(values)


def _get(
    session: Session, workspace_id: str, document_id: str, kind: str | None = None
) -> ReplicaDocumentRecord:
    row = session.scalar(
        select(ReplicaDocumentRecord)
        .where(
            ReplicaDocumentRecord.id == document_id,
            ReplicaDocumentRecord.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if row is None or (kind and row.kind != kind):
        raise ApiError(404, "REPLICA_NOT_FOUND", "未找到该复刻任务。")
    return row


def _claim(
    session: Session,
    workspace: WorkspaceContext,
    kind: str,
    fingerprint: str,
    payload: dict,
    status: str,
) -> tuple[ReplicaDocumentRecord, bool]:
    # Serialize claims before counting calls or retrying a failed import.
    session.get(WorkspaceRecord, workspace.workspace_id, with_for_update=True)
    query = select(ReplicaDocumentRecord).where(
        ReplicaDocumentRecord.workspace_id == workspace.workspace_id,
        ReplicaDocumentRecord.kind == kind,
        ReplicaDocumentRecord.fingerprint == fingerprint,
    )
    row = session.scalar(query)
    if row:
        return row, False
    active = session.scalars(
        select(ReplicaDocumentRecord.id).where(
            ReplicaDocumentRecord.workspace_id == workspace.workspace_id,
            ReplicaDocumentRecord.status.in_(["fetching", "analysing"]),
        )
    ).all()
    if len(active) >= 3:
        raise ApiError(429, "REPLICA_BUSY", "已有任务在处理中，请稍后再试。")
    row = ReplicaDocumentRecord(
        id="rep_" + uuid4().hex,
        workspace_id=workspace.workspace_id,
        created_by=workspace.principal.user_id,
        kind=kind,
        fingerprint=fingerprint,
        payload=payload,
        status=status,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        row = session.scalar(query)
        if not row:
            raise
        return row, False
    return row, True


def _save(app: Any, document_id: str, status: str, payload: dict) -> None:
    with app.state.session_factory() as session:
        row = session.get(ReplicaDocumentRecord, document_id)
        if row is None:
            return
        if (
            payload.get("_product_fetch_attempt")
            and row.payload.get("_product_fetch_attempt")
            != payload["_product_fetch_attempt"]
        ):
            return
        if (
            payload.get("analysis_attempt_id")
            and row.payload.get("analysis_attempt_id") != payload["analysis_attempt_id"]
        ):
            return
        row.status, row.payload, row.updated_at = status, payload, utc_now()
        session.commit()


async def _fetch(app: Any, document_id: str) -> None:
    with app.state.session_factory() as session:
        row = session.get(ReplicaDocumentRecord, document_id)
        payload = dict(row.payload)
    try:
        token = app.state.settings.everyinfra_api_key.get_secret_value()

        def checkpoint(state: dict) -> None:
            with app.state.session_factory() as session:
                current = session.get(
                    ReplicaDocumentRecord, document_id, with_for_update=True
                )
                if (
                    current is None
                    or current.status != "fetching"
                    or current.payload.get("_product_fetch_attempt")
                    != payload.get("_product_fetch_attempt")
                ):
                    raise ApiError(
                        409,
                        "PRODUCT_FETCH_LEASE_LOST",
                        "商品采集已由其他请求继续处理。",
                    )
                payload["_product_provider_state"] = state
                current.payload = dict(payload)
                current.updated_at = utc_now()
                session.commit()

        product = await asyncio.to_thread(
            fetch_product,
            token,
            payload["platform"],
            payload["item_id"],
            payload["canonical_url"],
            state=payload.get("_product_provider_state", {}),
            on_checkpoint=checkpoint,
        )
        _save(app, document_id, "ready", {**payload, "product": product})
    except Exception as error:
        _save(
            app,
            document_id,
            "failed",
            {
                **payload,
                "error_code": getattr(error, "code", "PRODUCT_FETCH_FAILED"),
                "error": error.message
                if isinstance(error, ApiError)
                else "商品获取失败，请稍后重试或上传参考图。",
            },
        )


@router.get("/product-platforms")
def product_platforms(workspace: WorkspaceDependency) -> list[dict[str, str]]:
    return [{"code": code, "label": source.label} for code, source in SOURCES.items()]


def _resume_product_fetch(
    row: ReplicaDocumentRecord,
    session: Any,
    app: Any,
    background_tasks: BackgroundTasks,
    *,
    explicit: bool = False,
) -> None:
    updated = (
        row.updated_at.replace(tzinfo=UTC)
        if row.updated_at.tzinfo is None
        else row.updated_at
    )
    stale = (utc_now() - updated).total_seconds() > 300
    if not (
        (explicit and row.status == "failed") or (row.status == "fetching" and stale)
    ):
        return
    row.status = "fetching"
    row.payload = {
        key: value
        for key, value in row.payload.items()
        if key not in {"error", "error_code", "analysis_error_details"}
    }
    row.payload = {**row.payload, "_product_fetch_attempt": uuid4().hex}
    row.updated_at = utc_now()
    session.commit()
    background_tasks.add_task(_fetch, app, row.id)


@router.post("/products", status_code=202)
async def create_product(
    payload: ProductRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> dict:
    platform, item_id, canonical = await asyncio.to_thread(
        parse_product_link, payload.url, payload.platform
    )
    row, created = _claim(
        session,
        workspace,
        "product",
        _hash(
            [
                "everyinfra-v1",
                platform,
                item_id,
                canonical,
                (datetime.now(UTC) + timedelta(hours=8)).date().isoformat(),
            ]
        ),
        {
            "platform": platform,
            "item_id": item_id,
            "canonical_url": canonical,
            "_product_fetch_attempt": uuid4().hex,
        },
        "fetching",
    )
    if created:
        background_tasks.add_task(_fetch, request.app, row.id)
    else:
        _resume_product_fetch(
            row, session, request.app, background_tasks, explicit=True
        )
    return _wire(row)


@router.get("/documents/{document_id}")
def get_document(
    document_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceDependency,
    session: SessionDependency,
) -> dict:
    row = _get(session, workspace.workspace_id, document_id)
    if row.kind == "product" and row.payload.get("_product_fetch_attempt"):
        _resume_product_fetch(row, session, request.app, background_tasks)
    if reconcile_document(session, row):
        session.commit()
    from .replica_native_plan import snapshot_completion_ready

    if (
        row.status == "failed"
        and (snapshot_completion_ready(row.payload.get("_analysis_checkpoint", {})))
        and row.payload.get("auto_start_after_plan") is True
        and not row.payload.get("job_ids")
        and row.payload.get("_analysis_checkpoint")
        and row.payload.get("error_code")
        in {
            "REPLICA_PLAN_AUDIT_REJECTED",
            "REPLICA_STYLE_EVIDENCE_INVALID",
            "REPLICA_MODEL_RESPONSE_INVALID",
            "REPLICA_SEGMENTATION_INVALID",
            "REPLICA_ANALYSIS_UNAVAILABLE",
        }
    ):
        # Old automatic submissions adopt the same completion behavior when
        # reopened. No button or fresh analysis window is required.
        values = dict(row.payload)
        values["_completion_requested"] = values["error_code"]
        values.pop("error", None)
        values.pop("error_code", None)
        values["analysis_attempt_id"] = uuid4().hex
        values["analysis_stage"] = "正在按现有方案准备生成"
        row.status, row.payload, row.updated_at = "analysing", values, utc_now()
        session.commit()
        background_tasks.add_task(
            _analyse, request.app, row.id, values["analysis_attempt_id"]
        )
    since = (
        row.updated_at.replace(tzinfo=UTC)
        if row.updated_at.tzinfo is None
        else row.updated_at
    )
    if row.status in {"fetching", "analysing"} and utc_now() - since > timedelta(
        minutes=20
    ):
        row.status = "failed"
        row.payload = {
            **row.payload,
            "error": "任务处理已中断，请重新发起。已生成的图片可在生成记录查看。",
        }
        session.commit()
    if (
        row.status == "awaiting_confirmation"
        and row.payload.get("auto_start_after_plan") is True
        and not row.payload.get("automatic_submission_error")
    ):
        from .replica_auto_start import submit_automatic

        attempt_id = row.payload.get("analysis_attempt_id")
        document_id = row.id
        session.commit()
        if isinstance(attempt_id, str):
            background_tasks.add_task(
                submit_automatic, request.app, document_id, attempt_id
            )
    result = _wire(row)
    # A poll must release its row lock before response/dependency cleanup awaits
    # the event loop. Background checkpoints may need to update the same row.
    session.commit()
    return result


@router.get("/sessions")
def history(workspace: WorkspaceDependency, session: SessionDependency) -> list[dict]:
    rows = session.scalars(
        select(ReplicaDocumentRecord)
        .where(
            ReplicaDocumentRecord.workspace_id == workspace.workspace_id,
            ReplicaDocumentRecord.kind == "session",
        )
        .order_by(ReplicaDocumentRecord.created_at.desc())
        .limit(30)
    ).all()
    changed = [reconcile_document(session, row) for row in rows]
    if any(changed):
        session.commit()
    return [_wire(row) for row in rows]


@router.get("/records", response_model=list[ReplicaCreationRecordResponse])
def creation_records(
    workspace: WorkspaceDependency, session: SessionDependency
) -> list[dict]:
    """Lightweight durable records for the site-wide history and task centre.

    Select only display metadata, never the large private model checkpoints.
    Existing sessions appear immediately without copying or creating paid jobs.
    """
    from .replica_records import creation_rows

    rows = creation_rows(session, workspace.workspace_id)
    linked_ids = [jid for r in rows for jid in (r["job_ids"] or [])]
    jobs = (
        {
            j.id: j
            for j in session.execute(
                select(
                    JobRecord.id,
                    JobRecord.status,
                    JobRecord.progress,
                    JobRecord.expected_item_count,
                    JobRecord.succeeded_item_count,
                ).where(
                    JobRecord.workspace_id == workspace.workspace_id,
                    JobRecord.id.in_(linked_ids),
                )
            ).all()
        }
        if linked_ids
        else {}
    )
    result = [
        {
            "id": r["id"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
            "method": r["method"],
            "job_ids": r["job_ids"] or [],
            "current_job_ids": r["job_ids"] or [],
            "progress": r["progress"] or 0,
            "stage": r["stage"],
            "expected": (r["execution"] or {}).get("total")
            or (r["analysis"] or {}).get("total")
            or len(r["references"] or []),
            "generated": (r["execution"] or {}).get("generated", 0),
        }
        for r in rows
    ]
    for record, source in zip(result, rows, strict=True):
        linked = [jobs[jid] for jid in record["job_ids"] if jid in jobs]
        if linked:
            states = {j.status for j in linked}
            if states == {"completed"}:
                record["status"] = "completed"
            elif states == {"cancelled"}:
                record["status"] = "cancelled"
            elif states <= {"failed", "cancelled", "partial", "completed"}:
                record["status"] = (
                    "partial" if states & {"partial", "completed"} else "failed"
                )
            else:
                record["status"] = "generating"
            record["progress"] = min(j.progress for j in linked)
            record["expected"] = sum(j.expected_item_count for j in linked)
            record["generated"] = max(
                record["generated"], sum(j.succeeded_item_count for j in linked)
            )
        record["job_ids"] = list(
            dict.fromkeys(
                [
                    *record["job_ids"],
                    *(
                        event[key]
                        for event in (source["retry_events"] or [])
                        for key in ("from", "to")
                        if event.get(key)
                    ),
                ]
            )
        )
    return result


@router.get("/jobs/{job_id}/session")
def session_for_job(
    job_id: str, workspace: WorkspaceDependency, session: SessionDependency
) -> dict:
    job = session.scalar(
        select(JobRecord).where(
            JobRecord.id == job_id, JobRecord.workspace_id == workspace.workspace_id
        )
    )
    document_id = job.configuration.get("replica_session_id") if job else None
    if not isinstance(document_id, str):
        raise ApiError(404, "REPLICA_JOB_NOT_FOUND", "未找到当前工作区的复刻任务。")
    row = _get(session, workspace.workspace_id, document_id, "session")
    return _wire(row)


class ImageRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        if not image_url(newurl):
            raise ApiError(400, "IMAGE_REDIRECT_REJECTED", "图片重定向地址不受支持。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str, max_bytes: int) -> bytes:
    if not image_url(url):
        raise ApiError(400, "IMAGE_URL_REJECTED", "图片地址不受支持。")
    try:
        with build_opener(ProxyHandler({}), ImageRedirects()).open(
            UrlRequest(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30
        ) as response:
            payload = response.read(max_bytes + 1)
    except Exception:
        raise ApiError(
            502, "IMAGE_DOWNLOAD_FAILED", "该图片暂时无法导入，请取消选择后重试。"
        ) from None
    if len(payload) > max_bytes:
        raise ApiError(413, "IMAGE_TOO_LARGE", "图片过大，请选择其他参考图。")
    return payload


@router.post("/products/{document_id}/import")
async def import_images(
    document_id: str,
    payload: ImportRequest,
    request: Request,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> list[dict]:
    row = _get(session, workspace.workspace_id, document_id, "product")
    product = row.payload.get("product", {})
    allowed = set(
        product.get("imageUrls", [])
        + product.get("descImageUrls", [])
        + product.get("skuImageUrls", [])
    )
    if row.status != "ready" or not set(payload.urls) <= allowed:
        raise ApiError(400, "REFERENCE_SELECTION_INVALID", "请选择本次商品返回的图片。")
    session.rollback()
    from .routes_v1 import upload_asset

    results = []
    for url in dict.fromkeys(payload.urls):
        data = await asyncio.to_thread(
            _download, url, request.app.state.settings.max_upload_bytes
        )
        asset = await upload_asset(
            request=request,
            workspace=workspace,
            file=UploadFile(filename="reference.jpg", file=BytesIO(data)),
            origin_kind=AssetOriginKind.IMPORTED,
            media_kind=AssetMediaKind.IMAGE,
            idempotency_header="replica-import-" + _hash([document_id, url]),
        )
        role = (
            "主图"
            if url in product.get("imageUrls", [])
            else "详情图"
            if url in product.get("descImageUrls", [])
            else "SKU图"
        )
        results.append({"url": url, "asset_id": asset.id, "role": role})
    return results


def _analysis_current(app: Any, document_id: str, attempt_id: str) -> bool:
    with app.state.session_factory() as session:
        row = session.get(ReplicaDocumentRecord, document_id)
        return (
            row is not None
            and row.status == "analysing"
            and row.payload.get("analysis_attempt_id") == attempt_id
        )


def _touch_analysis_heartbeat(app: Any, document_id: str, attempt_id: str) -> bool:
    with app.state.session_factory() as session:
        if session.get_bind().dialect.name == "postgresql":
            # Monitoring must yield to real work and release its connection.
            session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        touched = session.scalar(
            update(ReplicaDocumentRecord)
            .where(
                ReplicaDocumentRecord.id == document_id,
                ReplicaDocumentRecord.status == "analysing",
                ReplicaDocumentRecord.payload["analysis_attempt_id"].as_string()
                == attempt_id,
            )
            .values(updated_at=utc_now())
            .returning(ReplicaDocumentRecord.id)
        )
        session.commit()
        return touched is not None


async def _analysis_heartbeat(
    app: Any, document_id: str, attempt_id: str, *, interval: float = 30
) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            current = await asyncio.to_thread(
                _touch_analysis_heartbeat, app, document_id, attempt_id
            )
        except SQLAlchemyError:
            logger.warning("Replica analysis heartbeat deferred: %s", document_id)
            continue
        if not current:
            return


async def _analyse(app: Any, document_id: str, attempt_id: str) -> None:
    slots = getattr(app.state, "replica_analysis_slots", None)
    if slots is None and app.state.settings.replica_analysis_concurrency:
        slots = app.state.replica_analysis_slots = asyncio.Semaphore(
            app.state.settings.replica_analysis_concurrency
        )
    from contextlib import nullcontext

    from .replica_automatic_recovery import MAX_RECOVERIES, claim_analysis_recovery

    async with slots if slots is not None else nullcontext():
        for _ in range(MAX_RECOVERIES + 1):
            if not _analysis_current(app, document_id, attempt_id):
                return
            pulse = asyncio.create_task(
                _analysis_heartbeat(app, document_id, attempt_id)
            )
            try:
                await _analyse_document(app, document_id, attempt_id)
            finally:
                pulse.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await pulse
            try:
                recovered = claim_analysis_recovery(app, document_id, attempt_id)
            except Exception:
                # A recovery admission failure must not overwrite the original
                # failure, weaken its gate, or dispatch an unbound attempt.
                logger.exception("Replica automatic recovery admission failed")
                recovered = None
            if recovered:
                attempt_id = recovered
                continue
            from .replica_auto_start import submit_automatic

            await submit_automatic(app, document_id, attempt_id)
            return


async def _analyse_document(app: Any, document_id: str, attempt_id: str) -> None:
    from .xinghe_agent import PlannerRequest, SourceImage, build_tengli_gateway

    with app.state.session_factory() as session:
        row = session.get(ReplicaDocumentRecord, document_id)
        workspace_id, payload = row.workspace_id, dict(row.payload)
    plans = list(payload.get("plans", []))
    run = None
    try:
        with app.state.session_factory() as session:
            assets = get_ready_assets(
                session,
                workspace_id,
                payload["product_asset_ids"]
                + [r["asset_id"] for r in payload["references"]],
            )
            records = {
                key: (
                    value.original_filename,
                    value.content_type,
                    value.sha256,
                    value.object_key,
                    value.width,
                    value.height,
                )
                for key, value in assets.items()
            }
        gateway = getattr(app.state, "replica_gateway", None) or build_tengli_gateway(
            app.state.settings
        )
        if payload.get("replication_method") in SKILLS:
            run_type = NativeReplicaRun
            if payload.get("replication_method") == "style":
                from .replica_style_analysis import DurableStyleAnalysisRun

                run_type = DurableStyleAnalysisRun
            if payload.get("_exact_model_protocol"):
                from .replica_exact_compact import VERSION as compact_version
                from .replica_exact_compact import run_class

                run_type = run_class(
                    payload["replication_method"], payload["_exact_model_protocol"]
                )
            input_hash = _analysis_input_hash(payload)
            revision = payload.get("_analysis_revision") or {}
            parent = payload.get("_analysis_checkpoint") or {}
            revise = (
                revision.get("parent_snapshot_sha256") == _hash(parent)
                and revision.get("parent_protocol") == parent.get("model_protocol")
                and payload.get("_analysis_input_hash")
                == _analysis_input_hash(payload, "exact-compact-v2")
                and payload.get("_exact_model_protocol") == compact_version
            )
            if (
                revision
                and not revise
                and payload.get("_analysis_input_hash") != input_hash
            ):
                raise ReplicaNativeError(
                    "方案修正的原始输入或检查点已变化，已停止以保留原进度。"
                )
            checkpoint = (
                payload.get("_analysis_checkpoint")
                if revise or payload.get("_analysis_input_hash") == input_hash
                else None
            )
            run = run_type(
                payload["replication_method"],
                document_id,
                workspace_id,
                gateway,
                app.state.storage,
                **({"root": Path(checkpoint["root"])} if checkpoint else {}),
            )
            if payload["replication_method"] == "style":
                style_run: Any = run
                style_run.crop_policy_adoption = payload.get(
                    "_style_crop_policy_adoption"
                )
            if checkpoint:
                if revise:
                    revision_run: Any = run
                    await revision_run.restore_for_revision(checkpoint)
                else:
                    await run.restore(checkpoint)
            else:
                run.root = run.root / payload["analysis_attempt_id"]
                run.root.mkdir(parents=True, exist_ok=True)
                run.run = run.root

            from .replica_analysis_recovery import configure_run

            configure_run(run, payload, checkpoint)

            async def checkpoint_updated() -> None:
                if not _analysis_current(app, document_id, attempt_id):
                    raise ReplicaNativeError("Analysis attempt is no longer current")
                payload.update(
                    _analysis_checkpoint=await run.snapshot(),
                    _analysis_input_hash=input_hash,
                    analysis_progress=run.context.get("analysis_progress", {}),
                    analysis_accounting=native_accounting(run.usage),
                    **(
                        {
                            "segmentation_diagnostics": run.context[
                                "segmentation_diagnostics"
                            ]
                        }
                        if run.context.get("segmentation_diagnostics")
                        else {}
                    ),
                )
                _save(app, document_id, "analysing", dict(payload))

            def progress_updated(progress: int, label: str) -> None:
                # Replanning is additional work in the same attempt, not a reset.
                payload.update(
                    progress=max(int(payload.get("progress", 0)), progress),
                    analysis_stage=label,
                )
                _save(app, document_id, "analysing", dict(payload))

            run.on_progress = progress_updated
            if hasattr(run, "on_checkpoint"):
                run.on_checkpoint = checkpoint_updated
            try:
                if payload.get("_completion_requested"):
                    from .replica_completion import CompleteExistingAnalysis

                    raise CompleteExistingAnalysis(payload["_completion_requested"])
                plans = await run.prepare(payload, records)
            except ReplicaNativeError as error:
                from .replica_native_plan import completion_ready

                native_state = (
                    read_json(run.root / "session.json")
                    if (run.root / "session.json").exists()
                    else {}
                )
                if (
                    payload.get("auto_start_after_plan") is not True
                    or isinstance(error, ReplicaInputTooLarge)
                    or not completion_ready(run, native_state)
                ):
                    # A segmentation/observation failure is not three failed
                    # complete plans. Keep its actual stage and checkpoint.
                    raise
                from .replica_completion import prepare as prepare_completion

                # The original bounded analysis and real FAIL remain evidence.
                # Completion is a separate application policy, never an audit PASS.
                failed_snapshot = await run.snapshot()
                plans = await prepare_completion(run, payload, records, error)
                payload["_analysis_lineage"] = [
                    *payload.get("_analysis_lineage", []),
                    failed_snapshot,
                ]
                payload["planning_status"] = "BEST_EFFORT"
                payload["analysis_stage"] = "方案修正已结束，正在准备生成"
            snapshot = await run.snapshot()
            binding = {
                "replication_method": run.method,
                "skill_id": run.skill_id,
                "skill_content_hash": run.source_hash,
                "plans": plans,
                "native_snapshot_hash": _hash(snapshot),
            }
            _save(
                app,
                document_id,
                "awaiting_confirmation",
                {
                    **payload,
                    **binding,
                    "plan_hash": _hash(binding),
                    "native_snapshot": snapshot,
                    "progress": 100,
                    "analysis_cost_micros": native_accounting(run.usage)[
                        "actual_cost_micros"
                    ],
                },
            )
            return
        protocol = load_skill_protocol(
            SKILL, source_content_hash=BundledSkillManifestValidator().validate(SKILL)
        )
        product_bytes = {
            key: await app.state.storage.read_bytes(records[key][3])
            for key in payload["product_asset_ids"]
        }
        for index, choice in enumerate(payload["references"]):
            if index < len(plans):
                continue
            if not _analysis_current(app, document_id, attempt_id):
                return
            reference_id = choice["asset_id"]
            source_bytes = {
                **product_bytes,
                reference_id: await app.state.storage.read_bytes(
                    records[reference_id][3]
                ),
            }
            sources = tuple(
                SourceImage(
                    asset_id=key,
                    group_key="replica",
                    filename=records[key][0],
                    content_type=records[key][1],
                    sha256=records[key][2],
                    payload=source_bytes[key],
                    source_role=role,
                )
                for role, keys in (
                    ("product_reference", payload["product_asset_ids"]),
                    ("style_reference", [choice["asset_id"]]),
                )
                for key in keys
            )
            canvas = TARGET_DIMENSIONS.get(choice["aspect_ratio"]) or (
                records[reference_id][4] or 1024,
                records[reference_id][5] or 1024,
            )
            result = await gateway.plan(
                PlannerRequest(
                    workspace_id=workspace_id,
                    job_id=document_id,
                    group_key=f"replica-{index}",
                    sources=sources,
                    page_count=1,
                    language=payload["language"],
                    product_name="用户产品图中的商品",
                    product_information=(
                        payload["facts"],
                        "需要生成的图片类型：" + choice["title"],
                        "目标画幅比例：" + choice["aspect_ratio"],
                        "方案说明使用简体中文；新画面文字使用目标语言。",
                        "产品图是唯一身份依据；参考图仅用于构图、光影和排版。",
                        "所有新创作的画面文案语言必须为："
                        + payload["language"]
                        + "；保留产品图中原本的品牌与标签文字。",
                    ),
                    platform_code=payload["platform_code"],
                    size=f"{canvas[0]}x{canvas[1]}",
                    idempotency_key=f"{document_id}:analysis:{index}",
                    planning_profile="reference_visual_remix",
                    system_prompt=(
                        "Follow the verified Skill. The product_reference images "
                        "identify the user's product. The final style_reference "
                        "image supplies visual strategy only. "
                        "Return original_remix_prompt "
                        "in the target language, within 3000 characters.\n"
                    )
                    + json.dumps(protocol.prompt_payload(), ensure_ascii=False),
                )
            )
            analysis = result.payload
            if (
                result.error_code
                or not isinstance(analysis, dict)
                or not isinstance(analysis.get("original_remix_prompt"), str)
                or not analysis["original_remix_prompt"].strip()
                or len(analysis["original_remix_prompt"]) > 4000
                or not {
                    "product_identity_lock",
                    "reference_visual_analysis",
                    "hard_composition_skeleton",
                    "commercial_intent",
                }
                <= analysis.keys()
            ):
                raise ApiError(
                    502,
                    "REPLICA_ANALYSIS_FAILED",
                    "图片分析暂时未完成，请稍后重新分析。",
                )
            plans.append(
                {
                    "reference": choice,
                    "analysis": analysis,
                    "prompt": analysis["original_remix_prompt"],
                    "usage": result.normalized_usage,
                    "actual_cost_micros": result.actual_cost_micros,
                }
            )
            payload = {
                **payload,
                "plans": plans,
                "progress": round(len(plans) / len(payload["references"]) * 100),
            }
            _save(app, document_id, "analysing", payload)
        payload["plan_hash"] = _hash(plans)
        _save(app, document_id, "awaiting_confirmation", payload)
    except Exception as error:
        save_checkpoint = getattr(run, "checkpoint", None)
        if callable(save_checkpoint):
            # Preserve the primary failure if checkpoint storage is unavailable.
            with suppress(Exception):
                await save_checkpoint()
        analysis_failure = {}
        calls = getattr(run, "usage", [])
        if (
            isinstance(error, ReplicaAnalysisUnavailable)
            and calls
            and calls[-1].get("error_code") == "PLANNER_SCHEMA_INVALID"
        ):
            last = calls[-1]
            analysis_failure = {
                "error_code": "REPLICA_MODEL_RESPONSE_INVALID",
                "error": (
                    "分析模型未返回完整 JSON 对象，无法核验原方案；"
                    "已有分析、素材与具体原因已保留。"
                ),
                "analysis_error_details": {
                    key: last.get(key)
                    for key in (
                        "provider_request_id",
                        "http_status",
                        "response_format_type",
                        "output_characters",
                        "parse_trace",
                    )
                },
            }
        if (
            isinstance(error, ReplicaAnalysisUnavailable)
            and error.code == "REPLICA_MODEL_RESPONSE_INVALID"
        ):
            from .provider_diagnostics import safe_text

            detail = safe_text(str(error), 1000)
            analysis_failure = {
                "error": error.message + " 具体原因：" + detail,
                "analysis_error_details": {
                    "code": error.code,
                    "message": detail,
                    "stage": payload.get("analysis_stage"),
                },
            }
        if (
            isinstance(error, ReplicaAnalysisUnavailable)
            and calls
            and calls[-1].get("error_code") == "PLANNER_OUTPUT_TRUNCATED"
        ):
            last = calls[-1]
            limit = last.get("output_token_limit")
            analysis_failure = {
                "error_code": "REPLICA_PLANNER_OUTPUT_TRUNCATED",
                "error": (
                    f"分析输出被截断（finish_reason=length，输出额度{limit} token）；"
                    "详细方案尚未完成，已停止生图。已有分析与素材已保留。"
                ),
                "analysis_error_details": {
                    key: last.get(key)
                    for key in (
                        "provider_request_id",
                        "http_status",
                        "finish_reason",
                        "output_token_limit",
                        "input_tokens",
                        "output_tokens",
                        "response_format_type",
                        "output_characters",
                        "output_non_whitespace_characters",
                        "trailing_whitespace_characters",
                    )
                },
            }
        _save(
            app,
            document_id,
            "failed",
            {
                **payload,
                "plans": plans,
                "error_code": getattr(error, "code", "REPLICA_ANALYSIS_FAILED"),
                "error": error.message
                if isinstance(
                    error,
                    (
                        ApiError,
                        ReplicaSegmentationInvalid,
                        ReplicaInputTooLarge,
                        ReplicaAnalysisUnavailable,
                        ReplicaPlanAuditRejected,
                        StyleEvidenceInvalid,
                    ),
                )
                else "分析服务暂时不可用，请稍后再试。",
                **analysis_failure,
            },
        )


@router.post("/sessions", status_code=202)
def analyse(
    payload: AnalysisRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> dict:
    if not request.app.state.settings.reference_remix_provider_enabled:
        raise ApiError(503, "REPLICA_DISABLED", "复刻生成服务暂未开放，请稍后再试。")
    values = payload.model_dump(mode="json")
    if payload.replication_method == "exact":
        from .replica_exact_compact import VERSION

        values["_exact_model_protocol"] = VERSION
    if payload.auto_start_after_plan:
        values["_automatic_submission"] = {
            "version": "replica-submission-v1",
            "actor_user_id": workspace.principal.user_id,
            "email_verified": workspace.principal.email_verified,
        }
    get_ready_assets(
        session,
        workspace.workspace_id,
        payload.product_asset_ids + [r.asset_id for r in payload.references],
    )
    row, created = _claim(
        session,
        workspace,
        "session",
        _hash(values),
        {**values, "plans": [], "job_ids": [], "analysis_attempt_id": uuid4().hex},
        "analysing",
    )
    if created:
        background_tasks.add_task(
            _analyse, request.app, row.id, row.payload["analysis_attempt_id"]
        )
    return _wire(row)


@router.post("/sessions/{document_id}/retry", status_code=202)
def retry_analysis(
    document_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> dict:
    row = _get(session, workspace.workspace_id, document_id, "session")
    if row.status == "analysing":
        return _wire(row)
    if row.status != "failed" or row.payload.get("job_ids"):
        raise ApiError(409, "REPLICA_RETRY_UNAVAILABLE", "当前任务无需重新分析。")
    if row.payload.get("error_code") == ReplicaPlanAuditRejected.code:
        raise ApiError(
            409,
            "REPLICA_PLAN_REVISION_REQUIRED",
            "部分方案仍未通过校验，已完成的规划和素材已保留。请先调整未通过的方案。",
        )
    if row.payload.get("replication_method") == "style" and row.payload.get(
        "_analysis_checkpoint"
    ):
        from .replica_style_analysis import crop_policy_upgrade, policy

        checkpoint = row.payload["_analysis_checkpoint"]
        if checkpoint.get("analysis_policy") != policy():
            authority = crop_policy_upgrade(
                row.payload, _analysis_input_hash(row.payload)
            )
            if authority is None:
                raise ApiError(
                    409,
                    "REPLICA_RETRY_UNAVAILABLE",
                    "原裁图检查点无法安全升级，原记录已保留。",
                )
            row.payload = {
                **row.payload,
                "_style_crop_policy_adoption": authority,
                "_analysis_lineage": [
                    *row.payload.get("_analysis_lineage", []),
                    *(
                        [checkpoint]
                        if checkpoint not in row.payload.get("_analysis_lineage", [])
                        else []
                    ),
                ],
            }
    from .replica_analysis_recovery import (
        prepare_segmentation_retry,
        segmentation_repair_available,
    )

    if row.payload.get(
        "error_code"
    ) == "REPLICA_SEGMENTATION_INVALID" and row.payload.get("_analysis_checkpoint"):
        if not segmentation_repair_available(row, _analysis_input_hash(row.payload)):
            raise ApiError(
                409,
                "REPLICA_SEGMENTATION_REPAIR_UNAVAILABLE",
                "原拆屏检查点无法安全恢复，请保留原记录并重新诊断。",
            )
        row.payload = prepare_segmentation_retry(row.payload)
    row.status = "analysing"
    row.updated_at = utc_now()
    row.payload = {
        key: value
        for key, value in row.payload.items()
        if key not in {"error", "error_code", "analysis_error_details"}
    }
    row.payload = {
        **row.payload,
        "analysis_attempt_id": uuid4().hex,
        "progress": row.payload.get("progress", 0)
        if row.payload.get("_analysis_checkpoint")
        else 0,
    }
    session.commit()
    background_tasks.add_task(
        _analyse, request.app, row.id, row.payload["analysis_attempt_id"]
    )
    return _wire(row)


@router.post("/sessions/{document_id}/revise", status_code=202)
def revise_analysis(
    document_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> dict:
    """Explicitly adopt corrected rules while retaining verifiable earlier work."""
    from .replica_exact_compact import VERSION

    row = _get(session, workspace.workspace_id, document_id, "session")
    if row.status == "analysing" and (
        row.payload.get("_analysis_revision")
        or row.payload.get("_exact_plan_revision")
        or row.payload.get("_style_revision_requested")
    ):
        return _wire(row)
    from .replica_analysis_recovery import (
        exact_revision_available,
        style_revision_available,
    )

    if exact_revision_available(row, _analysis_input_hash(row.payload)):
        checkpoint = row.payload["_analysis_checkpoint"]
        row.payload = {
            **{
                k: v for k, v in row.payload.items() if k not in {"error", "error_code"}
            },
            "_exact_plan_revision": {
                "id": uuid4().hex,
                "parent_snapshot_sha256": _hash(checkpoint),
            },
            "_analysis_lineage": [
                *row.payload.get("_analysis_lineage", []),
                checkpoint,
            ],
            "analysis_attempt_id": uuid4().hex,
        }
        row.status, row.updated_at = "analysing", utc_now()
        session.commit()
        background_tasks.add_task(
            _analyse, request.app, row.id, row.payload["analysis_attempt_id"]
        )
        return _wire(row)

    if style_revision_available(row, _analysis_input_hash(row.payload)):
        row.payload = {
            **{
                k: v for k, v in row.payload.items() if k not in {"error", "error_code"}
            },
            "_style_revision_requested": uuid4().hex,
            "_style_analysis_revision": {
                "parent_snapshot_sha256": _hash(row.payload["_analysis_checkpoint"])
            },
            "_analysis_lineage": [
                *row.payload.get("_analysis_lineage", []),
                row.payload["_analysis_checkpoint"],
            ],
            "analysis_attempt_id": uuid4().hex,
        }
        row.status, row.updated_at = "analysing", utc_now()
        session.commit()
        background_tasks.add_task(
            _analyse, request.app, row.id, row.payload["analysis_attempt_id"]
        )
        return _wire(row)
    if not _revision_available(row) or row.payload.get(
        "_analysis_input_hash"
    ) != _analysis_input_hash(row.payload):
        raise ApiError(
            409, "REPLICA_REVISION_UNAVAILABLE", "当前任务没有可保留并继续修正的方案。"
        )
    checkpoint = row.payload["_analysis_checkpoint"]
    row.payload = {
        **{
            key: value
            for key, value in row.payload.items()
            if key not in {"error", "error_code", "analysis_error_details"}
        },
        "_exact_model_protocol": VERSION,
        "_analysis_revision": {
            "parent_protocol": checkpoint["model_protocol"],
            "parent_snapshot_sha256": _hash(checkpoint),
        },
        "analysis_attempt_id": uuid4().hex,
    }
    row.status, row.updated_at = "analysing", utc_now()
    session.commit()
    background_tasks.add_task(
        _analyse, request.app, row.id, row.payload["analysis_attempt_id"]
    )
    return _wire(row)


@router.post("/sessions/{document_id}/confirm", status_code=202)
def confirm(
    document_id: str,
    payload: ConfirmRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> dict:
    from .routes_v1 import create_job

    row = _get(session, workspace.workspace_id, document_id, "session")
    values = dict(row.payload)
    if values.get("auto_start_after_plan") is True:
        values.pop("automatic_submission_error", None)
        values.pop("automatic_submission_error_code", None)
        values["automatic_plan_confirmation"] = {
            "confirmation_source": "task_submission_default",
            "actor_user_id": workspace.principal.user_id,
            "plan_hash": payload.plan_hash,
            "confirmed_at": utc_now().isoformat(),
        }
        row.payload = values
    if values.get("plan_hash") != payload.plan_hash:
        raise ApiError(409, "REPLICA_PLAN_CHANGED", "方案已变化，请重新确认。")
    if row.status not in {"awaiting_confirmation", "generating", "completed"}:
        raise ApiError(409, "REPLICA_PLAN_NOT_READY", "请等待图片方案分析完成。")
    if any(len(text) > 4000 for text in payload.instructions):
        raise ApiError(
            422, "REPLICA_INSTRUCTION_TOO_LONG", "单张图片补充要求不能超过4000字。"
        )
    if values.get("replication_method") in SKILLS:
        return _confirm_native(
            document_id, payload, request, background_tasks, workspace, session, row
        )
    prompts = [
        plan["prompt"]
        + (
            "\n用户补充要求：" + payload.instructions[index]
            if index < len(payload.instructions) and payload.instructions[index]
            else ""
        )
        for index, plan in enumerate(values["plans"])
    ]
    if len(payload.instructions) > len(prompts) or any(
        len(prompt) > 4000 for prompt in prompts
    ):
        raise ApiError(
            422,
            "REPLICA_PROMPT_TOO_LONG",
            "补充要求较长，请精简后再确认；方案不会被截断。",
        )
    confirmed_hash = _hash(payload.instructions)
    if (
        values.get("confirmed_instructions_hash")
        and values["confirmed_instructions_hash"] != confirmed_hash
    ):
        raise ApiError(
            409, "REPLICA_ALREADY_CONFIRMED", "该方案已开始生成，请新建复刻来修改要求。"
        )
    if values.get("submission_complete") and len(values.get("job_ids", [])) == len(
        values["plans"]
    ):
        return _wire(row)
    remaining = len(values["plans"]) - len(values.get("job_ids", []))
    limits = lock_workspace_limits(session, workspace.workspace_id)
    active = (
        session.scalar(
            select(func.count(JobRecord.id)).where(
                JobRecord.workspace_id == workspace.workspace_id,
                JobRecord.status.not_in(
                    ["completed", "partial", "failed", "cancelled"]
                ),
            )
        )
        or 0
    )
    available = max(0, limits.max_active_jobs - active)
    if remaining > available:
        raise ApiError(
            429,
            "REPLICA_CAPACITY_WAIT",
            f"当前仍有任务在处理，本次最多还能提交 {available} 张。"
            "请稍后再确认，方案会保留。",
        )
    values["confirmed_instructions"] = payload.instructions
    values["submission_complete"] = False
    values["confirmed_instructions_hash"] = confirmed_hash
    row.payload = values
    session.commit()
    jobs = list(values.get("job_ids", []))
    request.state.replica_session_id = document_id
    for index, plan in enumerate(values["plans"]):
        if index < len(jobs):
            continue
        extra = payload.instructions[index] if index < len(payload.instructions) else ""
        prompt = prompts[index]
        body = ReferenceRemixJobCreate.model_validate(
            {
                "type": "reference_remix",
                "intent": "submit",
                "display_name": "一键复刻 · " + plan["reference"]["title"],
                "input": {
                    "product_asset_ids": values["product_asset_ids"],
                    "reference_asset_ids": [plan["reference"]["asset_id"]],
                },
                "configuration": {
                    "reference_skill": SKILL,
                    "platform_code": values["platform_code"],
                    "language": values["language"],
                    "selling_points": [
                        values["facts"][offset : offset + 500]
                        for offset in range(0, len(values["facts"]), 500)
                    ]
                    if values["facts"]
                    else [],
                    "aspect_ratio": plan["reference"]["aspect_ratio"],
                    "image_count": 1,
                    "execution_mode": "direct_prompt",
                    "inspiration_product_first": True,
                    "quality": "high",
                    "instruction": extra,
                    "original_remix_prompt": prompt,
                },
            }
        )
        created = create_job(
            payload=body,
            request=request,
            background_tasks=background_tasks,
            workspace=workspace,
            session=session,
            idempotency_header=f"replica-generate-{document_id}-{index}",
        )
        if created.id not in jobs:
            jobs.append(created.id)
            row = _get(session, workspace.workspace_id, document_id, "session")
            row.status = "generating"
            row.payload = {**values, "job_ids": list(jobs)}
            session.commit()
    row = _get(session, workspace.workspace_id, document_id, "session")
    row.status, row.payload = (
        "generating",
        {**values, "job_ids": list(jobs), "submission_complete": True},
    )
    session.commit()
    return _wire(row)


def _confirm_native(
    document_id: str,
    payload: ConfirmRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceContext,
    session: Session,
    row: ReplicaDocumentRecord,
) -> dict:
    from .routes_v1 import create_job

    values = dict(row.payload)
    method = values["replication_method"]
    skill_id = SKILLS[method]
    content_hash = BundledSkillManifestValidator().validate(skill_id)
    if (
        values.get("skill_id") != skill_id
        or values.get("skill_content_hash") != content_hash
    ):
        raise ApiError(409, "REPLICA_SKILL_CHANGED", "Skill 版本已变化，请重新分析。")
    binding = {
        key: values[key]
        for key in (
            "replication_method",
            "skill_id",
            "skill_content_hash",
            "plans",
            "native_snapshot_hash",
        )
    }
    if (
        _hash(binding) != payload.plan_hash
        or _hash(values["native_snapshot"]) != values["native_snapshot_hash"]
    ):
        raise ApiError(409, "REPLICA_PLAN_CHANGED", "原生方案绑定已变化，请重新分析。")
    confirmed_hash = _hash(
        [method, content_hash, payload.plan_hash, payload.instructions]
    )
    if values.get("confirmed_instructions_hash"):
        if values["confirmed_instructions_hash"] != confirmed_hash:
            raise ApiError(
                409, "REPLICA_ALREADY_CONFIRMED", "该方案已确认，请新建复刻修改要求。"
            )
        if values.get("job_ids"):
            return _wire(row)
    # Supplemental changes alter the native composition/fact contract. They
    # must enter a fresh analysis instead of being appended after its audit.
    if any(text.strip() for text in payload.instructions):
        raise ApiError(
            409,
            "REPLICA_REANALYSIS_REQUIRED",
            "补充要求会影响原生方案，请返回选图填写要求并重新诊断。",
        )
    values.update(
        confirmed_instructions=payload.instructions,
        confirmed_instructions_hash=confirmed_hash,
        submission_complete=False,
    )
    row.payload = values
    session.commit()
    request.state.replica_session_id = document_id
    body = ReferenceRemixJobCreate.model_validate(
        {
            "type": "reference_remix",
            "intent": "submit",
            "display_name": "一键复刻 · "
            + ("一比一模仿" if method == "exact" else "风格复刻"),
            "input": {
                "product_asset_ids": values["product_asset_ids"],
                "reference_asset_ids": list(
                    dict.fromkeys(r["asset_id"] for r in values["references"])
                ),
            },
            "configuration": {
                "reference_skill": skill_id,
                "replication_method": method,
                "replica_plan_hash": payload.plan_hash,
                "image_count": len(values["plans"]),
                "platform_code": values["platform_code"],
                "language": values["language"],
                "quality": "high",
                "aspect_ratio": "follow-reference",
            },
        }
    )
    created = create_job(
        payload=body,
        request=request,
        background_tasks=background_tasks,
        workspace=workspace,
        session=session,
        idempotency_header="replica-native-" + document_id + "-" + payload.plan_hash,
    )
    row = _get(session, workspace.workspace_id, document_id, "session")
    row.status, row.payload = (
        "generating",
        {**values, "job_ids": [created.id], "submission_complete": True},
    )
    session.commit()
    return _wire(row)


class ImageRetryRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=80)


@router.post("/sessions/{document_id}/jobs/{job_id}/retry", status_code=202)
async def retry_image(
    document_id: str,
    job_id: str,
    payload: ImageRetryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> dict:
    from .domain import RetryScope
    from .routes_v1 import retry_job
    from .schemas_v1 import RetryRequest

    row = _get(session, workspace.workspace_id, document_id, "session")
    values = dict(row.payload)
    attempts = list(values.get("retry_events", []))
    if any(v["from"] == job_id for v in attempts):
        return _wire(row)
    jobs = list(values.get("job_ids", []))
    if job_id not in jobs:
        raise ApiError(404, "REPLICA_JOB_NOT_FOUND", "请从当前复刻结果选择图片。")
    unknown_codes = {"PROVIDER_OUTCOME_UNKNOWN", "IMAGE_RESULT_DOWNLOAD_UNCONFIRMED"}
    original = session.scalar(
        select(JobRecord).where(
            JobRecord.id == job_id, JobRecord.workspace_id == workspace.workspace_id
        )
    )
    uncertain_item = session.scalar(
        select(JobItemRecord.id)
        .where(
            JobItemRecord.job_id == job_id,
            JobItemRecord.workspace_id == workspace.workspace_id,
            JobItemRecord.error_code.in_(unknown_codes),
        )
        .limit(1)
    )
    if (
        original is not None and original.error_code in unknown_codes
    ) or uncertain_item:
        # A replacement job still inherits the native invocation fence. It can
        # neither reconcile the accepted task nor safely submit another image.
        raise ApiError(
            409,
            "REPLICA_OUTCOME_RECONCILIATION_REQUIRED",
            "原生图请求的结果尚未核实，请先查询并找回原任务；已停止重复提交，避免重复生成。",
        )
    retried = await retry_job(
        job_id=job_id,
        payload=RetryRequest(scope=RetryScope.FAILED),
        request=request,
        background_tasks=background_tasks,
        workspace=workspace,
        session=session,
        idempotency_header="replica-retry-" + _hash([document_id, job_id]),
    )
    row = _get(session, workspace.workspace_id, document_id, "session")
    jobs[jobs.index(job_id)] = retried.id
    row.status = "generating"
    row.payload = {
        **{
            key: value
            for key, value in values.items()
            if key
            not in (
                {"error", "error_code"}
                if values.get("error_code") == "REPLICA_ANALYSIS_BUDGET_REACHED"
                else {"execution_progress", "error", "error_code"}
            )
        },
        "job_ids": list(jobs),
        "retry_events": [
            *attempts,
            {"from": job_id, "to": retried.id, "request_id": payload.request_id},
        ],
    }
    session.commit()
    return _wire(row)


@router.post("/sessions/{document_id}/jobs/{job_id}/recover")
async def recover_images(
    document_id: str,
    job_id: str,
    request: Request,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
) -> dict:
    from .replica_preserved_outputs import recover_outputs

    job = session.scalar(
        select(JobRecord)
        .where(JobRecord.id == job_id, JobRecord.workspace_id == workspace.workspace_id)
        .with_for_update()
    )
    row = _get(session, workspace.workspace_id, document_id, "session")
    if job is None:
        raise ApiError(404, "REPLICA_JOB_NOT_FOUND", "未找到当前复刻任务。")
    try:
        count = await recover_outputs(session, request.app.state.storage, row, job)
    except ReplicaNativeError as error:
        raise ApiError(409, "REPLICA_RECOVERY_INVALID", str(error)) from error
    reconcile_document(session, row)
    session.commit()
    return {"recovered": count, "job_id": job_id}
