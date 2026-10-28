"""Verify original native delivery and keep filesystem finalization recoverable."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
    detail_workspace,
)
from ..database import read_snapshot_async
from ..models import (
    AssetRecord,
    JobAssetRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
)
from ..storage import ImageStorage
from .generation import (
    DetailGenerationError,
    bind_native_contract,
    generation_binding,
    require_current_pipeline,
    verify_accepted_canvas,
)
from .pixels import DetailPixelError, load_native_pair_pixels
from .plans import DetailPlanError, canonical_hash

DELIVERY_VERSION = "native-delivery-v1"


class DetailDeliveryError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _reject(message: str, code: str = "NATIVE_DETAIL_DELIVERY_INVALID") -> NoReturn:
    raise DetailDeliveryError(code, message)


def _json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction():
        _reject("Original delivery evidence cannot traverse a link")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        _reject("Original delivery evidence must be an object")
    return value


def _once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.parent.is_junction():
        _reject("Delivery evidence cannot be written through a link")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _accepted(module: Mapping[str, Any]) -> dict[str, Any]:
    attempts = [
        row
        for row in module.get("attempts", [])
        if row.get("image") == module.get("accepted_asset")
    ]
    if not attempts or module.get("status") not in {
        "COMPLETE",
        "COMPLETE_WITH_RESIDUALS",
    }:
        _reject("Every delivered screen needs its original accepted native attempt")
    return dict(attempts[-1])


def _stable_session(state: Mapping[str, Any]) -> str:
    return canonical_hash(
        {
            key: value
            for key, value in state.items()
            if key not in {"state", "events", "updated_at", "final"}
        }
    )


def _workspaces(
    db: Session, job: JobRecord, binding: Mapping[str, Any]
) -> tuple[Path, ...]:
    roots = [detail_workspace(job.id)]
    current = job
    seen = {job.id}
    while current.retry_of_job_id:
        prior = db.get(JobRecord, current.retry_of_job_id)
        if (
            prior is None
            or prior.workspace_id != job.workspace_id
            or canonical_hash(prior.configuration) != canonical_hash(job.configuration)
        ):
            break
        if prior.id in seen:
            _reject("Native delivery retry ancestry contains a cycle")
        seen.add(prior.id)
        root = detail_workspace(prior.id)
        original = root / "generation-binding.json"
        if original.is_file() and canonical_hash(_json(original)) == canonical_hash(
            binding
        ):
            roots.append(root)
        current = prior
    return tuple(roots)


def _pair_origin(
    db: Session,
    job: JobRecord,
    binding: Mapping[str, Any],
    state: Mapping[str, Any],
    pair_id: str,
    seen: frozenset[str] = frozenset(),
) -> tuple[tuple[Path, ...], Mapping[str, Any]]:
    """Each inherited pair retains its own original paid generation authority."""
    if job.id in seen:
        _reject("Local revision ancestry contains a cycle")
    intent = job.configuration.get("local_revision")
    if intent is None:
        return _workspaces(db, job, binding), binding
    from .revision_authorization import authorize_revision_quote

    if (
        not isinstance(intent, dict)
        or intent.get("job_id") != job.id
        or intent.get("workspace_id") != job.workspace_id
        or intent.get("sha256")
        != canonical_hash({k: v for k, v in intent.items() if k != "sha256"})
    ):
        _reject("Original local revision authority changed")
    quote = intent["authority"]["quote"]
    authority = authorize_revision_quote(
        quote,
        submitted_sha256=quote["sha256"],
        approval_text=intent["approval_text"],
        source_job_id=intent["source_job_id"],
        workspace_id=job.workspace_id,
        actor_id=intent["actor_id"],
    )
    root = detail_workspace(job.id)
    fork = state.get("revision_fork") or {}
    if (
        canonical_hash(intent["authority"]) != canonical_hash(authority)
        or fork.get("intent_sha256") != intent["sha256"]
        or fork.get("source_job_id") != intent["source_job_id"]
        or fork.get("pair_id") != quote["pair_id"]
        or fork.get("module_ids") != quote["module_ids"]
        or fork.get("source_session_sha256") != intent["source_session_sha256"]
        or _sha(root / "source-session.json") != intent["source_session_sha256"]
        or canonical_hash(_json(root / "local-revision-intent.json"))
        != canonical_hash(intent)
    ):
        _reject("Native local revision differs from its original source authority")
    if pair_id == quote["pair_id"]:
        return _workspaces(db, job, binding), binding
    source = db.get(JobRecord, intent["source_job_id"])
    source_plan = db.get(JobPlanRecord, quote["source_plan_id"])
    if (
        source is None
        or source.workspace_id != job.workspace_id
        or source_plan is None
        or source_plan.job_id != source.id
        or source_plan.workspace_id != job.workspace_id
        or canonical_hash(source_plan.payload) != quote["source_plan_payload_sha256"]
    ):
        _reject("An inherited pair lost its exact original source job and plan")
    snapshot = _json(root / "source-session.json")
    pair = next(
        p
        for p in state["plan"]["output_strategy"]["continuous_pair_rendering"][
            "pair_ids"
        ]
        if p["pair_id"] == pair_id
    )
    selected = set(pair["module_ids"])
    if [m for m in state["modules"] if m["id"] in selected] != [
        m for m in snapshot["modules"] if m["id"] in selected
    ]:
        _reject("An inherited pair changed its original accepted modules")
    original_binding = _json(detail_workspace(source.id) / "generation-binding.json")
    if original_binding["sha256"] != intent["source_generation_binding_sha256"]:
        _reject("An inherited pair changed its original generation binding")
    return _pair_origin(
        db, source, original_binding, snapshot, pair_id, seen | {job.id}
    )


async def _stored(
    storage: ImageStorage, workspace_id: str, key: str, payload: bytes
) -> None:
    if not isinstance(key, str) or not key.startswith(workspace_id + "/"):
        _reject("A delivery object escaped its workspace")
    try:
        actual = await storage.read_bytes(key)
    except Exception as exc:
        raise DetailDeliveryError(
            "NATIVE_DETAIL_DELIVERY_STORAGE_UNAVAILABLE",
            "Original delivery storage is unavailable; preserve the accepted result",
            503,
        ) from exc
    if actual != payload:
        _reject(
            "Durable delivery bytes differ from the exact accepted native raster",
            "NATIVE_DETAIL_DELIVERY_ASSET_CHANGED",
        )


def _delivery_rows(db: Session, job: JobRecord) -> list[Any]:
    rows = [
        (asset, link, item)
        for asset, link, item in db.execute(
            select(AssetRecord, JobAssetRecord, JobItemRecord)
            .join(JobAssetRecord, JobAssetRecord.asset_id == AssetRecord.id)
            .join(JobItemRecord, JobItemRecord.id == JobAssetRecord.job_item_id)
            .where(
                JobAssetRecord.workspace_id == job.workspace_id,
                JobAssetRecord.job_id == job.id,
                JobAssetRecord.direction == "output",
                JobAssetRecord.is_deliverable.is_(True),
                JobAssetRecord.role == "detail_page",
            )
            .order_by(JobItemRecord.ordinal)
        )
    ]
    return rows


def _delivery_pair_pixels(
    pair_roots: Sequence[Path],
    pair_binding: Mapping[str, Any],
    pair_id: str,
    canvas_sha: str,
    group_key: str,
) -> tuple[dict[str, Any], Any]:
    bundle = verify_accepted_canvas(
        pair_roots, pair_binding, pair_id=pair_id, artifact_sha256=canvas_sha
    )
    pixels = load_native_pair_pixels(bundle, pair_roots, group_key=group_key)
    return bundle, pixels


async def _verified_context(
    db: Session,
    job: JobRecord,
    plan: JobPlanRecord,
    storage: ImageStorage,
    *,
    read_only: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    require_current_pipeline(job.configuration)
    payload = plan.payload
    groups = payload["source_asset_manifest"]
    if (
        len(groups) != 1
        or type(payload.get("group_count")) is not int
        or payload["group_count"] != 1
    ):
        _reject("Native delivery requires one exact approved product campaign")
    group = groups[0]
    binding = generation_binding(
        payload,
        job.configuration["settings"],
        group["assets"],
        group_key=group["group_key"],
        job_input=job.configuration.get("input") or {},
    )
    root = detail_workspace(job.id)
    bind_native_contract(root, binding)
    state = _json(root / "session.json")
    from .delivery_authorization import (
        AUTO_MODE,
        job_receipt_authorization,
        native_authorization_text,
    )

    authorization = (
        await read_snapshot_async(db, job_receipt_authorization, db, job, plan)
        if read_only
        else job_receipt_authorization(db, job, plan)
    )
    expected_state = (
        AUTO_MODE if authorization is not None else "AWAITING_USER_FINAL_REVIEW"
    )
    if state.get("state") not in {expected_state, "FINALIZED"}:
        _reject(
            "The accepted native session is not awaiting its original delivery mode"
        )
    if authorization is not None and (
        (state.get("approval") or {}).get("delivery_mode") != AUTO_MODE
        or (state.get("approval") or {}).get("text")
        != native_authorization_text(payload)
    ):
        _reject(
            "Native automatic delivery differs from the original explicit authorization"
        )
    if state.get("assembly") or state.get("long_page_audit"):
        _reject(
            "Optional campaign preview needs its original website review evidence",
            "NATIVE_DETAIL_CAMPAIGN_EVIDENCE_REQUIRED",
        )
    modules = state["modules"]
    count = job.expected_item_count
    if count not in {8, 10, 12} or len(modules) != count:
        _reject("Native delivery does not cover the approved screens")
    rows = (
        await read_snapshot_async(db, _delivery_rows, db, job)
        if read_only
        else _delivery_rows(db, job)
    )
    if len(rows) != count or [item.ordinal for _, _, item in rows] != list(
        range(count)
    ):
        _reject("Durable screens are missing, duplicated or reordered")
    pairs = state["plan"]["output_strategy"]["continuous_pair_rendering"]["pair_ids"]
    if len(pairs) != count // 2:
        _reject("Accepted native pair coverage is incomplete")
    screens: list[dict[str, Any]] = []
    canvases: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for position, pair in enumerate(pairs):
        start = position * 2
        selected = modules[start : start + 2]
        if pair["module_ids"] != [m["id"] for m in selected]:
            _reject("Accepted native pairs differ from the approved adjacent windows")
        first = _accepted(selected[0])
        canvas_sha = first["normalized_image_sha256"]
        if any(
            _accepted(m).get("normalized_image_sha256") != canvas_sha for m in selected
        ):
            _reject("Two accepted screens do not share their original pair canvas")
        pair_roots, pair_binding = (
            await read_snapshot_async(
                db, _pair_origin, db, job, binding, state, pair["pair_id"]
            )
            if read_only
            else _pair_origin(db, job, binding, state, pair["pair_id"])
        )
        if read_only:
            bundle, pixels = await run_in_threadpool(
                _delivery_pair_pixels,
                pair_roots,
                pair_binding,
                pair["pair_id"],
                canvas_sha,
                group["group_key"],
            )
        else:
            bundle = verify_accepted_canvas(
                pair_roots,
                pair_binding,
                pair_id=pair["pair_id"],
                artifact_sha256=canvas_sha,
            )
            pixels = load_native_pair_pixels(
                bundle, pair_roots, group_key=group["group_key"]
            )
        keys = []
        for offset, raster in enumerate(pixels.screens):
            asset, link, item = rows[start + offset]
            module = selected[offset]
            if (
                asset.workspace_id != job.workspace_id
                or asset.deleted_at is not None
                or asset.lifecycle_status != "ready"
                or item.status not in {"succeeded", "reused"}
                or (asset.sha256, asset.width, asset.height, asset.byte_size)
                != (raster.sha256, raster.width, raster.height, len(raster.payload))
                or (
                    asset.media_kind,
                    asset.content_type,
                    asset.storage_provider,
                    asset.storage_bucket,
                )
                != (
                    "image",
                    raster.content_type,
                    storage.provider_name,
                    storage.bucket_name,
                )
                or item.configuration.get("page") != start + offset + 1
                or item.configuration.get("group_key") != group["group_key"]
                or item.configuration.get("pair_id") != pair["pair_id"]
                or item.configuration.get("pair_position") != offset + 1
            ):
                _reject("Durable screen metadata differs from accepted native pixels")
            await _stored(storage, job.workspace_id, asset.object_key, raster.payload)
            validation = link.validation
            key = validation.get("aurora_pair_canvas_object_key")
            if (
                not isinstance(key, str)
                or validation.get("aurora_pair_id") not in (None, pair["pair_id"])
                or validation.get("aurora_pair_canvas_sha256") != pixels.canvas.sha256
            ):
                _reject("The original accepted pair-canvas reference changed")
            keys.append(key)
            screens.append(
                {
                    "module_id": module["id"],
                    "asset_id": asset.id,
                    "ordinal": item.ordinal,
                    "sha256": asset.sha256,
                    "object_key": asset.object_key,
                    "object_version": asset.object_version,
                    "storage_provider": asset.storage_provider,
                    "storage_bucket": asset.storage_bucket,
                    "width": asset.width,
                    "height": asset.height,
                }
            )
        # A selected retry may retain the source sibling and copy the other to
        # its new job key. Every alias must contain the same accepted canvas.
        for key in dict.fromkeys(keys):
            await _stored(storage, job.workspace_id, key, pixels.canvas.payload)
        canvases.append(
            {
                "pair_id": pair["pair_id"],
                "module_ids": pair["module_ids"],
                "object_key": keys[-1],
                "source_object_keys": list(dict.fromkeys(keys)),
                "sha256": pixels.canvas.sha256,
                "width": pixels.canvas.width,
                "height": pixels.canvas.height,
                "pixel_lineage_sha256": bundle["sha256"],
                **(
                    {"generation_binding_sha256": pair_binding["sha256"]}
                    if pair_binding["sha256"] != binding["sha256"]
                    else {}
                ),
            }
        )
        audits.append(
            {
                "pair_id": pair["pair_id"],
                "judgment": deepcopy(first["judgment"]),
                "review_evidence": deepcopy(first.get("review_evidence")),
            }
        )
    context = {
        "generation_binding_sha256": binding["sha256"],
        "native_session_truth_sha256": _stable_session(state),
        "screens": screens,
        "pair_canvases": canvases,
        "pair_audits": audits,
    }
    return context, state, root


def _inspect(root: Path, output: Path) -> dict[str, Any]:
    if not output.resolve().is_relative_to(root.resolve()):
        _reject("Native final output escaped its owned delivery directory")
    return AuroraDetailHarnessRunner().run(
        "inspect-final-manifest",
        root,
        {"session": root / "session.json", "manifest": output},
    )


def _completion(
    root: Path, output: Path, request: Mapping[str, Any], response: Path
) -> dict[str, Any]:
    inspected = _inspect(root, output)
    state = _json(root / "session.json")
    binding = request["binding"]
    if (
        _stable_session(state) != binding["native_session_truth_sha256"]
        or inspected["manifest"]["approval"]["text"] != binding["approval_text"]
        or inspected["manifest"]["approval"]["mode"]
        != binding.get("delivery_mode", "USER_FINAL_REVIEW")
    ):
        _reject("The original finalized native intent changed")
    value = {
        "version": DELIVERY_VERSION,
        "binding_sha256": canonical_hash(binding),
        "request_sha256": canonical_hash(request),
        "request_file_sha256": _sha(response.parent / "request.json"),
        "native_inspection": inspected,
        "session_after_sha256": _sha(root / "session.json"),
    }
    sealed = {**value, "sha256": canonical_hash(value)}
    _once(response, sealed)
    return sealed


def _finish(
    root: Path, binding: Mapping[str, Any], *, allow_create: bool
) -> dict[str, Any]:
    directory = root / "delivery" / canonical_hash(binding)
    request_path = directory / "request.json"
    response_path = directory / "response.json"
    output = directory / "native-final-manifest.json"
    state = _json(root / "session.json")
    if request_path.exists():
        request = _json(request_path)
        before = base64.b64decode(request["session_before_bytes_base64"], validate=True)
        before_state = json.loads(before)
        if (
            request.get("version") != DELIVERY_VERSION
            or hashlib.sha256(before).hexdigest()
            != request.get("session_before_sha256")
            or _stable_session(before_state) != binding["native_session_truth_sha256"]
            or before_state.get("state")
            != (
                "AUTO_FINALIZE_AUTHORIZED"
                if binding.get("delivery_mode") == "AUTO_FINALIZE_AUTHORIZED"
                else "AWAITING_USER_FINAL_REVIEW"
            )
        ):
            _reject("The original pre-finalization session evidence changed")
        if canonical_hash(request.get("binding")) != canonical_hash(binding):
            _reject("Original native delivery request binding changed")
        if response_path.exists():
            response = _json(response_path)
            proof = {k: v for k, v in response.items() if k != "sha256"}
            if (
                response.get("sha256") != canonical_hash(proof)
                or response.get("binding_sha256") != canonical_hash(binding)
                or response.get("request_sha256") != canonical_hash(request)
                or response.get("request_file_sha256") != _sha(request_path)
                or response.get("session_after_sha256") != _sha(root / "session.json")
                or canonical_hash(response.get("native_inspection"))
                != canonical_hash(_inspect(root, output))
            ):
                _reject("The original completed native delivery evidence changed")
            return response
        if state.get("state") == "FINALIZED":
            return _completion(root, output, request, response_path)
        _reject(
            "Reconcile the original local finalization before repeating it",
            "NATIVE_DETAIL_FINALIZATION_OUTCOME_UNKNOWN",
        )
    automatic = binding.get("delivery_mode") == "AUTO_FINALIZE_AUTHORIZED"
    expected_state = (
        "AUTO_FINALIZE_AUTHORIZED" if automatic else "AWAITING_USER_FINAL_REVIEW"
    )
    if not allow_create or state.get("state") != expected_state:
        _reject("The original native finalization request is missing")
    before = (root / "session.json").read_bytes()
    request = {
        "version": DELIVERY_VERSION,
        "binding": dict(binding),
        "session_before_sha256": hashlib.sha256(before).hexdigest(),
        "session_before_bytes_base64": base64.b64encode(before).decode("ascii"),
    }
    _once(request_path, request)
    AuroraDetailHarnessRunner().run(
        "auto-finalize" if automatic else "finalize",
        root,
        {
            "session": root / "session.json",
            "output": output,
            **({} if automatic else {"approval-text": str(binding["approval_text"])}),
        },
    )
    return _completion(root, output, request, response_path)


def public_delivery_manifest(
    completed: Mapping[str, Any], context: Mapping[str, Any], review: Mapping[str, Any]
) -> dict[str, Any]:
    manifest = deepcopy(completed["native_inspection"]["manifest"])
    return {
        **manifest,
        "skill_id": manifest["skill"],
        "state": "FINALIZED",
        "delivery": manifest["approval"]["mode"],
        "native_manifest_sha256": completed["native_inspection"][
            "manifest_file_sha256"
        ],
        "native_delivery_receipt_sha256": completed["sha256"],
        "quality_state": review["quality_state"],
        "campaign_audit_status": "NOT_REQUESTED",
        "page_asset_refs": [row["asset_id"] for row in context["screens"]],
        "pair_canvas_asset_refs": review["pair_canvases"],
        "final_review": {"sha256": review["sha256"], "version": review["version"]},
        "pair_audits": context["pair_audits"],
    }


async def finalize_native_delivery(
    db: Session,
    job: JobRecord,
    plan: JobPlanRecord,
    storage: ImageStorage,
    *,
    actor_id: str,
    approval_text: str,
    final_review_sha256: str | None = None,
    allow_create: bool = True,
) -> dict[str, Any]:
    """Verify every original, then finalize once or recover the same local intent."""
    try:
        if not approval_text.strip():
            _reject("Explicit final human review text is required")
        from .delivery_authorization import receipt_authorization
        from .review_packet import validate_final_review

        if receipt_authorization(plan.payload) is not None:
            _reject(
                "Use the original authorized automatic-delivery recovery",
                "NATIVE_DETAIL_AUTOMATIC_DELIVERY_RECOVERY_REQUIRED",
            )
        context, state, root = await _verified_context(db, job, plan, storage)
        review = await validate_final_review(
            db, job, plan, storage, context, state, root, final_review_sha256
        )
        binding = {
            "version": DELIVERY_VERSION,
            "job_id": job.id,
            "workspace_id": job.workspace_id,
            "actor_id": actor_id,
            "plan_id": plan.id,
            "plan_revision": plan.revision,
            "approval_text": approval_text,
            "final_review_sha256": review["sha256"],
            **context,
        }
        completed = _finish(root, binding, allow_create=allow_create)
        return public_delivery_manifest(completed, context, review)
    except DetailDeliveryError:
        raise
    except (
        DetailGenerationError,
        DetailPixelError,
        DetailPlanError,
        AuroraDetailHarnessError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        code = str(getattr(exc, "code", "NATIVE_DETAIL_DELIVERY_INVALID"))
        if not code.startswith("NATIVE_DETAIL_"):
            code = "NATIVE_DETAIL_DELIVERY_INVALID"
        raise DetailDeliveryError(code, str(exc)) from exc
