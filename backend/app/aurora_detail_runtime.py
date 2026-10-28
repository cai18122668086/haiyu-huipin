# ruff: noqa: E501
"""Native Aurora domestic detail v4.3 Harness boundary.

The detail provider keeps the durable Polaris job model, but every storyboard
and generation transition is delegated to the bundled Skill Harness.  This
module contains only the subprocess boundary and the small adapter needed to
turn the existing, fact-grounded job payload into the v4.3 input contract.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import default_aurora_work_root, default_skill_root
from .domestic_detail.contracts import (
    normalize_native_plan_for_harness as normalize_native_plan_for_harness,
)
from .domestic_detail.plans import resolve_native_plan
from .skill_runtime import SkillManifestRegistry

DETAIL_SKILL_ID = "aurora-domestic-ecommerce-detail-page"
DETAIL_VERSION = "4.3.1"


class AuroraDetailHarnessError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, native_failure_kind: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.native_failure_kind = native_failure_kind


@dataclass(frozen=True)
class AuroraDetailHarnessRunner:
    skill_root: Path = default_skill_root(DETAIL_SKILL_ID)
    timeout_seconds: float = 90

    def run(
        self,
        command: str,
        workspace: Path,
        arguments: Mapping[str, str | Path],
    ) -> dict[str, Any]:
        try:
            SkillManifestRegistry.bundled().verify_source(
                DETAIL_SKILL_ID, self.skill_root
            )
        except Exception as exc:
            raise AuroraDetailHarnessError(
                "AURORA_DETAIL_SOURCE_MISMATCH", str(exc)
            ) from exc
        allowed = {
            "start": {"input", "session"},
            "lock-identity": {"session", "identity"},
            "resolve-conflicts": {"session", "resolution"},
            "compile-plan": {"session", "plan"},
            "preflight-plan": {"session", "plan"},
            "authorize": {
                "session",
                "approval-text",
                "approved-module-count",
                "approved-pair-canvas-aspect-ratio",
                "approval-document-sha256",
                "delivery-mode",
            },
            "generate-next": {"session", "out"},
            "record-generation": {"session", "token", "image"},
            "audit-pair": {"session", "judgment"},
            "inspect-pair-artifacts": {"session", "pair-id", "selection"},
            "inspect-pair-review-contracts": {"session", "pair-id"},
            "inspect-pair-review-evidence": {"session", "pair-id"},
            "inspect-final-manifest": {"session", "manifest"},
            "inspect-campaign-review-contract": {"session"},
            "fork-local-revision": {
                "session", "source-session", "source-session-sha256", "revision-intent",
            },
            "fork-campaign-review": {
                "session",
                "source-session",
                "source-session-sha256",
            },
            "prepare-repair": {"session", "repair-plan"},
            "assemble": {"session", "output"},
            "audit-long-page": {"session", "judgment"},
            "finalize": {"session", "output", "approval-text"},
            "auto-finalize": {"session", "output"},
            "inspect": {"session"},
        }.get(command)
        if allowed is None:
            raise AuroraDetailHarnessError("HARNESS_COMMAND_NOT_ALLOWED", command)
        if set(arguments) - allowed:
            raise AuroraDetailHarnessError(
                "HARNESS_ARGUMENT_NOT_ALLOWED",
                f"Unsupported arguments for {command}: {sorted(set(arguments) - allowed)}",
            )
        root = workspace.resolve()
        root.mkdir(parents=True, exist_ok=True)
        argv = [sys.executable, str(self.harness_path), command]
        for name, raw in arguments.items():
            value = str(raw)
            if name not in {
                "approval-text",
                "token",
                "delivery-mode",
                "approved-module-count",
                "approved-pair-canvas-aspect-ratio",
                "approval-document-sha256",
                "pair-id",
                "selection",
                "source-session-sha256",
            }:
                value = str(self._workspace_path(root, Path(value)))
            argv.extend((f"--{name}", value))
        try:
            completed = subprocess.run(
                argv,
                cwd=self.skill_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AuroraDetailHarnessError(
                "HARNESS_TIMEOUT", "Aurora detail Harness timed out"
            ) from exc
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            detail = completed.stderr.strip()[-1000:]
            native_kind = None
            try:
                error_payload = json.loads(completed.stderr)
                detail = str(error_payload.get("error") or detail)
                if error_payload.get("failure_kind") in {
                    "LOCAL_IO",
                    "CONTRACT_REJECTION",
                }:
                    native_kind = error_payload["failure_kind"]
            except json.JSONDecodeError:
                pass
            raise AuroraDetailHarnessError(
                "HARNESS_INVALID_OUTPUT",
                f"Aurora detail Harness did not return JSON (exit={completed.returncode}; stderr={detail})",
                native_failure_kind=native_kind,
            ) from exc
        if completed.returncode != 0 or result.get("status") == "BLOCKED":
            raise AuroraDetailHarnessError(
                "HARNESS_BLOCKED",
                str(
                    result.get("error")
                    or "Aurora detail Harness blocked the transition"
                ),
                native_failure_kind=(
                    result.get("failure_kind")
                    if result.get("failure_kind") in {"LOCAL_IO", "CONTRACT_REJECTION"}
                    else None
                ),
            )
        return result

    @property
    def harness_path(self) -> Path:
        path = (self.skill_root / "scripts" / "harness.py").resolve()
        if not path.is_file() or self.skill_root.resolve() not in path.parents:
            raise AuroraDetailHarnessError(
                "HARNESS_MISSING", "Aurora detail Harness is missing"
            )
        return path

    @staticmethod
    def _workspace_path(root: Path, value: Path) -> Path:
        candidate = value if value.is_absolute() else root / value
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise AuroraDetailHarnessError(
                "HARNESS_PATH_OUTSIDE_WORKSPACE",
                "Harness file arguments must remain inside the job workspace",
            )
        return resolved


def write_contract(path: Path, value: Mapping[str, Any] | Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def detail_workspace(job_id: str, *, root: Path | None = None) -> Path:
    if not job_id or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for char in job_id
    ):
        raise AuroraDetailHarnessError(
            "INVALID_JOB_ID", "Invalid detail job identifier"
        )
    base = (root or (default_aurora_work_root() / "domestic-detail")).resolve()
    workspace = (base / job_id).resolve()
    if base != workspace and base not in workspace.parents:
        raise AuroraDetailHarnessError(
            "WORKSPACE_ESCAPE", "Detail workspace escaped its root"
        )
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AuroraDetailHarnessError(
            "WORKSPACE_NOT_WRITABLE",
            "Aurora detail workspace is not writable (%s)."
            % (exc.strerror or type(exc).__name__),
        ) from exc
    return workspace


def _text_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def build_native_input(
    settings: Mapping[str, Any],
    *,
    product_images: Sequence[Mapping[str, Any]],
    image_paths: Sequence[str],
    intake: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize validated product/style provenance into the native input."""
    from copy import deepcopy

    from .domestic_detail.facts import validate_intake

    sources = [
        {"asset_id": image["id"], "role": image["role"], "sha256": image["sha256"]}
        for image in product_images
    ]
    validated = validate_intake(
        intake,
        settings=settings,
        sources=sources,
        brandless_confirmation=intake.get("brandless_confirmation")
        or settings.get("brandless_confirmation"),
        resolution=intake.get("resolution"),
    )
    if validated.get("brand_confirmation_required") or "identity" not in validated:
        raise AuroraDetailHarnessError(
            "NATIVE_DETAIL_INTAKE_BLOCKED",
            "Source or brand confirmation is still required",
        )
    path_map = {
        str(item["id"]): str(path)
        for item, path in zip(product_images, image_paths, strict=True)
    }
    references = deepcopy(validated["reference_materials"])
    for reference in references:
        reference["path"] = path_map[reference["id"]]
    platform = settings.get("platform_profile")
    platform = dict(platform) if isinstance(platform, Mapping) else {}
    size = str(settings.get("size") or "1440x1920").split("x")
    width, height = int(size[0]), int(size[1])
    profile = {
        "target_platform": str(
            platform.get("target_platform")
            or settings.get("platform_code")
            or "taobao_tmall"
        ),
        "profile_name": str(platform.get("profile_name") or "INTERNAL_REVIEW_3_4"),
        "selection_mode": "USER_SPECIFIED",
        "selection_rationale": "采用已在网页声明、待完整审批确认的移动端详情单屏规格，按相邻双屏固定切片并保持连续阅读。",
        "source": "INTERNAL_REVIEW_PROFILE",
        "aspect_ratio": {"width_units": 3, "height_units": 4, "label": "3:4"},
        "canvas_width_px": width,
        "module_height_px": height,
        "safe_margin_px": int(platform.get("safe_margin_px") or min(96, width // 15)),
        "slice_height_px": height,
        "format": "PNG",
        "max_file_bytes": None,
        "verification_status": "INTERNAL_ONLY",
    }
    user_facts = validated["user_facts"]
    points = [
        {
            "id": f"USP{index:02d}",
            "text": fact["text"],
            "source_ref": fact["evidence_ref"],
            "fact_ids": [fact["id"]],
        }
        for index, fact in enumerate(
            (
                fact
                for fact in user_facts
                if fact["evidence_ref"].startswith("settings/selling_points/")
            ),
            start=1,
        )
    ]
    identity = validated["identity"]
    value = {
        "intake_route": "USER_SELLING_POINT_LED"
        if points
        else "PHOTO_TO_PRODUCT_STRATEGY",
        "product_name": str(settings.get("product_name") or identity["canonical_name"]),
        "category": identity["category"],
        "target_buyer": str(
            settings.get("target_buyer")
            or validated.get("target_buyer")
            or "了解当前商品特征的购买者"
        ),
        "primary_scenario": str(validated.get("primary_scenario") or ""),
        "product_images": [
            {
                "id": str(item["id"]),
                "path": path_map[str(item["id"])],
                "role": "product_reference",
            }
            for item in product_images
            if item["role"] == "product_reference"
        ],
        "verified_facts": [*user_facts, *validated["observed_facts"]],
        "user_supplied_selling_points": points,
        "conflict_scan": validated["conflict_scan"],
        "source_conflicts": validated["source_conflicts"],
        "reference_materials": references,
        "platform_profile": profile,
        "coverage_directive": "MAXIMIZE_USER_SUPPLIED_RELEVANT_FACTS",
        "question_budget": 0,
        "conflict_policy": "BLOCK_PLAN_AND_BATCH_ASK",
    }
    if settings.get("website_contract_version") == "WEBSITE_DIRECT_V2":
        value["website_contract_version"] = "WEBSITE_DIRECT_V2"
        value["identity_observations"] = deepcopy(validated["identity_observations"])
    # Direct website plans carry the authored creative brief. Keep optional
    # legacy sales ledgers in the original intake receipt, without activating
    # their unrelated atomic-fact/depth gates in the native render input.
    if (
        settings.get("website_contract_version") != "WEBSITE_DIRECT_V2"
        and validated.get("operating_analysis") is not None
    ):
        value["operating_analysis"] = deepcopy(validated["operating_analysis"])
    return value


def build_native_identity(
    settings: Mapping[str, Any],
    product_image_ids: Sequence[str],
    *,
    intake: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the actual validated observation contract; never synthesize geometry."""
    from copy import deepcopy

    identity = intake.get("identity")
    if not isinstance(identity, Mapping) or not set(
        identity.get("reference_image_ids") or []
    ).issubset(set(product_image_ids)):
        raise AuroraDetailHarnessError(
            "NATIVE_DETAIL_IDENTITY_MISSING",
            "A product-only observed identity is required",
        )
    return deepcopy(dict(identity))


def extract_native_plan(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    return resolve_native_plan(payload)


def source_path_for(workspace: Path, index: int, filename: str, payload: bytes) -> Path:
    suffix = Path(filename).suffix.lower() or ".bin"
    path = workspace / "references" / f"{index:02d}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if (
        not path.exists()
        or hashlib.sha256(path.read_bytes()).hexdigest()
        != hashlib.sha256(payload).hexdigest()
    ):
        path.write_bytes(payload)
    return path
