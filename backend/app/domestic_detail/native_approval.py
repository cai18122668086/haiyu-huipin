"""Exact native approval artifacts shared by planning, editing and generation."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from .plans import DetailPlanError, canonical_hash

VERSION = "domestic-native-approval-v1"
CONTRACTS = (
    "input",
    "identity",
    "normalized_plan",
    "compiled_plan",
    "compiled_input",
    "compiled_identity",
)


class NativeRunner(Protocol):
    def run(
        self, command: str, workspace: Path, arguments: Mapping[str, str | Path]
    ) -> dict[str, Any]: ...


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def write_native_json(path: Path, value: object) -> None:
    path.write_bytes(json_bytes(value))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _skill_hash() -> str:
    path = (
        Path(__file__).resolve().parents[1]
        / "skill_manifests/aurora-domestic-ecommerce-detail-page.json"
    )
    return str(json.loads(path.read_text(encoding="utf-8"))["content_hash"])


def _proof(bundle: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: bundle.get(key)
        for key in (
            "version",
            "source_job_id",
            "skill_content_hash",
            "document_sha256",
            *[
                field
                for name in CONTRACTS
                for field in (name + "_sha256", name + "_file_sha256")
            ],
        )
    }


def validate_native_approval(bundle: object) -> dict[str, Any]:
    if not isinstance(bundle, Mapping) or bundle.get("version") != VERSION:
        raise DetailPlanError("The exact native compiler approval is required")
    value = dict(bundle)
    document = value.get("document_markdown")
    if (
        not isinstance(document, str)
        or not document
        or _sha(document.encode("utf-8")) != value.get("document_sha256")
        or value.get("skill_content_hash") != _skill_hash()
        or value.get("artifact_id") != canonical_hash(_proof(value))
    ):
        raise DetailPlanError(
            "Native approval document, Skill or artifact binding changed"
        )
    if not isinstance(value.get("source_job_id"), str) or not re.fullmatch(
        r"job_[A-Za-z0-9_-]{1,120}", value["source_job_id"]
    ):
        raise DetailPlanError("Native approval source task is invalid")
    if any(
        not isinstance(value.get(key), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value[key])
        for key in _proof(value)
        if key not in {"version", "source_job_id"}
    ):
        raise DetailPlanError("Native approval fingerprints are incomplete")
    return value


def native_approval_directory(workspace: Path, bundle: object) -> Path:
    value = validate_native_approval(bundle)
    # A lossless 256-bit folder encoding avoids Windows MAX_PATH failures in
    # nested evidence roots. The public artifact fingerprint remains full SHA-256.
    folder = (
        base64.urlsafe_b64encode(bytes.fromhex(value["artifact_id"]))
        .decode("ascii")
        .rstrip("=")
    )
    return workspace.parent / value["source_job_id"] / "ap" / folder


def load_native_approval(workspace: Path, bundle: object) -> dict[str, Any]:
    value = validate_native_approval(bundle)
    root = native_approval_directory(workspace, value)
    try:
        metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        if metadata != value:
            raise DetailPlanError("The stored native approval metadata changed")
        result = {}
        for name in CONTRACTS:
            raw = (root / (name + ".json")).read_bytes()
            data = json.loads(raw)
            if (
                _sha(raw) != value[name + "_file_sha256"]
                or canonical_hash(data) != value[name + "_sha256"]
            ):
                raise DetailPlanError("The stored native " + name + " contract changed")
            result[name] = data
        if (root / "campaign-approval.md").read_bytes() != value[
            "document_markdown"
        ].encode("utf-8"):
            raise DetailPlanError("The stored native approval file bytes changed")
        return result
    except (OSError, ValueError) as exc:
        if isinstance(exc, DetailPlanError):
            raise
        raise DetailPlanError(
            "Native approval artifacts are missing or unreadable"
        ) from exc


def _persist(path: Path, raw: bytes) -> None:
    if path.exists():
        if path.read_bytes() != raw:
            raise DetailPlanError("An immutable native approval artifact changed")
        return
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != raw:
            raise DetailPlanError(
                "An immutable native approval artifact changed"
            ) from None


def compile_native_approval(
    workspace: Path,
    *,
    runner: NativeRunner,
    native_input: Mapping[str, Any],
    identity: Mapping[str, Any],
    normalized_plan: Mapping[str, Any],
    resolution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="approval-compile-", dir=workspace) as temporary:
        root = Path(temporary)
        contracts = {
            "input": dict(native_input),
            "identity": dict(identity),
            "normalized_plan": dict(normalized_plan),
        }
        for name, contract in contracts.items():
            write_native_json(root / (name + ".json"), contract)
        session = root / "session.json"
        runner.run("start", root, {"input": root / "input.json", "session": session})
        if resolution is not None and native_input.get("source_conflicts"):
            write_native_json(root / "resolution.json", resolution)
            runner.run(
                "resolve-conflicts",
                root,
                {"session": session, "resolution": root / "resolution.json"},
            )
        runner.run(
            "lock-identity",
            root,
            {"session": session, "identity": root / "identity.json"},
        )
        runner.run(
            "compile-plan",
            root,
            {"session": session, "plan": root / "normalized_plan.json"},
        )
        compiled = json.loads(session.read_text(encoding="utf-8"))
        document_path = Path(compiled["approval_document"]["path"])
        if document_path.resolve().parent != root.resolve():
            raise DetailPlanError("Native approval escaped the compiler workspace")
        document = document_path.read_bytes()
        if _sha(document) != compiled["approval_document"]["sha256"]:
            raise DetailPlanError(
                "Native compiler document fingerprint differs from its bytes"
            )
        contracts["compiled_plan"] = compiled["plan"]
        contracts["compiled_input"] = compiled["input"]
        contracts["compiled_identity"] = compiled["identity"]
        value = {
            "version": VERSION,
            "source_job_id": workspace.name,
            "skill_content_hash": _skill_hash(),
            "document_markdown": document.decode("utf-8"),
            "document_sha256": _sha(document),
        }
        for name, contract in contracts.items():
            value[name + "_sha256"] = canonical_hash(contract)
            value[name + "_file_sha256"] = _sha(json_bytes(contract))
        value["artifact_id"] = canonical_hash(_proof(value))
        target = native_approval_directory(workspace, value)
        target.mkdir(parents=True, exist_ok=True)
        for name, contract in contracts.items():
            _persist(target / (name + ".json"), json_bytes(contract))
        _persist(target / "campaign-approval.md", document)
        _persist(target / "metadata.json", json_bytes(value))
        load_native_approval(workspace, value)
        return value


def verify_compiled_native_approval(
    workspace: Path,
    bundle: object,
    *,
    native_input: Mapping[str, Any],
    identity: Mapping[str, Any],
    normalized_plan: Mapping[str, Any],
    compiled_plan: Mapping[str, Any],
    compiled_input: Mapping[str, Any],
    compiled_identity: Mapping[str, Any],
    document: bytes,
) -> dict[str, Any]:
    value = validate_native_approval(bundle)
    load_native_approval(workspace, value)
    for name, contract in (
        ("input", native_input),
        ("identity", identity),
        ("normalized_plan", normalized_plan),
        ("compiled_plan", compiled_plan),
        ("compiled_input", compiled_input),
        ("compiled_identity", compiled_identity),
    ):
        if (
            canonical_hash(contract) != value[name + "_sha256"]
            or _sha(json_bytes(contract)) != value[name + "_file_sha256"]
        ):
            raise DetailPlanError("Generation differs from the approved native " + name)
    for name, filename in (
        ("input", "input.json"),
        ("identity", "identity.json"),
        ("normalized_plan", "plan.json"),
    ):
        try:
            same_file = (
                _sha((workspace / filename).read_bytes())
                == value[name + "_file_sha256"]
            )
        except OSError as exc:
            raise DetailPlanError("A native generation input file is missing") from exc
        if not same_file:
            raise DetailPlanError(
                "A native generation input file differs from its approval"
            )
    if document != value["document_markdown"].encode("utf-8"):
        raise DetailPlanError(
            "Generation differs from the approved native document bytes"
        )
    return value


def verify_approval_sources(
    workspace: Path, bundle: object, sources: Sequence[Mapping[str, Any]]
) -> dict[str, str]:
    """Read the originally approved pixels without recreating or overwriting them."""
    contracts = load_native_approval(workspace, bundle)
    records = [
        *(
            dict(row, approved_role="product_reference")
            for row in contracts["input"]["product_images"]
        ),
        *(
            dict(row, approved_role="style_reference")
            for row in contracts["input"].get("reference_materials", [])
        ),
    ]
    paths = {str(row["id"]): str(row["path"]) for row in records}
    expected = {str(row["asset_id"]): row for row in sources}
    if (
        len(paths) != len(records)
        or len(expected) != len(sources)
        or set(paths) != set(expected)
    ):
        raise DetailPlanError(
            "Native approval source inventory differs from the approved assets"
        )
    for row in records:
        source = expected[str(row["id"])]
        path = Path(str(row["path"])).resolve()
        try:
            valid = (
                source.get("role") == row["approved_role"]
                and path.is_relative_to(workspace.parent.resolve())
                and _sha(path.read_bytes()) == source.get("sha256")
            )
        except OSError as exc:
            raise DetailPlanError(
                "Original native approval source pixels are unavailable"
            ) from exc
        if not valid:
            raise DetailPlanError(
                "Original native approval source pixels or roles changed"
            )
    return paths
