"""Direct native canvases retain pixels and can resume their original token."""

import asyncio
import math
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.replica_canvas import NATIVE_PIXEL_BUDGET, POLICY, native_budget_matches
from app.replica_direct_delivery import (
    V5_BINDING,
    V6_BINDING,
    V7_BINDING,
    V8_BINDING,
    V9_BINDING,
    V10_BINDING,
    V11_BINDING,
    V12_BINDING,
    V13_BINDING,
    V14_BINDING,
    V15_BINDING,
    V16_BINDING,
    inspect_artwork,
)
from app.replica_exact_compact import protocol_binding, run_class
from app.replica_native import ReplicaNativeError, digest, read_json, write_json
from app.replica_native_provider import (
    ReplicaCanvasMismatch,
    ReplicaImageOutcomeUnknown,
)
from tests.test_replica_resolution import renderer, result


def native_renderer(
    tmp_path, responses, canvas=(1024, 1623), *, direct=True, save_hook=None
):
    run, provider, _, calls = renderer(tmp_path, responses)
    run.direct_execution = direct
    if save_hook is not None:
        provider._save = save_hook
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_fixture",
        job_item_id="item_fixture",
        attempt_id="attempt_fixture",
    )

    def render():
        return provider._render(
            run,
            request,
            {"token": "native-budget"},
            "Native prompt",
            [],
            list(canvas),
            "M006",
        )

    return run, render, calls


def test_native_portrait_is_delivered_and_replayed_without_touching_bytes(tmp_path):
    response = result((996, 1579))
    run, render, calls = native_renderer(tmp_path, [response])
    path = asyncio.run(render())
    assert path.read_bytes() == response.artifact.payload
    assert asyncio.run(render()) == path and len(calls) == 1
    ledger = read_json(tmp_path / "invocations.json")["native-budget"]
    assert ledger["state"] == "canvas_rejected"
    assert ledger["canvas_attempts"][0]["accepted_canvas"] is False
    assert ledger["delivery_canvas"]["policy"] == POLICY
    assert ledger["delivery_canvas"]["actual_size"] == [996, 1579]
    assert ledger["delivery_canvas"]["locally_resampled"] is False
    record = inspect_artwork(run, str(path), {"width": 1024, "height": 1623})
    assert record["size"] == [996, 1579]
    assert record["delivery_canvas"] == ledger["delivery_canvas"]


@pytest.mark.parametrize("tamper", [None, "hash", "target", "prompt", "size"])
def test_existing_rejected_token_resumes_only_its_verified_native_bytes(
    tmp_path, tamper
):
    response = result((996, 1579))
    run, render, calls = native_renderer(tmp_path, [])
    path = tmp_path / "original.png"
    path.write_bytes(response.artifact.payload)
    prior = {
        "state": "canvas_rejected",
        "path": str(path),
        "sha256": digest(path.read_bytes()),
        "target": "M006",
        "native_prompt_sha256": digest(b"Native prompt"),
        "actual_size": [996, 1579],
        "canvas_attempts": [{"accepted_canvas": False, "size": [996, 1579]}],
    }
    original = deepcopy(prior)
    if tamper == "hash":
        path.write_bytes(result((996, 1579)).artifact.payload + b"changed")
    elif tamper == "target":
        prior["target"] = "M007"
    elif tamper == "prompt":
        prior["native_prompt_sha256"] = digest(b"Other prompt")
    elif tamper == "size":
        prior["actual_size"] = [1024, 1623]
    write_json(tmp_path / "invocations.json", {"native-budget": prior})
    if tamper:
        with pytest.raises(ReplicaNativeError):
            asyncio.run(render())
    else:
        assert asyncio.run(render()) == path
        assert asyncio.run(render()) == path
        accepted = read_json(tmp_path / "invocations.json")["native-budget"]
        assert {k: accepted[k] for k in original} == original
        assert accepted["delivery_canvas"]["sha256"] == digest(
            response.artifact.payload
        )
    assert calls == []


@pytest.mark.parametrize(
    "canvas,size",
    [
        ((1024, 1623), (256, 405)),
        ((1024, 1623), (1254, 1254)),
    ],
)
def test_small_preview_and_wrong_aspect_still_stop(
    tmp_path, canvas, size
):
    _, render, calls = native_renderer(tmp_path, [result(size)], canvas)
    with pytest.raises(ReplicaCanvasMismatch):
        asyncio.run(render())
    with pytest.raises(ReplicaCanvasMismatch):
        asyncio.run(render())
    assert len(calls) == 1


def test_legacy_visual_review_does_not_adopt_the_direct_delivery_policy(tmp_path):
    _, render, calls = native_renderer(
        tmp_path, [result((996, 1579))] * 2, direct=False
    )
    with pytest.raises(ReplicaCanvasMismatch):
        asyncio.run(render())
    assert len(calls) == 2


def test_unknown_request_is_not_replaced_by_native_budget_admission(tmp_path):
    response = result((996, 1579), "outcome_unknown")
    _, render, calls = native_renderer(tmp_path, [response])
    for _ in range(2):
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(render())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "canvas",
    [
        (1024, 1024),
        (1024, 1623),
        (1024, 1552),
        (1024, 1585),
        (1264, 1024),
        (1024, 1747),
        (1024, 1545),
        (1024, 1752),
        (1024, 1758),
        (1055, 1024),
        (1024, 1361),
        (1526, 1024),
        (1024, 1239),
        (1024, 1303),
        (1024, 1580),
        (1024, 1659),
        (1024, 1447),
    ],
)
def test_every_failed_task_aspect_keeps_its_complete_native_canvas(tmp_path, canvas):
    width = round(math.sqrt(NATIVE_PIXEL_BUDGET * canvas[0] / canvas[1]))
    height = round(width * canvas[1] / canvas[0])
    response = result((width, height))
    run, render, calls = native_renderer(tmp_path, [response], canvas)
    path = asyncio.run(render())
    record = inspect_artwork(
        run, str(path), dict(zip(("width", "height"), canvas, strict=True))
    )
    assert record["size"] == [width, height]
    assert path.read_bytes() == response.artifact.payload and len(calls) == 1
    assert native_budget_matches(list(canvas), (width, height))


def test_deployed_protocols_keep_their_original_hashes_and_new_runs_use_v17():
    for binding in (
        V5_BINDING,
        V6_BINDING,
        V7_BINDING,
        V8_BINDING,
        V9_BINDING,
        V10_BINDING,
        V11_BINDING,
        V12_BINDING,
        V13_BINDING,
        V14_BINDING,
        V15_BINDING,
        V16_BINDING,
    ):
        cls = run_class("exact", binding)
        assert sys.modules[cls.__module__].protocol_binding() == binding
    assert protocol_binding()["version"] == "exact-compact-v17"
