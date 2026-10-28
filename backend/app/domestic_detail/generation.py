"""Immutable domestic detail generation scope and durable invocation receipts.

A receipt created before transport is an uncertain call until its completion is
persisted. Reopening it never grants permission to invoke the provider again.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from .contracts import normalize_native_plan_for_harness
from .plans import canonical_hash, validate_approval_binding, validate_plan_views

PIPELINE_VERSION = "aurora-domestic-detail-v4.3.1"
BINDING_VERSION = "domestic-detail-generation-v1"


class DetailGenerationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def current_pipeline(configuration: Mapping[str, Any]) -> bool:
    return configuration.get("domestic_detail_pipeline") == PIPELINE_VERSION


def require_current_pipeline(configuration: Mapping[str, Any]) -> None:
    if not current_pipeline(configuration):
        raise DetailGenerationError(
            "DOMESTIC_DETAIL_LEGACY_RUNTIME_REQUIRED",
            (
                "This historical task requires its pinned runtime; it cannot be "
                "migrated automatically."
            ),
        )


def runtime_source_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = [
        *sorted((root / "domestic_detail").glob("*.py")),
        *[
            root / name
            for name in (
                "aurora_detail_runtime.py",
                "xinghe_agent.py",
                "execution.py",
                "routes_v1.py",
                "domestic_detail_provider.py",
            )
        ],
    ]
    return canonical_hash(
        {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        }
    )


def generation_binding(
    payload: Mapping[str, Any],
    settings: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    *,
    group_key: str,
    job_input: Mapping[str, Any],
) -> dict[str, Any]:
    validate_approval_binding(payload, settings)
    aurora = payload["aurora_v4_3"]
    receipt = aurora.get("approval_receipt")
    if (
        not isinstance(receipt, Mapping)
        or not isinstance(receipt.get("approval_text"), str)
        or len(receipt["approval_text"].strip()) < 2
        or receipt.get("approval_document_sha256") != aurora["approval_document_sha256"]
        or receipt.get("contract_binding_sha256")
        != aurora["contract_binding"]["sha256"]
        or receipt.get("pipeline") != PIPELINE_VERSION
        or receipt.get("approval_protocol") != payload["native_approval"]["version"]
        or receipt.get("native_approval_document_sha256")
        != payload["native_approval"]["document_sha256"]
    ):
        raise DetailGenerationError(
            "NATIVE_DETAIL_APPROVAL_RECEIPT_INVALID",
            "The exact user approval receipt is missing or stale",
        )
    from .delivery_authorization import receipt_authorization

    try:
        receipt_authorization(payload)
    except ValueError as exc:
        raise DetailGenerationError(
            "NATIVE_DETAIL_AUTO_DELIVERY_AUTHORIZATION_INVALID", str(exc)
        ) from exc
    assets = []
    for source in sources:
        asset = {
            key: source.get(key) for key in ("asset_id", "role", "sha256", "filename")
        }
        if (
            not all(isinstance(value, str) and value for value in asset.values())
            or len(str(asset["sha256"])) != 64
        ):
            raise DetailGenerationError(
                "NATIVE_DETAIL_ASSET_BINDING_INVALID",
                "Every source needs its original identity, role and byte hash",
            )
        assets.append(asset)
    approved_groups = [
        group
        for group in payload["source_asset_manifest"]
        if group["group_key"] == group_key
    ]
    if len(approved_groups) != 1 or approved_groups[0]["assets"] != assets:
        raise DetailGenerationError(
            "NATIVE_DETAIL_APPROVED_ASSETS_CHANGED",
            "The ordered source assets differ from the approved manifest",
        )
    if canonical_hash(job_input) != payload["source_input_sha256"]:
        raise DetailGenerationError(
            "NATIVE_DETAIL_APPROVED_INPUT_CHANGED",
            "Product groups or references differ from the approved input",
        )
    value = {
        "version": BINDING_VERSION,
        "pipeline": PIPELINE_VERSION,
        "runtime_source_sha256": runtime_source_hash(),
        "approved_scope": aurora["contract_binding"],
        "approval_receipt_sha256": canonical_hash(receipt),
        "normalized_plan_sha256": canonical_hash(
            normalize_native_plan_for_harness(validate_plan_views(payload))
        ),
        "approval_document_sha256": aurora["approval_document_sha256"],
        "source_assets": assets,
    }
    current = {**value, "sha256": canonical_hash(value)}
    if settings.get("website_contract_version") == "WEBSITE_DIRECT_V2":
        from ..aurora_detail_runtime import detail_workspace

        origin = payload["native_approval"]["source_job_id"]
        return original_retry_scope(current, detail_workspace(origin))
    return current


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DetailGenerationError(
            "NATIVE_DETAIL_RECEIPT_UNREADABLE",
            "Generation evidence is missing or unreadable",
        ) from exc
    if not isinstance(value, dict):
        raise DetailGenerationError(
            "NATIVE_DETAIL_BINDING_INVALID", "Generation evidence must be an object"
        )
    return value


def _create(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def bind_workspace(
    workspace: Path, binding: Mapping[str, Any], *, create: bool = False
) -> None:
    path = workspace / "generation-binding.json"
    if not path.exists() and create:
        with suppress(FileExistsError):
            _create(path, binding)
    if _read(path) != binding:
        raise DetailGenerationError(
            "NATIVE_DETAIL_BINDING_CHANGED",
            (
                "Runtime, Skill, approval, input or source bytes differ from this "
                "checkpoint"
            ),
        )
    plan_path = workspace / "plan.json"
    if plan_path.exists() and binding.get("normalized_plan_sha256") != canonical_hash(
        _read(plan_path)
    ):
        raise DetailGenerationError(
            "NATIVE_DETAIL_PLAN_CHANGED",
            "The compiled plan differs from the approved native plan",
        )


def original_retry_scope(
    current: Mapping[str, Any], source_workspace: Path
) -> dict[str, Any]:
    """Reuse an approved website authority across an explicit failed retry.

    Runtime fingerprints are historical provenance. Approval, plan, ordered
    sources and all other scope fields still match exactly; new invocations
    separately record the code that actually executes them.
    """
    path = source_workspace / "generation-binding.json"
    if not path.exists():
        return dict(current)
    original = _read(path)
    ignore = {"sha256", "runtime_source_sha256"}
    if {k: v for k, v in current.items() if k not in ignore} != {
        k: v for k, v in original.items() if k not in ignore
    }:
        return dict(current)
    if original.get("sha256") != canonical_hash(
        {k: v for k, v in original.items() if k != "sha256"}
    ):
        raise DetailGenerationError(
            "NATIVE_DETAIL_BINDING_INVALID", "Original authority hash changed"
        )
    bind_workspace(source_workspace, original)
    return original


def checkpoint_workspaces(workspace: Path, binding: Mapping[str, Any]) -> list[Path]:
    """Resolve paid pixels through verified failed-retry checkpoint ancestry."""
    roots: list[Path] = []
    current = workspace
    while True:
        if current in roots:
            raise DetailGenerationError(
                "NATIVE_DETAIL_BINDING_INVALID", "Checkpoint ancestry contains a cycle"
            )
        bind_workspace(current, binding)
        roots.append(current)
        state = _read(current / "session.json")
        source_id = state.get("checkpoint_source_job_id")
        if not source_id:
            return roots
        if (
            not isinstance(source_id, str)
            or not source_id.startswith("job_")
            or any(
                not (char.isascii() and (char.isalnum() or char in "_-"))
                for char in source_id
            )
        ):
            raise DetailGenerationError(
                "NATIVE_DETAIL_BINDING_INVALID", "Invalid checkpoint source owner"
            )
        origin = current.parent / source_id
        if (
            origin.is_symlink()
            or origin.is_junction()
            or origin.resolve().parent != workspace.resolve().parent
        ):
            raise DetailGenerationError(
                "NATIVE_DETAIL_BINDING_INVALID",
                "Checkpoint source escaped the job workspace",
            )
        current = origin


def begin_invocation(
    workspace: Path,
    binding: Mapping[str, Any],
    native_request: Mapping[str, Any],
    *,
    provider: str,
    model: str,
    pricing_version: str,
    idempotency_key: str,
    prompt: str,
    size: str,
    quality: str,
    sources: Sequence[Mapping[str, Any]],
) -> Path:
    bind_workspace(workspace, binding)
    token = native_request.get("token")
    if not isinstance(token, str) or not token:
        raise DetailGenerationError(
            "NATIVE_DETAIL_TOKEN_MISSING", "A signed native request is required"
        )
    path = (
        workspace
        / "generation-calls"
        / (hashlib.sha256(token.encode()).hexdigest() + ".json")
    )
    envelope = {
        "version": BINDING_VERSION,
        "binding_sha256": binding["sha256"],
        "execution_runtime_source_sha256": runtime_source_hash(),
        "pair_id": native_request.get("pair_id"),
        "native_request_sha256": canonical_hash(native_request),
        "native_request": dict(native_request),
        "provider": provider,
        "model": model,
        "pricing_version": pricing_version,
        "idempotency_key_sha256": hashlib.sha256(idempotency_key.encode()).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "size": size,
        "quality": quality,
        "source_assets": list(sources),
    }
    try:
        _create(
            path,
            {
                **envelope,
                "request_sha256": canonical_hash(envelope),
                "outcome": "outcome_unknown",
            },
        )
    except FileExistsError as exc:
        raise DetailGenerationError(
            "NATIVE_DETAIL_CALL_ALREADY_DISPATCHED",
            (
                "This signed request may already have incurred a charge; "
                "reconcile its original result"
            ),
        ) from exc
    return path


def finish_invocation(
    path: Path,
    *,
    outcome: str,
    provider_request_id: str | None,
    artifact_sha256: str | None,
    artifact_payload: bytes | None = None,
) -> None:
    receipt = _read(path)
    if artifact_payload is not None:
        if hashlib.sha256(artifact_payload).hexdigest() != artifact_sha256:
            raise DetailGenerationError(
                "NATIVE_DETAIL_ARTIFACT_HASH_INVALID",
                "Candidate bytes differ from their provider receipt",
            )
        with path.with_suffix(".artifact").open("xb") as stream:
            stream.write(artifact_payload)
            stream.flush()
            os.fsync(stream.fileno())
    completion = path.with_suffix(".completion.json")
    _create(
        completion,
        {
            "request_sha256": receipt["request_sha256"],
            "outcome": outcome,
            "provider_request_id": provider_request_id,
            "artifact_sha256": artifact_sha256,
        },
    )


def verify_original_invocation(
    workspaces: Sequence[Path],
    binding: Mapping[str, Any],
    *,
    pair_id: str,
    artifact_sha256: str,
    token: str | None = None,
    signed_request_sha256: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Verify the original paid response, before any native normalization."""
    for workspace in workspaces:
        bind_workspace(workspace, binding)
        for path in (workspace / "generation-calls").glob("*.json"):
            if path.name.endswith(".completion.json"):
                continue
            receipt = _read(path)
            unsigned = {
                key: value
                for key, value in receipt.items()
                if key not in {"request_sha256", "outcome"}
            }
            if receipt.get("request_sha256") != canonical_hash(unsigned):
                raise DetailGenerationError(
                    "NATIVE_DETAIL_REQUEST_RECEIPT_INVALID",
                    "The original invocation receipt changed",
                )
            if (
                receipt.get("binding_sha256") != binding["sha256"]
                or receipt.get("pair_id") != pair_id
            ):
                continue
            native = receipt.get("native_request") or {}
            if receipt.get("native_request_sha256") != canonical_hash(native):
                raise DetailGenerationError(
                    "NATIVE_DETAIL_REQUEST_RECEIPT_INVALID",
                    "Original native request envelope changed",
                )
            if (token is not None and native.get("token") != token) or (
                signed_request_sha256 is not None
                and native.get("signed_request_sha256") != signed_request_sha256
            ):
                continue
            completion = path.with_suffix(".completion.json")
            if not completion.exists():
                continue
            result = _read(completion)
            if (
                result.get("request_sha256") == receipt["request_sha256"]
                and result.get("outcome") == "succeeded"
                and result.get("artifact_sha256") == artifact_sha256
            ):
                original = path.with_suffix(".artifact")
                if (
                    not original.is_file()
                    or hashlib.sha256(original.read_bytes()).hexdigest()
                    != artifact_sha256
                ):
                    raise DetailGenerationError(
                        "NATIVE_DETAIL_ORIGINAL_ARTIFACT_CHANGED",
                        "The exact original paid response is missing or changed",
                    )
                return workspace, receipt
    raise DetailGenerationError(
        "NATIVE_DETAIL_CANVAS_RECEIPT_MISSING",
        "An accepted canvas needs its exact original successful invocation receipt",
    )


