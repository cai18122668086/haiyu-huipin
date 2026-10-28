"""Owned native local revision forks; original accepted campaigns stay immutable."""

from __future__ import annotations

import json

import pytest

from app.aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
)
from app.domestic_detail.plans import canonical_hash
from tests.test_native_detail_local_adjustment import adjustment, inventory, owned_copy

pytest_plugins = ("tests.test_native_detail_delivery_manifest",)


def fork_fixture(accepted_delivery, index=1, automatic=False):
    source_root, _, _, source_state = owned_copy(accepted_delivery)
    source = source_root / "session.json"
    source_state["session_dir"] = str(source_root)
    if automatic:
        source_state["state"] = "AUTO_FINALIZE_AUTHORIZED"
        source_state["approval"]["delivery_mode"] = "AUTO_FINALIZE_AUTHORIZED"
    source.write_text(json.dumps(source_state), encoding="utf-8")
    root = source_root.parent / "job_owned_revision"
    root.mkdir()
    snapshot = root / "source-session.json"
    snapshot.write_bytes(source.read_bytes())
    import hashlib

    source_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    intent = {
        "version": "domestic-detail-local-revision-v1",
        "job_id": root.name,
        "source_job_id": source_root.name,
        "workspace_id": "ws_original",
        "actor_id": "usr_local",
        "source_session_sha256": source_sha,
        "approval_text": "我明确授权按原方案和本次局部修改要求执行一个新双屏修订周期。",
        "adjustment": adjustment(source_state["modules"][index]["id"]),
    }
    intent["sha256"] = canonical_hash(intent)
    args = {
        "session": root / "session.json",
        "source-session": snapshot,
        "source-session-sha256": source_sha,
        "revision-intent": root / "local-revision-intent.json",
    }
    args["revision-intent"].write_text(json.dumps(intent), encoding="utf-8")
    return source_root, source_state, root, args, intent


@pytest.mark.parametrize("index", range(8))
@pytest.mark.parametrize("automatic", [False, True])
def test_owned_revision_keeps_source_exact_and_never_transfers_auto_authority(
    accepted_delivery,
    index,
    automatic,
):
    source_root, original, root, args, intent = fork_fixture(
        accepted_delivery, index, automatic
    )
    before = inventory(source_root)
    runner = AuroraDetailHarnessRunner()
    first = runner.run("fork-local-revision", root, args)
    changed_bytes = args["session"].read_bytes()
    current = json.loads(changed_bytes)
    assert first["reused"] is False
    assert current["current_index"] == index // 2 * 2
    assert current["approval"]["delivery_mode"] == "USER_FINAL_REVIEW"
    assert current["approval"]["text"] == intent["approval_text"]
    assert current["revision_fork"]["source_approval"] == original["approval"]
    assert current["revision_fork"]["maximum_new_image_requests"] == 2
    assert runner.run("fork-local-revision", root, args)["reused"] is True
    assert args["session"].read_bytes() == changed_bytes
    assert inventory(source_root) == before
    assert not list(root.glob(".local-*.json"))
    sibling = current["modules"][index ^ 1]["user_adjustment"]
    assert sibling["change_only"] != intent["adjustment"]["change_only"]
    assert sibling["preserve"] != intent["adjustment"]["preserve"]
    assert sibling["must_not_regress"] != intent["adjustment"]["must_not_regress"]
    for position, module in enumerate(current["modules"]):
        if position // 2 != index // 2:
            assert module == original["modules"][position]
        else:
            assert module["cycle_id"] == 8 and module["cycle_attempt_count"] == 0
            assert module["attempts"] == original["modules"][position]["attempts"]


