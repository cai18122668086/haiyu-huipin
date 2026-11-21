"""Immutable inspection pixels bound to the original signed native canvas."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from .plans import canonical_hash
from .review import DetailReviewError

VERSION = "native-source-comparison-evidence-v1"
ROLE = "source_candidate_comparison"
_SHA = re.compile(r"[a-f0-9]{64}")


def valid_view(asset_id: Any, role: Any, index: int, count: int) -> bool:
    if role == "external_copy_inspection":
        return (
            index > 0 and index == count - 1 and asset_id == "external-copy-inspection"
        )
    return role == ROLE and index < 12 and asset_id == f"source-candidate-pair-{index}"


def _invalid() -> NoReturn:
    raise DetailReviewError(
        "NATIVE_DETAIL_REVIEW_PROVENANCE_INVALID",
        "Original source comparison evidence is missing, changed or out of scope",
    )


def _binding(native: Mapping[str, Any], candidate_sha: str) -> dict[str, Any]:
    if not _SHA.fullmatch(candidate_sha) or not native.get("token"):
        _invalid()
    return {
        "native_request_sha256": canonical_hash(native),
        "candidate_sha256": candidate_sha,
        "render_token": native["token"],
    }


def _path(root: Path, filename: str, *, create: bool = False) -> Path:
    parent = root / "reviews"
    if (
        root.is_symlink() or root.is_junction()
        or parent.is_symlink() or parent.is_junction()
    ):
        _invalid()
    if create:
        parent.mkdir(mode=0o700, exist_ok=True)
    path = parent / filename
    if path.is_symlink() or path.is_junction():
        _invalid()
    return path


def _write_once(path: Path, payload: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.is_junction() or path.read_bytes() != payload:
            _invalid()
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def save_source_comparisons(
    root: Path, native: Mapping[str, Any], candidate_sha: str, sources: Sequence[Any]
) -> None:
    """Persist only already-verified comparison bytes before the first review."""
    if not sources:
        return
    if len(sources) > 13:
        _invalid()
    binding = _binding(native, candidate_sha)
    rows = []
    for index, source in enumerate(sources):
        if (
            not valid_view(source.asset_id, source.source_role, index, len(sources))
            or not _SHA.fullmatch(source.sha256)
            or not 1 <= len(source.payload) <= 20 * 1024 * 1024
            or hashlib.sha256(source.payload).hexdigest() != source.sha256
        ):
            _invalid()
        filename = "source-comparison-view-" + source.sha256 + ".png"
        _write_once(_path(root, filename, create=True), source.payload)
        rows.append(
            {
                "asset_id": source.asset_id,
                "role": source.source_role,
                "sha256": source.sha256,
                "filename": filename,
                "byte_size": len(source.payload),
            }
        )
    value = {"version": VERSION, "binding": binding, "sources": rows}
    payload = {**value, "manifest_sha256": canonical_hash(value)}
    filename = "source-comparisons-" + canonical_hash(binding) + ".json"
    _write_once(
        _path(root, filename, create=True),
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
    )


def load_source_comparison_inventory(
    root: Path, native: Mapping[str, Any], candidate_sha: str
) -> list[dict[str, Any]]:
    """Reconstruct the exact original inventory; never create missing evidence."""
    binding = _binding(native, candidate_sha)
    path = _path(root, "source-comparisons-" + canonical_hash(binding) + ".json")
    if not path.exists():
        return []  # Historical reviews did not attach comparison images.
    try:
        if path.stat().st_size > 65536:
            _invalid()
        value = json.loads(path.read_bytes())
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "binding", "sources", "manifest_sha256"}
            or value.get("version") != VERSION or value.get("binding") != binding
            or value.get("manifest_sha256") != canonical_hash({
                key: item for key, item in value.items() if key != "manifest_sha256"
            })
            or not isinstance(value.get("sources"), list)
            or not 1 <= len(value["sources"]) <= 13
        ):
            _invalid()
        inventory = []
        for index, row in enumerate(value["sources"]):
            if not isinstance(row, dict):
                _invalid()
            if (
                set(row) != {"asset_id", "role", "sha256", "filename", "byte_size"}
                or not valid_view(
                    row["asset_id"], row["role"], index, len(value["sources"])
                )
                or not _SHA.fullmatch(row["sha256"])
                or row["filename"] != "source-comparison-view-" + row["sha256"] + ".png"
                or type(row["byte_size"]) is not int
                or not 1 <= row["byte_size"] <= 20 * 1024 * 1024
            ):
                _invalid()
            image = _path(root, row["filename"])
            if (
                image.stat().st_size != row["byte_size"]
                or hashlib.sha256(image.read_bytes()).hexdigest() != row["sha256"]
            ):
                _invalid()
            inventory.append({key: row[key] for key in ("asset_id", "role", "sha256")})
        if len({row["asset_id"] for row in inventory}) != len(inventory):
            _invalid()
        return inventory
    except (KeyError, ValueError, TypeError, OSError):
        _invalid()
