"""Native review forks preserve accepted truth and original source bytes."""

from __future__ import annotations

import hashlib
import json

import pytest

from app.aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
)

pytest_plugins = ("tests.test_native_detail_delivery_manifest",)


def fork_fixture(accepted_delivery):
    source_root, source, state = accepted_delivery
    target_root = source_root.parent / "owned-review"
    target_root.mkdir()
    snapshot = target_root / "source-session.json"
    snapshot.write_bytes(source.read_bytes())
    arguments = {
        "session": target_root / "session.json",
        "source-session": snapshot,
        "source-session-sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
    }
    return source, state, target_root, arguments


def test_review_fork_reuses_originals_and_does_not_grant_new_delivery_authority(
    accepted_delivery,
):
    source, state, root, args = fork_fixture(accepted_delivery)
    original = source.read_bytes()
    runner = AuroraDetailHarnessRunner()
    first = runner.run("fork-campaign-review", root, args)
    target = args["session"]
    before = target.read_bytes()
    value = json.loads(before)
    assert first["reused"] is False
    assert value["modules"] == state["modules"] and value["plan"] == state["plan"]
    assert value["approval"] == state["approval"]
    assert value["state"] == "AWAITING_USER_FINAL_REVIEW"
    assert (
        value["assembly"] is None
        and value["long_page_audit"] is None
        and value["final"] is None
    )
    assert runner.run("fork-campaign-review", root, args)["reused"] is True
    assert target.read_bytes() == before and source.read_bytes() == original


@pytest.mark.parametrize("field", ["plan", "input", "approval", "modules"])
def test_review_fork_rejects_changed_existing_truth(accepted_delivery, field):
    source, _, root, args = fork_fixture(accepted_delivery)
    runner = AuroraDetailHarnessRunner()
    runner.run("fork-campaign-review", root, args)
    target = args["session"]
    value = json.loads(target.read_bytes())
    if field == "modules":
        value[field][0]["residual_issues"] = ["changed original residual"]
    else:
        value[field]["changed_original"] = True
    target.write_text(json.dumps(value), encoding="utf-8")
    changed, original = target.read_bytes(), source.read_bytes()
    with pytest.raises(AuroraDetailHarnessError):
        runner.run("fork-campaign-review", root, args)
    assert target.read_bytes() == changed and source.read_bytes() == original


def test_review_fork_rejects_changed_source_bytes_before_any_target_write(
    accepted_delivery,
):
    source, _, root, args = fork_fixture(accepted_delivery)
    snapshot = args["source-session"]
    snapshot.write_bytes(snapshot.read_bytes() + b" ")
    with pytest.raises(AuroraDetailHarnessError):
        AuroraDetailHarnessRunner().run("fork-campaign-review", root, args)
    assert not args["session"].exists()
    assert source.exists()
