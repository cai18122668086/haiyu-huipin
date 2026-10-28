from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


STATES = {
    "AWAITING_CONFLICT_RESOLUTION",
    "AWAITING_IDENTITY",
    "IDENTITY_LOCKED",
    "AWAITING_PLAN_APPROVAL",
    "AUTHORIZED",
    "AWAITING_GENERATED_MODULE",
    "AWAITING_PIXEL_AUDIT",
    "REPAIR_REQUIRED",
    "REPAIR_EXHAUSTED",
    "AWAITING_ASSEMBLY",
    "AWAITING_LONG_PAGE_AUDIT",
    "AWAITING_USER_FINAL_REVIEW",
    "AUTO_FINALIZE_AUTHORIZED",
    "FINALIZED",
}


class HarnessError(RuntimeError):
    pass


def load_json(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise HarnessError(f"Expected a JSON object: {path}")
    return value


def atomic_write_json(path: str | Path, value: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: str | Path, value: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value.rstrip() + "\n")
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def require_state(session: dict, *allowed: str) -> None:
    state = session.get("state")
    if state not in allowed:
        raise HarnessError(f"Illegal state {state!r}; expected one of {', '.join(allowed)}")


def require_fields(value: dict, fields: tuple[str, ...], label: str) -> None:
    # Empty arrays are valid for optional-result fields such as material_failures,
    # unauthorized_ocr_text, and residual_issues on a passing audit.
    missing = [field for field in fields if value.get(field) in (None, "")]
    if missing:
        raise HarnessError(f"{label} missing required fields: {', '.join(missing)}")


def current_module(session: dict) -> dict:
    modules = session.get("modules") or []
    index = session.get("current_index", 0)
    if not 0 <= index < len(modules):
        raise HarnessError("No current module")
    return modules[index]


def allowed_actions(state: str) -> list[str]:
    return {
        "AWAITING_CONFLICT_RESOLUTION": ["resolve-conflicts", "inspect"],
        "AWAITING_IDENTITY": ["lock-identity", "inspect"],
        "IDENTITY_LOCKED": ["compile-plan", "inspect"],
        "AWAITING_PLAN_APPROVAL": ["authorize", "inspect"],
        "AUTHORIZED": ["generate-next", "apply-user-adjustment"],
        "AWAITING_GENERATED_MODULE": ["record-generation", "record-generation-batch"],
        "AWAITING_PIXEL_AUDIT": ["audit-pair", "inspect"],
        "REPAIR_REQUIRED": ["prepare-repair", "inspect"],
        "REPAIR_EXHAUSTED": ["apply-user-adjustment", "inspect"],
        "AWAITING_ASSEMBLY": ["assemble", "apply-user-adjustment"],
        "AWAITING_LONG_PAGE_AUDIT": ["audit-long-page"],
        "AWAITING_USER_FINAL_REVIEW": ["finalize", "assemble", "audit-long-page", "apply-user-adjustment"],
        "AUTO_FINALIZE_AUTHORIZED": ["auto-finalize", "assemble", "audit-long-page", "apply-user-adjustment"],
        "FINALIZED": ["inspect", "apply-user-adjustment"],
    }.get(state, [])


