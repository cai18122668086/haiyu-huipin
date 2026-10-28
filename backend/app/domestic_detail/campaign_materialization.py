"""Materialize native campaign observations from one verified original result."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from ..aurora_detail_runtime import AuroraDetailHarnessError, AuroraDetailHarnessRunner
from .campaign_durability import bytes_once, encoded, json_once, replace_derived, sha
from .campaign_jobs import CAMPAIGN_JOB_VERSION
from .campaign_protocol import validate_campaign_observation
from .delivery import _json
from .plans import canonical_hash
from .review import ReviewStore

_sha = sha


def validate_campaign_preview(preview: bytes, screens: Sequence[bytes]) -> None:
    """An optional preview must contain the exact accepted RGB windows in order."""
    if not screens:
        raise ValueError("Campaign preview needs its accepted screens")
    with Image.open(BytesIO(preview)) as canvas:
        canvas.load()
        if canvas.format != "PNG" or canvas.mode != "RGB":
            raise ValueError("Campaign preview must preserve native RGB PNG pixels")
        top = 0
        for payload in screens:
            with Image.open(BytesIO(payload)) as screen:
                screen.load()
                width, height = screen.size
                if screen.mode != "RGB" or width != canvas.width:
                    raise ValueError("Preview differs from the accepted screen profile")
                if (
                    top + height > canvas.height
                    or canvas.crop((0, top, width, top + height)).tobytes()
                    != screen.tobytes()
                ):
                    raise ValueError("Preview pixels differ from accepted screen order")
                top += height
        if top != canvas.height:
            raise ValueError("Preview contains additional unaccepted pixels")


def materialize_campaign_review(
    root: Path,
    job_id: str,
    intent: dict[str, Any],
    model_context: dict[str, Any],
    result: Any,
    store: ReviewStore,
    journal_sha256: str | None,
    *,
    publish: Callable[[Path, dict[str, Any]], None] = json_once,
    publish_bytes: Callable[[Path, bytes], None] = bytes_once,
) -> tuple[dict[str, Any], str | None]:
    runner = AuroraDetailHarnessRunner()
    preview = (root / "long-page.png").read_bytes()
    judgment = validate_campaign_observation(
        result.payload, model_context, original_text=result.audit_raw_content
    )
    judgment_path = root / "campaign-judgment.json"
    if judgment_path.exists():
        if canonical_hash(_json(judgment_path)) != canonical_hash(judgment):
            raise ValueError("Original campaign judgment changed")
    else:
        publish(judgment_path, judgment)
    gate_error = None
    target = _json(root / "session.json")
    if target.get("long_page_audit"):
        if canonical_hash(target["long_page_audit"]) != canonical_hash(judgment):
            raise ValueError("Native campaign audit differs from original observations")
    else:
        try:
            runner.run(
                "audit-long-page",
                root,
                {"session": root / "session.json", "judgment": judgment_path},
            )
        except AuroraDetailHarnessError as exc:
            if exc.native_failure_kind == "LOCAL_IO" or (
                exc.native_failure_kind != "CONTRACT_REJECTION"
                and exc.code != "HARNESS_BLOCKED"
            ):
                raise OSError(
                    "Local native audit could not finish: " + str(exc)
                ) from exc
            gate_error = str(exc)
    publish_bytes(root / "audited-session.json", (root / "session.json").read_bytes())
    report = {
        "version": CAMPAIGN_JOB_VERSION,
        "review_job_id": job_id,
        "source_job_id": intent["source_job_id"],
        "intent_sha256": intent["intent_sha256"],
        "source_context_sha256": intent["source_context_sha256"],
        "request_context_sha256": canonical_hash(model_context),
        "request_file_sha256": _sha(store.request_path.read_bytes()),
        "response_file_sha256": _sha(store.response_path.read_bytes()),
        "native_session_sha256": _sha((root / "audited-session.json").read_bytes()),
        "native_session_file": "audited-session.json",
        "model_result_journal_sha256": journal_sha256,
        "preview_sha256": _sha(preview),
        "judgment": judgment,
        "native_gate_accepted": gate_error is None,
        "native_gate_error": gate_error,
        "residual_checks": [
            name for name, row in judgment["checks"].items() if row["pass"] is False
        ],
    }
    if intent.get("original_review_job_id"):
        report["original_review"] = {
            "job_id": intent["original_review_job_id"],
            "intent_sha256": intent["original_review_intent_sha256"],
            "journal_file_sha256": intent["original_result_journal_sha256"],
        }
    sealed = {**report, "sha256": canonical_hash(report)}
    path = root / "campaign-report.json"
    if path.exists():
        try:
            existing = _json(path)
        except (ValueError, OSError):
            if journal_sha256 is None:
                raise
            replace_derived(path, encoded(sealed), journal_sha256)
        else:
            if canonical_hash(existing) != canonical_hash(sealed):
                raise ValueError("Original campaign report changed")
    else:
        publish(path, sealed)
    return sealed, gate_error
