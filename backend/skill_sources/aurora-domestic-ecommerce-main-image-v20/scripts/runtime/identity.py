from __future__ import annotations

from pathlib import Path
from typing import Any

from core import HarnessError, reference_paths, require, validate_schema


def validate_identity(identity: dict[str, Any], raw: dict[str, Any]) -> None:
    validate_schema(identity, "identity.schema.json")
    require(identity["request_id"] == raw["request_id"], "identity request_id mismatch")
    allowed_paths = {str(Path(path).resolve()).casefold() for path in reference_paths(raw)}
    seen: set[str] = set()
    for item in identity["evidence"]:
        require(item["evidence_id"] not in seen, f"duplicate evidence_id {item['evidence_id']}")
        seen.add(item["evidence_id"])
        resolved = str(Path(item["path"]).resolve()).casefold()
        require(resolved in allowed_paths, f"identity evidence is not an original reference: {item['path']}")
        require(Path(item["path"]).is_file(), f"identity evidence file is missing: {item['path']}")
    if not seen:
        raise HarnessError("identity requires evidence")