def verify_accepted_canvas(
    workspaces: Sequence[Path],
    binding: Mapping[str, Any],
    *,
    pair_id: str,
    artifact_sha256: str,
) -> dict[str, Any]:
    """Verify accepted native pixels and their unchanged original paid response."""
    from ..aurora_detail_runtime import (
        AuroraDetailHarnessError,
        AuroraDetailHarnessRunner,
    )
    from .pixels import DetailPixelError, load_native_pair_pixels
    from .review import DetailReviewError
    from .review_provenance import verify_review_provenance

    for workspace in workspaces:
        bind_workspace(workspace, binding)
        if not (workspace / "session.json").is_file():
            continue
        try:
            bundle = AuroraDetailHarnessRunner().run(
                "inspect-pair-artifacts",
                workspace,
                {
                    "session": workspace / "session.json",
                    "pair-id": pair_id,
                    "selection": "ACCEPTED",
                },
            )
            pixels = load_native_pair_pixels(bundle, workspaces, group_key="verified")
        except (AuroraDetailHarnessError, DetailPixelError) as exc:
            raise DetailGenerationError(
                "NATIVE_DETAIL_PIXEL_LINEAGE_INVALID", str(exc)
            ) from exc
        if pixels.canvas.sha256 != artifact_sha256:
            raise DetailGenerationError(
                "NATIVE_DETAIL_CHECKPOINT_HASH_MISMATCH",
                "Stored canvas differs from its exact accepted native pixels",
            )
        paid_workspace, invocation = verify_original_invocation(
            workspaces,
            binding,
            pair_id=pair_id,
            artifact_sha256=bundle["source"]["sha256"],
            token=bundle["token"],
            signed_request_sha256=bundle["signed_request_sha256"],
        )
        try:
            evidence = AuroraDetailHarnessRunner().run(
                "inspect-pair-review-evidence",
                workspace,
                {"session": workspace / "session.json", "pair-id": pair_id},
            )
            evidence_root = Path(
                evidence["snapshot_reference"]["snapshot_file"]
            ).parent.parent
            if (
                evidence_root.resolve() != paid_workspace.resolve()
                or evidence_root.resolve()
                not in {path.resolve() for path in workspaces}
            ):
                raise DetailReviewError(
                    "NATIVE_DETAIL_REVIEW_PROVENANCE_INVALID",
                    "Original review belongs to another workspace",
                )
            verify_review_provenance(evidence_root, invocation, bundle, evidence)
        except (
            AuroraDetailHarnessError,
            DetailReviewError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise DetailGenerationError(
                "NATIVE_DETAIL_REVIEW_PROVENANCE_INVALID", str(exc)
            ) from exc
        return bundle
    raise DetailGenerationError(
        "NATIVE_DETAIL_PIXEL_LINEAGE_MISSING",
        "The original accepted native pixel lineage is required",
    )


def wire_idempotency_key(logical_key: str, token: str) -> str:
    """A repair has a distinct signed request; transport retries keep its key."""
    return "detail-" + hashlib.sha256(f"{logical_key}:{token}".encode()).hexdigest()


def require_recoverable_pair(
    workspaces: Sequence[Path], binding: Mapping[str, Any], *, page: int, pair_id: str
) -> str:
    checkpoint = _read(workspaces[0] / "session.json")
    bind_workspace(workspaces[0], binding)
    pair = (checkpoint.get("modules") or [])[page - 1 : page + 1]
    if len(pair) != 2 or [module.get("id") for module in pair] != [
        f"M{page:02d}",
        f"M{page + 1:02d}",
    ]:
        raise DetailGenerationError(
            "NATIVE_DETAIL_CHECKPOINT_INCOMPLETE", "The exact approved pair is missing"
        )
    hashes = set()
    for module in pair:
        if module.get("status") not in {"COMPLETE", "COMPLETE_WITH_RESIDUALS"}:
            raise DetailGenerationError(
                "NATIVE_DETAIL_CHECKPOINT_INCOMPLETE",
                "The original pair has no accepted result",
            )
        accepted = [
            attempt
            for attempt in module.get("attempts", [])
            if attempt.get("image") == module.get("accepted_asset")
        ]
        attempt = accepted[-1] if accepted else {}
        sha = attempt.get("normalized_image_sha256")
        if (
            not isinstance(sha, str)
            or len(sha) != 64
            or attempt.get("pair_id") != pair_id
            or (attempt.get("judgment") or {})
            .get("artifact_binding", {})
            .get("image_sha256")
            != sha
        ):
            raise DetailGenerationError(
                "NATIVE_DETAIL_CHECKPOINT_BINDING_INVALID",
                "Accepted pixels lack their original audit binding",
            )
        hashes.add(sha)
    if len(hashes) != 1:
        raise DetailGenerationError(
            "NATIVE_DETAIL_CHECKPOINT_BINDING_INVALID",
            "Accepted screens do not share the same original canvas",
        )
    sha = hashes.pop()
    verify_accepted_canvas(workspaces, binding, pair_id=pair_id, artifact_sha256=sha)
    return sha


def bind_native_contract(
    workspace: Path, binding: Mapping[str, Any], *, create: bool = False
) -> None:
    """Lock the compiled input, identity, plan and native approval before dispatch."""
    bind_workspace(workspace, binding)
    session = _read(workspace / "session.json")
    document = session.get("approval_document") or {}
    try:
        document_sha = hashlib.sha256(Path(document["path"]).read_bytes()).hexdigest()
    except (OSError, KeyError, TypeError) as exc:
        raise DetailGenerationError(
            "NATIVE_DETAIL_APPROVAL_FILE_MISSING",
            "The compiler approval document is missing",
        ) from exc
    if document_sha != document.get("sha256") or session.get("version") != "4.3.1":
        raise DetailGenerationError(
            "NATIVE_DETAIL_COMPILED_SCOPE_INVALID",
            "Native version or approval bytes changed",
        )
    module_plans = [module.get("plan") for module in session.get("modules", [])]
    if module_plans != (session.get("plan") or {}).get("modules"):
        raise DetailGenerationError(
            "NATIVE_DETAIL_COMPILED_SCOPE_INVALID",
            "Module plans differ from their compiled authority",
        )
    value = {
        "binding_sha256": binding["sha256"],
        "files": {
            name: hashlib.sha256((workspace / name).read_bytes()).hexdigest()
            for name in ("input.json", "identity.json", "plan.json")
        },
        "compiled_plan_sha256": canonical_hash(session.get("plan")),
        "native_approval_document_sha256": document_sha,
        "native_authorization_sha256": canonical_hash(session.get("approval")),
    }
    path = workspace / "native-contract-binding.json"
    if create and not path.exists():
        with suppress(FileExistsError):
            _create(path, value)
    if _read(path) != value:
        raise DetailGenerationError(
            "NATIVE_DETAIL_COMPILED_SCOPE_CHANGED",
            "Compiled identity, plan or approval changed after authorization",
        )
