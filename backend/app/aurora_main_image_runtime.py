"""Narrow subprocess boundary for the bundled Aurora six-image Harness.

The web application never imports or invokes the Skill's runtime helpers directly.
Every state transition goes through ``scripts/harness.py`` with an argv allowlist,
no shell, a fixed interpreter, and paths constrained to a per-job workspace.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from .config import default_aurora_v20_skill_root, default_skill_root

AURORA_MAIN_IMAGE_SKILL_ID = "aurora-domestic-ecommerce-main-image"
AURORA_MAIN_IMAGE_VERSION = "0.1.17"
AURORA_MAIN_IMAGE_V20_VERSION = "2.0.0"


class AuroraHarnessError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_COMMAND_ARGUMENTS: dict[str, frozenset[str]] = {
    "start": frozenset({"input", "session"}),
    "lock-identity": frozenset({"session", "identity"}),
    "compile": frozenset({"session", "plan"}),
    "compile-v013": frozenset(
        {"session", "input", "identity", "source-manifest", "output"}
    ),
    "replan": frozenset({"session", "plan", "reason"}),
    "restart-campaign": frozenset({"session", "reason"}),
    "authorize": frozenset({"session", "approval-text"}),
    "generate-next": frozenset({"session", "out"}),
    "record-generation": frozenset({"session", "token", "image"}),
    "audit-current": frozenset({"session", "judgment"}),
    "calibrate-search-baseline": frozenset({"session", "observation"}),
    "audit-set-role-distinction": frozenset({"session", "observation"}),
    "prepare-repair": frozenset({"session", "repair-plan"}),
    "apply-user-adjustment": frozenset({"session", "adjustment"}),
    "calibrate-style": frozenset({"session", "observation"}),
    "finalize": frozenset({"session", "output", "approval-text"}),
    "inspect": frozenset({"session"}),
}


@dataclass(frozen=True)
class AuroraHarnessRunner:
    skill_root: Path = default_skill_root(AURORA_MAIN_IMAGE_SKILL_ID)
    timeout_seconds: float = 45
    command_arguments: ClassVar[Mapping[str, frozenset[str]]] = _COMMAND_ARGUMENTS

    def run(
        self,
        command: str,
        workspace: Path,
        arguments: Mapping[str, str | Path],
    ) -> dict[str, Any]:
        allowed = self.command_arguments.get(command)
        if allowed is None:
            raise AuroraHarnessError("HARNESS_COMMAND_NOT_ALLOWED", command)
        unexpected = set(arguments) - allowed
        if unexpected:
            raise AuroraHarnessError(
                "HARNESS_ARGUMENT_NOT_ALLOWED",
                f"Unsupported arguments for {command}: {sorted(unexpected)}",
            )
        root = workspace.resolve()
        root.mkdir(parents=True, exist_ok=True)
        argv: list[str] = [sys.executable, str(self.harness_path), command]
        for name, raw_value in arguments.items():
            value = str(raw_value)
            if name not in {"reason", "approval-text", "token"}:
                value = str(self._workspace_path(root, Path(value)))
            argv.extend((f"--{name}", value))
        try:
            completed = subprocess.run(
                argv,
                cwd=self.skill_root,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AuroraHarnessError(
                "HARNESS_TIMEOUT", "Aurora Harness timed out"
            ) from exc
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            diagnostic = completed.stderr.strip()
            if len(diagnostic) > 1000:
                diagnostic = diagnostic[-1000:]
            detail = f" (exit={completed.returncode}"
            if diagnostic:
                detail += f"; stderr={diagnostic}"
            detail += ")"
            raise AuroraHarnessError(
                "HARNESS_INVALID_OUTPUT",
                f"Aurora Harness did not return JSON{detail}",
            ) from exc
        if completed.returncode != 0 or result.get("status") == "BLOCKED":
            message = str(
                result.get("error") or "Aurora Harness blocked the transition"
            )
            raise AuroraHarnessError("HARNESS_BLOCKED", message)
        return result

    @property
    def harness_path(self) -> Path:
        path = (self.skill_root / "scripts" / "harness.py").resolve()
        if not path.is_file() or self.skill_root.resolve() not in path.parents:
            raise AuroraHarnessError(
                "HARNESS_MISSING", "Bundled Aurora Harness is missing"
            )
        return path

    @staticmethod
    def _workspace_path(root: Path, value: Path) -> Path:
        candidate = value if value.is_absolute() else root / value
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise AuroraHarnessError(
                "HARNESS_PATH_OUTSIDE_WORKSPACE",
                "Harness file arguments must remain inside the job workspace",
            )
        return resolved


@dataclass(frozen=True)
class AuroraV20HarnessRunner(AuroraHarnessRunner):
    """Subprocess boundary for the unmodified Aurora v2.0 Skill tree.

    The v2.0 command names are intentionally kept separate from the legacy
    compatibility aliases above.  This prevents v0.1.17/v013 command routing
    from silently changing the native v2.0 protocol.
    """

    skill_root: Path = default_aurora_v20_skill_root()
    command_arguments: ClassVar[Mapping[str, frozenset[str]]] = {
        "start": frozenset({"input", "session"}),
        "lock-identity": frozenset({"session", "identity"}),
        "compile": frozenset({"session", "plan"}),
        "authorize": frozenset({"session", "approval-text"}),
        "generate-next": frozenset({"session", "out"}),
        "record-generation": frozenset({"session", "token", "image"}),
        "recover-generation": frozenset({"session", "token", "generated-dir"}),
        "audit-current": frozenset({"session", "judgment"}),
        "prepare-repair": frozenset({"session", "repair-plan"}),
        "calibrate-search": frozenset({"session", "observation"}),
        "calibrate-style": frozenset({"session", "observation"}),
        "apply-user-adjustment": frozenset({"session", "adjustment"}),
        "audit-set": frozenset({"session", "observation"}),
        "finalize": frozenset({"session", "output", "approval-text"}),
        "restart-campaign": frozenset({"session", "reason"}),
        "inspect": frozenset({"session"}),
    }

    def run(
        self,
        command: str,
        workspace: Path,
        arguments: Mapping[str, str | Path],
    ) -> dict[str, Any]:
        verify_aurora_v20_source(self.skill_root)
        command_arguments = self.command_arguments.get(command)
        if command_arguments is None:
            raise AuroraHarnessError("HARNESS_COMMAND_NOT_ALLOWED", command)
        unexpected = set(arguments) - command_arguments
        if unexpected:
            raise AuroraHarnessError(
                "HARNESS_ARGUMENT_NOT_ALLOWED",
                f"Unsupported arguments for {command}: {sorted(unexpected)}",
            )
        return super().run(command, workspace, arguments)


def write_contract(path: Path, value: Mapping[str, Any] | Sequence[Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def reset_v20_pending_generation_for_retry(
    session: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an authorized v2 session after a transient unbound provider call.

    The native Skill intentionally remains in ``AWAITING_GENERATED_ASSET``
    after ``generate-next`` until an image is recorded.  The API's bounded
    transient retry is a new attempt scope; when no artifact was returned, it
    must clear that unbound token before issuing the next native request.
    """

    reset = dict(session)
    reset["pending_generation"] = None
    reset["active_page_index"] = None
    reset["state"] = "AUTHORIZED"
    current_authorization = session.get("authorization")
    authorization = dict(
        current_authorization if isinstance(current_authorization, Mapping) else {}
    )
    authorization["retry_after_provider_failure"] = True
    reset["authorization"] = authorization
    return reset


