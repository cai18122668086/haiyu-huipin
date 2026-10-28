"""Immutable original native initial/completion/repair planner calls."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .planner_dispatch import (
    PlannerDispatchError,
    require_route,
    validate_dispatch_result,
)
from .plans import canonical_hash
from .review import DetailReviewError, ReviewStore

PLANNING_CALL_VERSION = "native-planning-call-v1"
UNCERTAIN_OUTCOMES = {"outcome_unknown", "response_invalid"}


def call_binding(
    *,
    profile: str,
    group_key: str,
    target: str,
    logical_key: str,
    semantics: Mapping[str, Any],
    prompt: str,
    schema: Mapping[str, Any],
    route: Mapping[str, Any],
    skill_hash: str,
    runtime_hash: str,
) -> dict[str, Any]:
    semantic = {
        "profile": profile,
        "group_key": group_key,
        "target": target,
        "semantics": deepcopy(dict(semantics)),
        "prompt": prompt,
        "schema": deepcopy(dict(schema)),
    }
    return {
        "version": PLANNING_CALL_VERSION,
        "phase": profile,
        "group_key": group_key,
        "target": target,
        "logical_key_sha256": canonical_hash(logical_key),
        "semantic_sha256": canonical_hash(semantic),
        "semantic_request": semantic,
        "dispatch_route": require_route(route),
        "skill_content_hash": skill_hash,
        "runtime_source_sha256": runtime_hash,
    }


@dataclass(frozen=True)
class NativePlanningStore:
    root: Path
    binding: Mapping[str, Any]

    @property
    def store(self) -> ReviewStore:
        return ReviewStore(self.root, self.binding)

    @property
    def wire_key(self) -> str:
        return "native-plan-" + self.store.sha256

    def _stamp(self, replayed: bool = False) -> dict[str, Any]:
        return {
            "version": PLANNING_CALL_VERSION,
            "binding_sha256": self.store.sha256,
            "request_file": "planner-calls/" + self.store.request_path.name,
            "response_file": "planner-calls/" + self.store.response_path.name,
            "replayed": replayed,
            "phase": self.binding["phase"],
            "target": self.binding["target"],
            "group_key": self.binding["group_key"],
        }

    def _original(self) -> dict[str, Any]:
        try:
            result = self.store.completed()
        except DetailReviewError as exc:
            raise PlannerDispatchError(
                "NATIVE_PLANNING_OUTCOME_UNKNOWN",
                "Reconcile missing or changed original planning evidence",
            ) from exc
        if result is None:
            raise PlannerDispatchError(
                "NATIVE_PLANNING_OUTCOME_UNKNOWN",
                "The original planning request lost its completion evidence",
            )
        validate_dispatch_result(result, require_route(self.binding["dispatch_route"]))
        envelope = result.get("provider_envelope") or {}
        if canonical_hash(envelope.get("native_planner_receipt")) != canonical_hash(
            self._stamp()
        ):
            raise PlannerDispatchError(
                "NATIVE_PLANNING_RECEIPT_INVALID",
                "Original planning receipt binding changed",
            )
        if result.get("error_code") is None and isinstance(
            result.get("audit_raw_content"), str
        ):
            from ..xinghe_agent import _planner_content_payload

            if canonical_hash(
                _planner_content_payload(result["audit_raw_content"])
            ) != canonical_hash(result["payload"]):
                raise PlannerDispatchError(
                    "NATIVE_PLANNING_RAW_REPLY_CHANGED",
                    "Original planning model text differs from its parsed result",
                )
        return result

    def _prior(self, path: Path) -> NativePlanningStore:
        if path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction():
            raise PlannerDispatchError(
                "NATIVE_PLANNING_RECEIPT_INVALID",
                "Planning evidence cannot traverse a link",
            )
        binding = json.loads(path.read_bytes())["binding"]
        old = NativePlanningStore(path.parent, binding)
        if old.store.request_path != path:
            raise PlannerDispatchError(
                "NATIVE_PLANNING_RECEIPT_INVALID",
                "Planning request file does not match its canonical binding",
            )
        return old

    def completed(self) -> dict[str, Any] | None:
        if self.store.request_path.exists():
            result = self._original()
            result["provider_envelope"]["native_planner_receipt"] = self._stamp(
                replayed=True
            )
            return result
        for path in self.root.glob("*.request.json"):
            old = self._prior(path)
            if (
                old.binding.get("logical_key_sha256")
                != self.binding["logical_key_sha256"]
            ):
                continue
            result = old._original()
            dispatch = result["provider_envelope"]["native_planner_dispatch"]
            if dispatch["outcome"] in UNCERTAIN_OUTCOMES:
                raise PlannerDispatchError(
                    "NATIVE_PLANNING_OUTCOME_UNKNOWN",
                    "Reconcile original planning before changing its context",
                )
            if any(
                old.binding.get(k) != self.binding[k]
                for k in ("runtime_source_sha256", "skill_content_hash")
            ):
                raise PlannerDispatchError(
                    "NATIVE_PLANNING_BINDING_CHANGED",
                    "An existing planning request uses another runtime or Skill",
                )
            if old.binding.get("semantic_sha256") == self.binding["semantic_sha256"]:
                raise PlannerDispatchError(
                    "NATIVE_PLANNING_BINDING_CHANGED",
                    "An existing identical planning phase uses another route or model",
                )
        return None

    def assert_no_unknown_source(self, source_root: Path) -> None:
        for path in source_root.glob("*.request.json"):
            old = self._prior(path)
            if any(
                old.binding.get(k) != self.binding[k]
                for k in ("phase", "group_key", "target")
            ):
                continue
            result = old._original()
            if (
                result["provider_envelope"]["native_planner_dispatch"]["outcome"]
                in UNCERTAIN_OUTCOMES
            ):
                raise PlannerDispatchError(
                    "NATIVE_PLANNING_OUTCOME_UNKNOWN",
                    "The source planning call is uncertain; do not send it again",
                )

    def begin(self) -> None:
        try:
            self.store.begin()
        except DetailReviewError as exc:
            raise PlannerDispatchError(
                "NATIVE_PLANNING_ALREADY_DISPATCHED",
                "This planning call already has an original dispatch marker",
            ) from exc

    def finish(self, result: Mapping[str, Any]) -> dict[str, Any]:
        value = deepcopy(dict(result))
        envelope = dict(value.get("provider_envelope") or {})
        envelope["native_planner_receipt"] = self._stamp()
        value["provider_envelope"] = envelope
        self.store.finish(value)
        return self._original()
