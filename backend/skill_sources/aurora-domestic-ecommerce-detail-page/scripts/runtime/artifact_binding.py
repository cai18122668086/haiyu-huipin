from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from .state import HarnessError, require_fields


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifact_binding(judgment: dict, artifact_path: str | Path, label: str) -> dict:
    binding = judgment.get("artifact_binding")
    if not isinstance(binding, dict):
        raise HarnessError(f"{label} requires artifact_binding")
    require_fields(
        binding,
        ("image_sha256", "auditor", "audited_at", "ocr_text", "visual_inspection_output"),
        f"{label} artifact binding",
    )
    expected = file_sha256(artifact_path)
    if binding["image_sha256"].lower() != expected:
        raise HarnessError(f"{label} image_sha256 does not match the audited raster")
    try:
        datetime.fromisoformat(str(binding["audited_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HarnessError(f"{label} audited_at must be an ISO-8601 timestamp") from exc
    if not isinstance(binding["ocr_text"], list):
        raise HarnessError(f"{label} artifact_binding.ocr_text must be a list")
    visual = binding["visual_inspection_output"]
    if not binding["ocr_text"] and visual in (None, "", {}, []):
        raise HarnessError(f"{label} must bind OCR text or a visual-inspection output")
    return binding