def verify_aurora_v20_source(skill_root: Path | None = None) -> dict[str, Any]:
    """Fail closed if any imported v2.0 source file changes or disappears."""

    root = (skill_root or default_aurora_v20_skill_root()).resolve()
    manifest_path = (
        Path(__file__).resolve().parent.parent
        / "source_manifests"
        / "aurora-domestic-ecommerce-main-image-v20-source.json"
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuroraHarnessError(
            "AURORA_V20_MANIFEST_INVALID", "Aurora v2.0 source manifest is invalid"
        ) from exc
    entries = manifest.get("files")
    if manifest.get("source_file_count") != 24 or not isinstance(entries, list):
        raise AuroraHarnessError(
            "AURORA_V20_MANIFEST_INVALID", "Aurora v2.0 source manifest count changed"
        )
    if len(entries) != 24:
        raise AuroraHarnessError(
            "AURORA_V20_MANIFEST_INVALID", "Aurora v2.0 source list changed"
        )
    for entry in entries:
        expected, separator, relative = str(entry).partition("  ")
        path = (root / relative).resolve()
        if not separator or root not in path.parents or not path.is_file():
            raise AuroraHarnessError(
                "AURORA_V20_SOURCE_MISMATCH", f"Missing Aurora v2.0 source: {relative}"
            )
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise AuroraHarnessError(
                "AURORA_V20_SOURCE_MISMATCH", f"Changed Aurora v2.0 source: {relative}"
            )
    return manifest
