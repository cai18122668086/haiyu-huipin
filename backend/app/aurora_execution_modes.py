"""Execution-mode predicates shared by the Aurora planner and workers."""

from __future__ import annotations

from collections.abc import Iterable

PARALLEL_V20 = "parallel_v20"
PARALLEL_V20_CALIBRATED = "parallel_v20_calibrated"
SEQUENTIAL_V20 = "sequential_v20"
PARALLEL_AURORA_MODES = frozenset({PARALLEL_V20, PARALLEL_V20_CALIBRATED})
V20_AURORA_MODES = frozenset({SEQUENTIAL_V20, *PARALLEL_AURORA_MODES})


def is_parallel_aurora_mode(mode: object) -> bool:
    return isinstance(mode, str) and mode in PARALLEL_AURORA_MODES


def is_v20_aurora_mode(mode: object) -> bool:
    return isinstance(mode, str) and mode in V20_AURORA_MODES


def is_calibrated_parallel_aurora_mode(mode: object) -> bool:
    return mode == PARALLEL_V20_CALIBRATED


def contains_parallel_aurora_mode(values: Iterable[object]) -> bool:
    return any(value in PARALLEL_AURORA_MODES for value in values)
