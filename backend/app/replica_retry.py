"""Reuse real canvases for a fresh native audit, never reuse an audit verdict."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .replica_native import ReplicaNativeError, contained, digest


@dataclass(frozen=True)
class PriorPixelReview:
    source_job_id: str
    artwork_sha256: str
    observations: tuple[str, ...]


@dataclass(frozen=True)
class RetryArtwork:
    module_id: str
    object_key: str
    sha256: str
    source_job_id: str
    plan_sha256: str
    prior_reviews: tuple[PriorPixelReview, ...] = ()


def _document(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    record = snapshot.get("files", {}).get(name, {})
    text = record.get("text")
    if not isinstance(text, str) or digest(text.encode()) != record.get("sha256"):
        raise ReplicaNativeError("原生重试来源快照哈希不一致。")
    try:
        value = json.loads(text)
    except ValueError as error:
        raise ReplicaNativeError("原生重试来源快照无效。") from error
    if not isinstance(value, dict):
        raise ReplicaNativeError("原生重试来源快照无效。")
    return value


def _failed_observations(judgment: dict[str, Any]) -> tuple[str, ...]:
    observations = judgment.get("major_mismatches")
    if observations == []:
        observations = [
            row.get("evidence")
            for row in judgment.get("comparisons", [])
            if row.get("match") is False
        ]
    if (
        not isinstance(observations, list)
        or not observations
        or any(not isinstance(x, str) or not x.strip() for x in observations)
    ):
        raise ReplicaNativeError("原生重试的既往像素差异缺少有效观察。")
    return tuple(observations)


def retry_artwork_candidates(
    previous: dict[str, Any],
    current: dict[str, Any],
    source_job_id: str,
    object_prefix: str,
    *,
    history: dict[str, dict[str, Any]] | None = None,
) -> dict[str, RetryArtwork]:
    if current.get("method") != "exact" or any(
        previous.get(key) != current.get(key)
        for key in (
            "schema", "method", "skill_id", "source_hash", "root", "model_protocol"
        )
    ):
        return {}
    before, now = (
        _document(previous, "session.json"),
        _document(current, "session.json"),
    )
    if before.get("state") not in {"SET_COMPLETE", "FINALIZED"}:
        return {}
    if not now.get("plan_sha256") or any(
        before.get(key) != now.get(key)
        for key in ("runtime_version", "plan_sha256", "plan", "input")
    ):
        return {}
    # Scope, approved copy, source observations and user facts remain frozen.
    if _document(previous, "context.json") != _document(current, "context.json"):
        return {}
    modules = before.get("modules", [])
    planned = now.get("plan", {}).get("modules", [])
    if len(modules) != len(planned):
        raise ReplicaNativeError("原生重试来源的逐屏记录不完整。")
    result = {}
    for record, module in zip(modules, planned, strict=True):
        attempts = record.get("attempts", [])
        if (
            record.get("id") != module["id"]
            or record.get("status") not in {"COMPLETE", "REJECTED_BEST_EFFORT"}
            or not attempts
        ):
            raise ReplicaNativeError("原生重试来源尚未完成逐屏核验。")
        latest = attempts[-1]
        judgment = latest.get("judgment") or {}
        if judgment.get("derived_decision") not in {"PASS", "FAIL"}:
            raise ReplicaNativeError("原生重试来源缺少真实审图记录。")
        artwork = latest.get("artwork")
        if not isinstance(artwork, str):
            raise ReplicaNativeError("原生重试来源缺少真实画布。")
        relative = (
            contained(Path(previous["root"]), artwork)
            .relative_to(Path(previous["root"]).resolve())
            .as_posix()
        )
        image = previous["files"].get(relative, {})
        sha, key = image.get("sha256"), image.get("object_key")
        if (
            not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha)
            or judgment.get("artwork_sha256") != sha
            or not isinstance(key, str)
            or not key.startswith(object_prefix)
        ):
            raise ReplicaNativeError("原生重试画布未绑定当前工作区和审图哈希。")
        result[module["id"]] = RetryArtwork(
            module["id"], key, sha, source_job_id, now["plan_sha256"]
        )
    reviews: dict[str, list[PriorPixelReview]] = {key: [] for key in result}
    for job_id, old in {**(history or {}), source_job_id: previous}.items():
        if old is not previous:
            # Apply exactly the same frozen-contract and pixel-binding checks to
            # ancestors. Their verdicts never become this attempt's verdict.
            compatible = retry_artwork_candidates(old, current, job_id, object_prefix)
            if not compatible:
                continue
        old_state = _document(old, "session.json")
        for record in old_state["modules"]:
            candidate = result[record["id"]]
            for attempt in record.get("attempts", []):
                judgment = attempt.get("judgment") or {}
                if (
                    judgment.get("derived_decision") != "FAIL"
                    or judgment.get("artwork_sha256") != candidate.sha256
                ):
                    continue
                relative = (
                    contained(Path(old["root"]), attempt["artwork"])
                    .relative_to(Path(old["root"]).resolve())
                    .as_posix()
                )
                image = old["files"].get(relative, {})
                if (
                    image.get("sha256") != candidate.sha256
                    or not isinstance(image.get("object_key"), str)
                    or not image["object_key"].startswith(object_prefix)
                ):
                    raise ReplicaNativeError("原生重试的既往像素差异缺少有效来源绑定。")
                review = PriorPixelReview(
                    job_id, candidate.sha256, _failed_observations(judgment)
                )
                if review not in reviews[record["id"]]:
                    reviews[record["id"]].append(review)
    return {
        key: RetryArtwork(
            row.module_id,
            row.object_key,
            row.sha256,
            row.source_job_id,
            row.plan_sha256,
            tuple(reviews[key]),
        )
        for key, row in result.items()
    }


def prior_pixel_reviews(
    candidate: RetryArtwork | None, module: dict[str, Any], source_job_id: str
) -> list[dict[str, Any]]:
    """Keep observations as fallible questions across this module's real edits."""
    reviews = list(candidate.prior_reviews) if candidate else []
    for attempt in module.get("attempts", []):
        judgment = attempt.get("judgment") or {}
        if judgment.get("derived_decision") == "FAIL":
            sha = judgment.get("artwork_sha256")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise ReplicaNativeError("当前画布的既往像素差异缺少有效来源绑定。")
            reviews.append(
                PriorPixelReview(source_job_id, sha, _failed_observations(judgment))
            )
    return [
        {
            "source_job_id": row.source_job_id,
            "artwork_sha256": row.artwork_sha256,
            "observations": list(row.observations),
        }
        for row in dict.fromkeys(reviews)
    ]
