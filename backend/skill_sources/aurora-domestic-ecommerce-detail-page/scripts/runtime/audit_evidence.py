"""Immutable original pair audit input and exact native decision, per render token."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from .atomic_review import contract_sha256, validate_atomic_reports
from .artifact_binding import validate_artifact_binding
from .pixel_lineage import pair_artifacts
from .local_revision_review import validate_revision_observation
from .local_contracts import validate_request_contracts
from .schema_validation import validate_schema
from .state import HarnessError

VERSION = "native-pair-audit-evidence-v1"


def _binding(lineage: dict) -> dict:
    return {"version": VERSION, **{key: lineage[key] for key in
            ("pair_id", "module_ids", "token", "cycle_id", "cycle_attempt", "signed_request_sha256")},
            "artifact_lineage_sha256": lineage["sha256"]}


def _snapshot_path(lineage: dict) -> Path:
    source = Path(lineage["source"]["path"])
    if source.parent.name != "provider-originals":
        raise HarnessError("Pair audit evidence requires the native original source directory")
    return source.parent.parent / "audit-originals" / (hashlib.sha256(lineage["token"].encode()).hexdigest() + ".json")


def record_pair_audit(judgment_path: str | Path, judgment: dict, recorded: dict, lineage: dict, *, revision_contract: dict | None = None) -> dict:
    original = Path(judgment_path).read_bytes()
    if contract_sha256(json.loads(original)) != contract_sha256(judgment):
        raise HarnessError("Original pair audit input changed during validation")
    value = {"binding": _binding(lineage), "audit_input_bytes_base64": base64.b64encode(original).decode(),
             "audit_input_sha256": hashlib.sha256(original).hexdigest(),
             "recorded_judgment": recorded, "recorded_judgment_sha256": contract_sha256(recorded)}
    if revision_contract is not None:
        value["revision_contract"] = revision_contract
    path = _snapshot_path(lineage)
    path.parent.mkdir(exist_ok=True)
    if path.parent.is_symlink() or path.parent.is_junction() or path.is_symlink():
        raise HarnessError("Original audit snapshot cannot traverse a link")
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != payload:
            raise HarnessError("The immutable original pair audit cannot be replaced")
    return {**_binding(lineage), "snapshot_file": str(path),
            "snapshot_sha256": hashlib.sha256(payload).hexdigest()}


def inspect_pair_audit(session: dict, pair_id: str) -> dict:
    try:
        lineage = pair_artifacts(session, pair_id, "ACCEPTED")
        by_id = {row["id"]: row for row in session["modules"]}
        modules = [by_id[key] for key in lineage["module_ids"]]
        attempts = [next(row for row in reversed(module["attempts"]) if row["image"] == module["accepted_asset"])
                    for module in modules]
        reference = attempts[0]["review_evidence"]
        if any(contract_sha256(row.get("review_evidence")) != contract_sha256(reference) for row in attempts):
            raise HarnessError("The two accepted slices do not share the same original audit evidence")
        expected = _binding(lineage)
        if contract_sha256({key: reference.get(key) for key in expected}) != contract_sha256(expected):
            raise HarnessError("Original audit evidence belongs to another render or pixel lineage")
        path = _snapshot_path(lineage)
        if Path(reference["snapshot_file"]) != path or path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction():
            raise HarnessError("Original audit snapshot escaped its render workspace")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != reference["snapshot_sha256"]:
            raise HarnessError("Original pair audit snapshot bytes changed")
        snapshot = json.loads(payload)
        if contract_sha256(snapshot["binding"]) != contract_sha256(expected):
            raise HarnessError("Original audit snapshot binding changed")
        original = base64.b64decode(snapshot["audit_input_bytes_base64"], validate=True)
        if hashlib.sha256(original).hexdigest() != snapshot["audit_input_sha256"]:
            raise HarnessError("Original pair audit input bytes changed")
        judgment = json.loads(original)
        recorded = snapshot["recorded_judgment"]
        if contract_sha256(recorded) != snapshot["recorded_judgment_sha256"] or any(contract_sha256(row["judgment"]) != snapshot["recorded_judgment_sha256"] for row in attempts):
            raise HarnessError("Accepted pair observations differ from the immutable original audit")
        ignored = {"material_failures", "effective_passed_layers"}
        if contract_sha256({k: v for k, v in recorded.items() if k not in ignored}) != contract_sha256({k: v for k, v in judgment.items() if k not in ignored}):
            raise HarnessError("Native audit decision does not preserve the original observations")
        from .website_review import schema_name
        validate_schema(judgment, schema_name(session["plan"]), "original accepted pair audit")
        if judgment["pair_id"] != pair_id or judgment["module_ids"] != lineage["module_ids"]:
            raise HarnessError("Original accepted audit identifies another pair")
        validate_artifact_binding(judgment, lineage["canvas"]["path"], "original accepted audit")
        validate_request_contracts(session, lineage, modules)
        validate_atomic_reports(judgment, modules, lineage)
        validate_revision_observation(snapshot.get("revision_contract"), judgment, lineage)
        result = {"version": VERSION, "snapshot_reference": reference,
                  "audit_input": judgment, "recorded_judgment": recorded}
        result["sha256"] = contract_sha256(result)
        return result
    except (OSError, KeyError, TypeError, ValueError, StopIteration) as exc:
        if isinstance(exc, HarnessError):
            raise
        raise HarnessError("Original accepted pair audit evidence is missing, changed or unreadable") from exc
