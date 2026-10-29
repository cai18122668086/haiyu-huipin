from __future__ import annotations

import hashlib
import json
import os
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = "2.0.0"
SESSION_SCHEMA_VERSION = "aurora-native-fast-session-v1"
ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
PAGE_ROLES = [
    "SEARCH_MAIN_IMAGE_LISTING_COVER",
    "CAROUSEL_IDENTITY_HERO",
    "CAROUSEL_CORE_FEATURE_PROOF",
    "CAROUSEL_SCENE_OR_OPERATION",
    "CAROUSEL_DETAIL_OR_TRUST",
    "CAROUSEL_OFFER_OR_SPEC_CLOSURE",
]
IDENTITY_SENSITIVE_PAGES = {0, 1, 3, 5}
CHECK_NAMES = [
    "product_identity_match",
    "factual_copy_match",
    "typography_style_match",
    "typography_legibility",
    "page_role_expression",
    "evidence_relation_visible",
    "campaign_style_match",
    "headline_treatment_match",
    "palette_role_match",
    "graphic_language_match",
    "photography_finish_match",
    "information_surface_match",
    "layout_not_cloned",
    "set_diversity_match",
    "product_separation",
    "numeric_hierarchy_if_applicable",
    "design_intensity_controlled",
    "visual_performance_match",
    "baseline_performance_match",
    "approved_plan_fidelity_match",
    "campaign_constitution_match",
    "source_background_noise_absent",
    "selling_point_visible_without_copy",
    "selling_point_visual_dominance",
    "claim_to_evidence_relation_clear",
    "text_not_substituting_for_visual_proof",
    "thumbnail_success",
    "no_unsupported_claim",
    "no_hard_error",
]
HARD_CHECKS = {
    "product_identity_match",
    "factual_copy_match",
    "source_background_noise_absent",
    "no_unsupported_claim",
    "no_hard_error",
}
MATERIAL_CHECKS = HARD_CHECKS | {
    "typography_legibility",
    "page_role_expression",
    "evidence_relation_visible",
    "visual_performance_match",
    "baseline_performance_match",
    "approved_plan_fidelity_match",
    "campaign_constitution_match",
    "selling_point_visible_without_copy",
    "selling_point_visual_dominance",
    "claim_to_evidence_relation_clear",
    "text_not_substituting_for_visual_proof",
    "thumbnail_success",
    "layout_not_cloned",
}
STYLE_CHECKS = {"typography_style_match", "campaign_style_match", "headline_treatment_match", "palette_role_match", "graphic_language_match", "photography_finish_match", "information_surface_match"}
REBUILD_CHECKS = {"product_identity_match", "no_hard_error"}


class HarnessError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise HarnessError(message)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_digest(path: str | Path) -> str:
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot load JSON {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def _validate(value: Any, schema: dict[str, Any], path: str) -> None:
    if "const" in schema:
        require(value == schema["const"], f"{path} must equal {schema['const']!r}")
    if "enum" in schema:
        require(value in schema["enum"], f"{path} has an invalid value")
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        require(any(_matches_type(value, item) for item in types), f"{path} must be {' or '.join(types)}")
    if isinstance(value, str):
        require(len(value) >= schema.get("minLength", 0), f"{path} is too short")
        if "maxLength" in schema:
            require(len(value) <= schema["maxLength"], f"{path} is too long")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            require(value >= schema["minimum"], f"{path} is below minimum")
        if "maximum" in schema:
            require(value <= schema["maximum"], f"{path} is above maximum")
    if isinstance(value, list):
        require(len(value) >= schema.get("minItems", 0), f"{path} has too few items")
        if "maxItems" in schema:
            require(len(value) <= schema["maxItems"], f"{path} has too many items")
        if schema.get("uniqueItems"):
            require(len({canonical(item) for item in value}) == len(value), f"{path} contains duplicates")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            require(name in value, f"{path}.{name} is required")
        properties = schema.get("properties", {})
        for name, item in value.items():
            if name in properties:
                _validate(item, properties[name], f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise HarnessError(f"{path}.{name} is not allowed")


def validate_schema(value: dict[str, Any], schema_name: str) -> None:
    schema = load_json(SCHEMAS / schema_name)
    _validate(value, schema, "$" )


def reference_paths(raw: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in raw.get("product", {}).get("original_references", []):
        if isinstance(item, str):
            path = item.strip()
        elif isinstance(item, dict):
            path = next((str(item.get(key, "")).strip() for key in ("path", "image_path", "file") if str(item.get(key, "")).strip()), "")
        else:
            path = ""
        require(bool(path), "every original reference needs a path")
        if path not in result:
            result.append(path)
    return result


def fact_records(raw: dict[str, Any]) -> list[dict[str, str]]:
    product = raw.get("product", {})
    result: list[dict[str, str]] = []
    for source in (
        "verified_selling_points", "user_provided_display_facts",
        "verified_attributes", "verified_scenes", "verified_offers",
        "verified_service_or_trust_facts",
    ):
        value = product.get(source, [])
        values = [f"{key}：{item}" for key, item in value.items()] if isinstance(value, dict) else value
        if not isinstance(values, list):
            continue
        for item in values:
            text = str(item).strip()
            if text and all(existing["text"] != text for existing in result):
                result.append({"text": text, "source": source})
    return result


def record(session: dict[str, Any], action: str, previous_state: str) -> None:
    session.setdefault("history", []).append({"at": now(), "action": action, "from": previous_state, "to": session["state"]})
    session["updated_at"] = now()


def transition(session: dict[str, Any], state: str, action: str) -> None:
    previous = session["state"]
    session["state"] = state
    record(session, action, previous)


def require_state(session: dict[str, Any], *states: str) -> None:
    require(session.get("state") in states, f"state {session.get('state')} does not allow this action")


def image_dimensions(path: str | Path) -> tuple[int, int]:
    data = Path(path).read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8\xff"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            length = int.from_bytes(data[offset:offset + 2], "big")
            require(length >= 2 and offset + length <= len(data), "invalid JPEG structure")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                return int.from_bytes(data[offset + 5:offset + 7], "big"), int.from_bytes(data[offset + 3:offset + 5], "big")
            offset += length
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if kind == b"VP8L" and data[20] == 0x2F:
            b1, b2, b3, b4 = data[21:25]
            return 1 + (b1 | ((b2 & 0x3F) << 8)), 1 + ((b2 >> 6) | (b3 << 2) | ((b4 & 0x0F) << 10))
        if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    raise HarnessError(f"unsupported or unreadable image: {path}")


def selected_version(session: dict[str, Any], page_index: int) -> dict[str, Any] | None:
    versions = [item for item in session.get("generated_versions", []) if item["page_index"] == page_index and item.get("status") == "GENERATED_NOT_REVIEWED"]
    return versions[-1] if versions else None
