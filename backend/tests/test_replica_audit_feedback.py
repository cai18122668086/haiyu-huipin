"""Known pixel failures survive retries without supplying an inherited verdict."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.replica_native import ReplicaNativeError, read_json
from app.replica_retry import prior_pixel_reviews, retry_artwork_candidates

from .test_replica_audit_evidence import fixture
from .test_replica_retry import candidates, json_record, snapshots


def passing_parent(previous):
    parent = deepcopy(previous)
    state = json.loads(parent["files"]["session.json"]["text"])
    state["modules"][0]["status"] = "COMPLETE"
    judgment = state["modules"][0]["attempts"][0]["judgment"]
    judgment.update(decision="PASS", derived_decision="PASS", major_mismatches=[])
    parent["files"]["session.json"] = json_record(state)
    return parent


def from_history(previous, current, older):
    return retry_artwork_candidates(
        previous,
        current,
        "job_parent",
        "ws_test/replica/rep_fixture/",
        history={"job_ancestor": older},
    )["M001"]


def test_same_pixel_failure_survives_a_later_pass_without_mutating_history(tmp_path):
    old, current, _ = snapshots(tmp_path)
    original = deepcopy(old)
    candidate = from_history(passing_parent(old), current, old)
    assert len(candidate.prior_reviews) == 1
    review = candidate.prior_reviews[0]
    assert review.source_job_id == "job_ancestor"
    assert review.artwork_sha256 == candidate.sha256
    assert review.observations == ("Real eye defect",)
    assert old == original


@pytest.mark.parametrize("different", ["pixels", "plan", "context", "unfinished"])
def test_unrelated_old_failures_do_not_taint_current_pixels(tmp_path, different):
    old, current, _ = snapshots(tmp_path)
    parent = passing_parent(old)
    state = json.loads(old["files"]["session.json"]["text"])
    if different == "pixels":
        state["modules"][0]["attempts"][0]["judgment"]["artwork_sha256"] = "d" * 64
        old["files"]["artwork/real.png"]["sha256"] = "d" * 64
    elif different == "plan":
        state["plan_sha256"] = "d" * 64
    elif different == "context":
        old["files"]["context.json"] = json_record({"user_facts": "other"})
    else:
        state["state"] = "AWAITING_AUDIT"
    old["files"]["session.json"] = json_record(state)
    assert from_history(parent, current, old).prior_reviews == ()


@pytest.mark.parametrize("bad", ["workspace", "missing_observations", "hash"])
def test_corrupt_historical_failure_binding_stops_the_retry(tmp_path, bad):
    old, current, _ = snapshots(tmp_path)
    parent = passing_parent(old)
    if bad == "workspace":
        old["files"]["artwork/real.png"]["object_key"] = "other/secret"
    elif bad == "hash":
        old["files"]["artwork/real.png"]["sha256"] = "d" * 64
    else:
        state = json.loads(old["files"]["session.json"]["text"])
        state["modules"][0]["attempts"][0]["judgment"]["major_mismatches"] = []
        old["files"]["session.json"] = json_record(state)
    with pytest.raises(ReplicaNativeError):
        from_history(parent, current, old)


def test_repaired_artwork_is_checked_against_both_retry_and_current_failure(tmp_path):
    previous, current, _ = snapshots(tmp_path)
    candidate = candidates(previous, current)["M001"]
    module = {
        "attempts": [
            {
                "judgment": {
                    "derived_decision": "FAIL",
                    "artwork_sha256": "e" * 64,
                    "major_mismatches": ["Evidence block remains too low"],
                }
            }
        ]
    }
    reviews = prior_pixel_reviews(candidate, module, "job_current")
    assert [x["source_job_id"] for x in reviews] == ["job_old", "job_current"]
    assert reviews[1]["artwork_sha256"] == "e" * 64
    assert all("decision" not in row for row in reviews)


def test_failed_comparison_evidence_is_preserved_without_a_major_summary(tmp_path):
    old, current, _ = snapshots(tmp_path)
    state = json.loads(old["files"]["session.json"]["text"])
    judgment = state["modules"][0]["attempts"][0]["judgment"]
    judgment.update(
        major_mismatches=[],
        comparisons=[{"match": False, "evidence": "Synthetic subject is too tall"}],
    )
    old["files"]["session.json"] = json_record(state)
    result = candidates(old, current)["M001"]
    assert result.prior_reviews[0].observations == ("Synthetic subject is too tall",)


def verdict(passed):
    return {
        "decision": "PASS" if passed else "FAIL",
        "major_mismatches": [] if passed else ["Evidence block remains too low"],
        "product_geometry_match": True,
        "comparisons": [{"dimension": str(n), "match": passed} for n in range(10)],
    }


@pytest.mark.parametrize("second_passes", [True, False])
def test_known_concerns_get_an_independent_review_and_both_raw_responses_survive(
    tmp_path, second_passes
):
    first, second = verdict(True), verdict(second_passes)
    run, calls, received, context, reference, artwork = fixture(tmp_path, first)
    context["prior_pixel_reviews"] = [{"observations": ["Evidence block too low"]}]

    async def plan(request):
        calls.append(request)
        if len(calls) == 2:
            payload = json.loads(request.product_information[0])
            assert "decision" not in json.dumps(payload["context"])
        return SimpleNamespace(
            payload=first if len(calls) == 1 else second,
            error_code=None,
            normalized_usage={"reported": False},
            actual_cost_micros=None,
        )

    run.gateway.plan = plan
    value = asyncio.run(
        run.contract(
            "exact-pixel-audit-test",
            "Observe actual pixels",
            context,
            "audit-current",
            "judgment",
            paths=[reference, artwork],
        )
    )
    assert len(calls) == len(run.usage) == 2
    assert len(received) == 1 and received[0] == value
    assert value["decision"] == second["decision"]
    saved = [
        read_json(p)
        for p in (tmp_path / "contracts").glob("exact-pixel-audit-test-*.json")
    ]
    assert len(saved) == 2
    assert sorted(x["decision"] for x in saved) == sorted(["PASS", second["decision"]])


@pytest.mark.parametrize("has_concerns,passes", [(False, True), (True, False)])
def test_no_extra_review_without_a_positive_result_and_known_concerns(
    tmp_path, has_concerns, passes
):
    run, calls, received, context, reference, artwork = fixture(
        tmp_path, verdict(passes)
    )
    if has_concerns:
        context["prior_pixel_reviews"] = [{"observations": ["Evidence too low"]}]
    asyncio.run(
        run.contract(
            "exact-pixel-audit-test",
            "Observe pixels",
            context,
            "audit-current",
            "judgment",
            paths=[reference, artwork],
        )
    )
    assert len(calls) == len(received) == len(run.usage) == 1


def test_unavailable_independent_review_never_applies_the_primary_pass(tmp_path):
    run, calls, received, context, reference, artwork = fixture(tmp_path, verdict(True))
    context["prior_pixel_reviews"] = [{"observations": ["Evidence too low"]}]

    async def plan(request):
        calls.append(request)
        return SimpleNamespace(
            payload=verdict(True) if len(calls) == 1 else None,
            error_code=None if len(calls) == 1 else "UPSTREAM_TIMEOUT",
            normalized_usage={"reported": False},
            actual_cost_micros=None,
        )

    run.gateway.plan = plan
    with pytest.raises(ReplicaNativeError):
        asyncio.run(
            run.contract(
                "exact-pixel-audit-test",
                "Observe pixels",
                context,
                "audit-current",
                "judgment",
                paths=[reference, artwork],
            )
        )
    assert len(calls) == 2 and received == []
    saved = list((tmp_path / "contracts").glob("exact-pixel-audit-test-*.json"))
    assert len(saved) == 1


@pytest.mark.parametrize("malformed", [None, "ten chars!", [None] * 10])
def test_malformed_pass_is_corrected_by_native_validation_before_independent_review(
    tmp_path, malformed
):
    bad = {**verdict(True), "comparisons": malformed}
    run, calls, received, context, reference, artwork = fixture(tmp_path, bad)
    context["prior_pixel_reviews"] = [{"observations": ["Evidence too low"]}]
    original_cli = run.cli

    def cli(command, **kwargs):
        value = read_json(kwargs["judgment"])
        if value["comparisons"] == malformed:
            raise ReplicaNativeError("Synthetic native schema rejection")
        original_cli(command, **kwargs)

    async def plan(request):
        calls.append(request)
        return SimpleNamespace(
            payload=bad if len(calls) == 1 else verdict(True),
            error_code=None,
            normalized_usage={"reported": False},
            actual_cost_micros=None,
        )

    run.cli, run.gateway.plan = cli, plan
    value = asyncio.run(
        run.contract(
            "exact-pixel-audit-test",
            "Observe pixels",
            context,
            "audit-current",
            "judgment",
            paths=[reference, artwork],
        )
    )
    assert len(calls) == 3 and len(received) == 1
    assert value["decision"] == "PASS" and len(value["comparisons"]) == 10
