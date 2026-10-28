"""Native local revision ordering on owned synthetic copies, never website jobs."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from app.aurora_detail_runtime import AuroraDetailHarnessRunner

pytest_plugins = ("tests.test_native_detail_delivery_manifest",)


def adjustment(module_id):
    return {
        "module_id": module_id,
        "verbatim_instruction": "只将本屏标题移到商品左侧，保留已确认文案和商品形态。",
        "change_scope": "local",
        "change_kind": "TYPOGRAPHY_LAYOUT",
        "change_only": ["本屏标题移到商品左侧"],
        "preserve": ["原商品结构、批准文案与另一屏"],
        "must_not_regress": ["身份、事实、组内接缝与文字可读性"],
        "changes_identity": False,
        "changes_facts": False,
        "changes_target_buyer": False,
        "changes_campaign_style": False,
        "changes_module_sequence": False,
    }


def inventory(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def owned_copy(accepted_delivery):
    source_root, source, state = accepted_delivery
    state["plan"]["output_strategy"].update(
        target_platform="synthetic",
        module_count=8,
        canvas_width_px=3,
        verification_status="INTERNAL_ONLY",
    )
    for module in state["modules"]:
        module.update(cycle_id=7, cycle_attempt_count=2)
    source.write_text(json.dumps(state), encoding="utf-8")
    root = source_root.parent / "owned-local-revision"
    root.mkdir()
    target = root / "session.json"
    target.write_bytes(source.read_bytes())
    return source_root, root, target, state


def run_native(root, target, delta):
    path = root / "adjustment.json"
    path.write_text(json.dumps(delta, ensure_ascii=False), encoding="utf-8")
    runner = AuroraDetailHarnessRunner()
    # Intentionally bypass the website allowlist only in this native contract
    # test. Production Runner must keep rejecting source-mutating adjustments.
    return subprocess.run(
        [
            sys.executable,
            str(runner.harness_path),
            "apply-user-adjustment",
            "--session",
            str(target),
            "--adjustment",
            str(path),
        ],
        cwd=runner.skill_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("target_index", range(8))
def test_local_revision_starts_at_pair_upper_and_preserves_both_cycle_histories(
    accepted_delivery,
    target_index,
):
    source_root, root, target, state = owned_copy(accepted_delivery)
    original = inventory(source_root)
    delta = adjustment(state["modules"][target_index]["id"])
    result = run_native(root, target, delta)
    assert result.returncode == 0, result.stderr
    changed = json.loads(target.read_bytes())
    pair_start = target_index // 2 * 2
    assert changed["current_index"] == pair_start
    assert changed["state"] == "AUTHORIZED"
    for index, module in enumerate(changed["modules"]):
        prior = state["modules"][index]
        if pair_start <= index < pair_start + 2:
            assert module["cycle_id"] == 8 and module["cycle_attempt_count"] == 0
            assert module["prior_accepted_asset"] == prior["accepted_asset"]
            assert module["accepted_asset"] is None
            assert module["attempts"] == prior["attempts"]
            history = module["revision_history"][-1]
            assert history["cycle_id"] == 7 and history["attempt_count"] == 2
            assert history["closed_at"]
            if index == target_index:
                assert module["user_adjustment"] == delta
                assert history["instruction"] == delta["verbatim_instruction"]
            else:
                assert (
                    "Preserve sibling"
                    in module["user_adjustment"]["verbatim_instruction"]
                )
                assert history["source_module_id"] == delta["module_id"]
                assert (
                    history["verbatim_user_instruction"]
                    == delta["verbatim_instruction"]
                )
        else:
            assert module == prior
    assert inventory(source_root) == original


@pytest.mark.parametrize(
    "field",
    [
        "change_scope",
        "changes_identity",
        "changes_facts",
        "changes_target_buyer",
        "changes_campaign_style",
        "changes_module_sequence",
        "module_id",
    ],
)
def test_global_or_unknown_local_revision_is_rejected_without_mutating_copy(
    accepted_delivery,
    field,
):
    source_root, root, target, _ = owned_copy(accepted_delivery)
    original = inventory(source_root)
    before = target.read_bytes()
    delta = adjustment("M02")
    delta[field] = (
        "global"
        if field == "change_scope"
        else "missing"
        if field == "module_id"
        else True
    )
    result = run_native(root, target, delta)
    assert result.returncode != 0
    assert target.read_bytes() == before and inventory(source_root) == original
