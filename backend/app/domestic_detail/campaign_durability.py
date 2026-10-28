"""Atomic campaign evidence and recovery from an original durable model result."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .delivery import _json
from .planner_dispatch import validate_dispatch_result
from .plans import canonical_hash
from .review import ReviewStore

JOURNAL = "campaign-model-result.json"


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def encoded(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode(
        "utf-8"
    )


def _stage(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction():
        raise ValueError("Campaign evidence cannot traverse links")
    staged = path.with_name(".campaign-stage-" + uuid4().hex)
    # A failed unpublished write stays here for inspection. Never remove the
    # only partial original model result just because the filesystem failed.
    with staged.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if staged.read_bytes() != payload:
        raise OSError("Staged campaign evidence did not read back exactly")
    return staged


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def bytes_once(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.is_symlink() or path.read_bytes() != payload:
            raise ValueError("Original campaign evidence bytes changed")
        return
    staged = _stage(path, payload)
    try:
        # Atomic, exclusive publication on both Windows and Linux. Both names
        # belong to this job; remove the temporary alias only after read-back.
        os.link(staged, path)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != payload:
            raise ValueError("Concurrent original campaign evidence differs") from None
    if path.read_bytes() != payload:
        raise OSError("Published campaign evidence did not read back exactly")
    _sync_directory(path.parent)
    staged.unlink()
    _sync_directory(path.parent)


def json_once(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if canonical_hash(_json(path)) != canonical_hash(value):
            raise ValueError("Original campaign document changed")
        return
    bytes_once(path, encoded(value))


def save_result(store: ReviewStore, result: Mapping[str, Any]) -> str:
    path = store.root.parent / JOURNAL
    if path.exists():
        original = load_result(store)
        if canonical_hash(original["result"]) != canonical_hash(result):
            raise ValueError("Original durable model result changed")
        return sha(path.read_bytes())
    value = {
        "version": "campaign-model-result-v1",
        "binding": dict(store.binding),
        "request_file_sha256": sha(store.request_path.read_bytes()),
        "result": dict(result),
        "result_sha256": canonical_hash(result),
        "captured_at": datetime.now(UTC).isoformat(),
    }
    json_once(path, {**value, "sha256": canonical_hash(value)})
    return sha(path.read_bytes())


def load_result(store: ReviewStore) -> dict[str, Any]:
    value = _json(store.root.parent / JOURNAL)
    if (
        value.get("version") != "campaign-model-result-v1"
        or value.get("sha256")
        != canonical_hash({k: v for k, v in value.items() if k != "sha256"})
        or canonical_hash(value.get("binding")) != store.sha256
        or value.get("request_file_sha256") != sha(store.request_path.read_bytes())
        or value.get("result_sha256") != canonical_hash(value.get("result"))
        or not isinstance(value.get("result"), dict)
    ):
        raise ValueError("Original model result journal or request changed")
    validate_dispatch_result(value["result"], store.binding["dispatch_route"])
    return value


def replace_derived(path: Path, payload: bytes, journal_sha256: str) -> None:
    """Preserve every damaged byte before reconstructing a derived receipt."""
    if not path.exists():
        bytes_once(path, payload)
        return
    original = path.read_bytes()
    if original == payload:
        return
    archive = (
        path.parent
        / "recovered-originals"
        / (sha(path.name.encode("utf-8") + b"\0" + original) + ".bytes")
    )
    bytes_once(archive, original)
    proof = {
        "path": path.name,
        "original_sha256": sha(original),
        "replacement_sha256": sha(payload),
        "journal_sha256": journal_sha256,
    }
    json_once(archive.with_suffix(".json"), proof)
    staged = _stage(path, payload)
    if path.read_bytes() != original:
        raise ValueError("Derived receipt changed during original-byte preservation")
    os.replace(staged, path)
    _sync_directory(path.parent)
    if path.read_bytes() != payload or archive.read_bytes() != original:
        raise OSError("Reconstructed receipt or preserved original did not verify")


def recover_response(store: ReviewStore) -> dict[str, Any]:
    journal = load_result(store)
    result = journal["result"]
    try:
        current = _json(store.response_path) if store.response_path.exists() else None
    except (ValueError, OSError):
        current = None
    if (
        isinstance(current, dict)
        and isinstance(current.get("result"), dict)
        and canonical_hash(current["result"]) != canonical_hash(result)
    ):
        raise ValueError("Saved response conflicts with original model journal")
    raw = result.get("audit_raw_content")
    raw_receipt = None
    journal_sha = sha((store.root.parent / JOURNAL).read_bytes())
    if isinstance(raw, str):
        payload = raw.encode("utf-8")
        replace_derived(store.stem.with_suffix(".reply.txt"), payload, journal_sha)
        raw_receipt = {"sha256": sha(payload), "bytes": len(payload)}
    # A valid response remains byte-identical, including its original timestamp.
    try:
        completed = store.completed()
        if completed is not None:
            return completed
    except (ValueError, OSError):
        pass
    response = {
        "binding_sha256": store.sha256,
        "result": result,
        "result_sha256": canonical_hash(result),
        "response": result.get("payload"),
        "raw_reply": raw_receipt,
        "raw_reply_state": "original_message_utf8"
        if raw_receipt
        else "predecoded_only",
        "completed_at": journal["captured_at"],
        "reconstructed_from_original_journal_sha256": journal_sha,
    }
    replace_derived(store.response_path, encoded(response), journal_sha)
    completed = store.completed()
    if completed is None:
        raise ValueError("Original model result could not be reconstructed")
    return completed