@pytest.mark.parametrize(
    "tamper",
    [
        "source_sha",
        "intent_hash",
        "job_id",
        "source_job_id",
        "actor_id",
        "approval_text",
        "global",
        "missing_local_flag",
        "generic_continue",
        "unknown_module",
    ],
)
def test_owned_revision_rejects_changed_or_missing_original_authority_before_write(
    accepted_delivery,
    tamper,
):
    source_root, _, root, args, intent = fork_fixture(accepted_delivery)
    before = inventory(source_root)
    if tamper == "source_sha":
        args["source-session-sha256"] = "f" * 64
    elif tamper in {"job_id", "source_job_id", "actor_id", "approval_text"}:
        intent[tamper] = "wrong_owner" if "job_id" in tamper else ""
    elif tamper == "global":
        intent["adjustment"]["changes_facts"] = True
    elif tamper == "missing_local_flag":
        del intent["adjustment"]["changes_identity"]
    elif tamper == "generic_continue":
        intent["adjustment"]["verbatim_instruction"] = "继续"
    elif tamper == "unknown_module":
        intent["adjustment"]["module_id"] = "M99"
    if tamper != "intent_hash":
        intent["sha256"] = canonical_hash(
            {k: v for k, v in intent.items() if k != "sha256"}
        )
    else:
        intent["sha256"] = "f" * 64
    args["revision-intent"].write_text(json.dumps(intent), encoding="utf-8")
    with pytest.raises(AuroraDetailHarnessError):
        AuroraDetailHarnessRunner().run("fork-local-revision", root, args)
    assert not args["session"].exists() and inventory(source_root) == before


@pytest.mark.parametrize(
    "tamper",
    ["other_pair", "feedback", "approval", "cycle_reset", "old_attempt", "local_delta"],
)
def test_repeated_revision_fork_rejects_changed_existing_truth_without_reopening(
    accepted_delivery,
    tamper,
):
    source_root, _, root, args, _ = fork_fixture(accepted_delivery)
    runner = AuroraDetailHarnessRunner()
    runner.run("fork-local-revision", root, args)
    current = json.loads(args["session"].read_bytes())
    if tamper == "other_pair":
        current["modules"][2]["residual_issues"] = ["changed accepted other screen"]
    elif tamper == "feedback":
        current["feedback_constraints"]["global"]["preserve"] = [
            "unauthorized global layout"
        ]
    elif tamper == "approval":
        current["approval"]["delivery_mode"] = "AUTO_FINALIZE_AUTHORIZED"
    elif tamper == "cycle_reset":
        current["modules"][0]["cycle_id"] = 9
    elif tamper == "old_attempt":
        current["modules"][0]["attempts"][0]["image"] = "altered original"
    elif tamper == "local_delta":
        current["modules"][0]["user_adjustment"]["change_only"] = [
            "different local instruction"
        ]
    args["session"].write_text(json.dumps(current), encoding="utf-8")
    before, source_before = args["session"].read_bytes(), inventory(source_root)
    with pytest.raises(AuroraDetailHarnessError):
        runner.run("fork-local-revision", root, args)
    assert (
        args["session"].read_bytes() == before
        and inventory(source_root) == source_before
    )


def test_fork_retry_cannot_reset_two_recorded_attempts(accepted_delivery):
    source_root, _, root, args, _ = fork_fixture(accepted_delivery)
    runner = AuroraDetailHarnessRunner()
    runner.run("fork-local-revision", root, args)
    current = json.loads(args["session"].read_bytes())
    for module in current["modules"][:2]:
        module["attempts"].extend(
            [{"cycle_id": 8, "cycle_attempt": 1}, {"cycle_id": 8, "cycle_attempt": 2}]
        )
        module["cycle_attempt_count"] = 2
    args["session"].write_text(json.dumps(current), encoding="utf-8")
    before, source_before = args["session"].read_bytes(), inventory(source_root)
    assert runner.run("fork-local-revision", root, args)["reused"] is True
    assert args["session"].read_bytes() == before
    current["modules"][0]["cycle_attempt_count"] = 0
    args["session"].write_text(json.dumps(current), encoding="utf-8")
    with pytest.raises(AuroraDetailHarnessError):
        runner.run("fork-local-revision", root, args)
    assert inventory(source_root) == source_before


def test_website_runner_still_rejects_source_mutating_adjustment(accepted_delivery):
    source_root, _, root, args, _ = fork_fixture(accepted_delivery)
    before = inventory(source_root)
    with pytest.raises(AuroraDetailHarnessError) as error:
        AuroraDetailHarnessRunner().run(
            "apply-user-adjustment",
            source_root,
            {
                "session": source_root / "session.json",
                "adjustment": args["revision-intent"],
            },
        )
    assert error.value.code == "HARNESS_COMMAND_NOT_ALLOWED"
    assert inventory(source_root) == before
