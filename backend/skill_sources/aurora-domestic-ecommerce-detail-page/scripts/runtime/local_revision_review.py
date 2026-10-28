"""Bind a local revision audit to original and candidate pixels, never plan prose."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .pixel_lineage import pair_artifacts
from .schema_validation import validate_schema
from .state import HarnessError

VERSION = "native-local-revision-pixel-v1"


def _sha(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _owned_bytes(path: Path, root: Path) -> bytes:
    if (not path.is_absolute() or path.parent.resolve() != root.resolve()
            or any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))):
        raise HarnessError("Local revision review inputs must belong to the owned workspace")
    return path.read_bytes()


def revision_review_contract(session: dict, pair_id: str) -> dict | None:
    fork = session.get("revision_fork")
    if fork is None:
        return None
    if (not isinstance(fork, dict) or fork.get("version") != "owned-local-revision-fork-v1"
            or fork.get("maximum_new_image_requests") != 2 or fork.get("delivery_mode") != "USER_FINAL_REVIEW"):
        raise HarnessError("Local revision review authority is malformed")
    pairs = session.get("plan", {}).get("output_strategy", {}).get("continuous_pair_rendering", {}).get("pair_ids", [])
    if not any(row.get("pair_id") == fork.get("pair_id") and row.get("module_ids") == fork.get("module_ids") for row in pairs):
        raise HarnessError("Local revision review authority identifies no approved pair")
    if fork.get("pair_id") != pair_id:
        return None
    try:
        root = Path(session["session_dir"])
        source_path = Path(fork["source_session"])
        if source_path.name != "source-session.json":
            raise HarnessError("Local revision review requires its owned original snapshot")
        source_bytes = _owned_bytes(source_path, root)
        if hashlib.sha256(source_bytes).hexdigest() != fork["source_session_sha256"]:
            raise HarnessError("Local revision review source snapshot changed")
        source = json.loads(source_bytes)
        intent = json.loads(_owned_bytes(root / "local-revision-intent.json", root))
        if (intent["sha256"] != _sha({k: v for k, v in intent.items() if k != "sha256"})
                or intent["sha256"] != fork["intent_sha256"]
                or intent["version"] != "domestic-detail-local-revision-v1"
                or intent["source_session_sha256"] != fork["source_session_sha256"]
                or intent["job_id"] != fork["job_id"] or intent["job_id"] != root.name
                or intent["source_job_id"] != fork["source_job_id"]
                or intent["source_job_id"] != Path(source["session_dir"]).name
                or intent["adjustment"] != fork["adjustment"]):
            raise HarnessError("Local revision review intent differs from its original authority")
        adjustment = intent["adjustment"]
        validate_schema(adjustment, "user-adjustment.schema.json", "local revision review adjustment")
        lineage = pair_artifacts(source, pair_id, "ACCEPTED")
        if (lineage["module_ids"] != fork["module_ids"]
                or adjustment["module_id"] not in lineage["module_ids"]):
            raise HarnessError("Local revision review targets another accepted pair")
        quote = (intent.get("authority") or {}).get("quote")
        if quote is not None:
            canvas = quote["source_pair_canvas"]
            if (quote["pair_id"] != pair_id or quote["module_ids"] != lineage["module_ids"]
                    or quote["selected_module_id"] != adjustment["module_id"]
                    or canvas["sha256"] != lineage["canvas"]["sha256"]
                    or canvas["width"] != lineage["canvas"]["width_px"]
                    or canvas["height"] != lineage["canvas"]["height_px"]
                    or canvas["pixel_lineage_sha256"] != lineage["sha256"]
                    or [(row["module_id"], row["sha256"]) for row in quote["source_screens"]]
                    != [(row["module_id"], row["sha256"]) for row in lineage["slices"]]):
                raise HarnessError("Local revision quote differs from the actual accepted original pixels")
        value = {"version": VERSION, "intent_sha256": intent["sha256"],
                 "source_session_sha256": fork["source_session_sha256"],
                 "pair_id": pair_id, "module_ids": lineage["module_ids"],
                 "selected_module_id": adjustment["module_id"], "adjustment": adjustment,
                 "authority_files": {"source_session": {"path": str(source_path), "sha256": fork["source_session_sha256"]},
                                     "intent": {"path": str(root / "local-revision-intent.json"), "canonical_sha256": intent["sha256"]}},
                 "baseline": {"canvas": lineage["canvas"], "screens": lineage["slices"],
                              "artifact_lineage_sha256": lineage["sha256"]}}
        return {**value, "sha256": _sha(value)}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, HarnessError):
            raise
        raise HarnessError("Local revision original review pixels or authority are missing or changed") from exc


def validate_revision_observation(contract: dict | None, judgment: dict, lineage: dict) -> list[dict]:
    """Unknown observations are protocol errors; explicit negatives use the existing cycle."""
    observation = judgment.get("local_revision_observation")
    if contract is None:
        if "local_revision_observation" in judgment:
            raise HarnessError("A local revision observation requires original revision authority")
        return []
    try:
        if (contract["version"] != VERSION
                or contract["sha256"] != _sha({k: v for k, v in contract.items() if k != "sha256"})):
            raise HarnessError("Local revision review contract changed")
        validate_schema(observation, "local-revision-observation.schema.json", "local revision pixel observation")
        expected = {"version": VERSION, "contract_sha256": contract["sha256"],
                    "intent_sha256": contract["intent_sha256"],
                    "source_session_sha256": contract["source_session_sha256"],
                    "pair_id": contract["pair_id"], "module_ids": contract["module_ids"],
                    "selected_module_id": contract["selected_module_id"],
                    "source_canvas_sha256": contract["baseline"]["canvas"]["sha256"],
                    "candidate_canvas_sha256": lineage["canvas"]["sha256"]}
        if (lineage["pair_id"] != contract["pair_id"]
                or lineage["module_ids"] != contract["module_ids"]
                or any(lineage["canvas"][key] != contract["baseline"]["canvas"][key] for key in ("width_px", "height_px"))
                or any(observation[key] != value for key, value in expected.items())):
            raise HarnessError("Local revision observation belongs to another intent, pair or canvas")
        before = {row["module_id"]: row for row in contract["baseline"]["screens"]}
        after = {row["module_id"]: row for row in lineage["slices"]}
        target = contract["selected_module_id"]
        sibling = next(key for key in contract["module_ids"] if key != target)
        if (observation["target_before_slice_sha256"] != before[target]["sha256"]
                or observation["target_candidate_slice_sha256"] != after[target]["sha256"]):
            raise HarnessError("Local revision target observations identify other fixed screen pixels")
        # A snapshot remains usable when a later fork revises a different pair.
        # Validate its own original bytes rather than the later session's fork.
        for row in [contract["authority_files"]["source_session"], contract["baseline"]["canvas"], *before.values()]:
            path = Path(row["path"])
            if (any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))
                    or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]):
                raise HarnessError("Original local revision baseline pixels changed")
        intent_ref = contract["authority_files"]["intent"]
        intent_bytes = _owned_bytes(Path(intent_ref["path"]), Path(contract["authority_files"]["source_session"]["path"]).parent)
        intent = json.loads(intent_bytes)
        if (intent.get("sha256") != intent_ref["canonical_sha256"]
                or intent_ref["canonical_sha256"] != contract["intent_sha256"]
                or _sha({k: v for k, v in intent.items() if k != "sha256"}) != contract["intent_sha256"]):
            raise HarnessError("Original local revision intent authority changed")
        findings = []
        def observe(row: dict, module_id: str, check: str) -> None:
            for key in ("before_observation", "after_observation", "visible_evidence"):
                if not row[key].strip() or re.fullmatch(r"(?:unknown|unverifiable|unclear|cannot determine|无法确定|无法判断|看不清)(?:[.!。:：\s].*)?",
                                row[key].strip(), re.IGNORECASE | re.DOTALL):
                    raise HarnessError("Unknown local revision pixels cannot authorize an image repair")
            if row["pass"] is False:
                findings.append({"check": check, "module_id": module_id,
                                 "visible_evidence": row["visible_evidence"]})
        for group in ("change_only", "preserve", "must_not_regress"):
            requirements = contract["adjustment"][group]
            rows = observation[group]
            if len(rows) != len(requirements) or any(
                    row["index"] != index or row["original_requirement"] != requirement
                    for index, (row, requirement) in enumerate(zip(rows, requirements))):
                raise HarnessError("Local revision must observe every exact original requirement in order")
            for row in rows:
                observe(row, target, "local_revision_" + group)
        row = observation["sibling"]
        if (row["module_id"] != sibling or row["before_slice_sha256"] != before[sibling]["sha256"]
                or row["candidate_slice_sha256"] != after[sibling]["sha256"]):
            raise HarnessError("Local revision sibling observation identifies other fixed screen pixels")
        observe(row, sibling, "local_revision_sibling_preservation")
        if judgment["status"] == "PASS" and findings:
            raise HarnessError("PASS contradicts explicit negative local revision pixel observations")
        return findings
    except (OSError, KeyError, TypeError, ValueError, StopIteration) as exc:
        if isinstance(exc, HarnessError):
            raise
        raise HarnessError("Local revision pixel observation or original binding is missing or changed") from exc
