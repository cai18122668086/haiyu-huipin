import asyncio
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.provider import ProviderOutcome
from app.replica_native import digest, read_json, write_json
from app.replica_native_provider import (
    NativeReplicaProvider,
    ReplicaCanvasMismatch,
    ReplicaImageOutcomeUnknown,
)

from .test_replica_retry import render_fixture
from .test_replica_transport import run_at


def result(size, outcome=ProviderOutcome.SUCCEEDED):
    data = BytesIO()
    Image.new("RGB", size, "pink").save(data, "PNG")
    return SimpleNamespace(
        outcome=outcome,
        artifact=SimpleNamespace(payload=data.getvalue(), locally_resampled=False),
        normalized_usage={},
        actual_cost_micros=None,
        estimated_cost_micros=None,
        http_status=200,
        error_code=None,
        error_message="Unknown request outcome",
        raw_usage={},
    )


def renderer(tmp_path, responses, *, method="exact"):
    calls = []

    async def edit(request):
        calls.append(request)
        return responses[len(calls) - 1]

    run = run_at(tmp_path, method, SimpleNamespace(edit=edit))
    provider = NativeReplicaProvider(None, run.gateway, None)

    async def save(*args, **kwargs):
        pass

    provider._save = save
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
            {"token": "resolution-test"},
            "Native prompt",
            [],
            [64, 80],
            "M001",
        )

    return run, provider, render, calls


@pytest.mark.parametrize("unknown_second", [False, True])
def test_correct_ratio_preview_needs_native_resolution_correction(
    tmp_path, unknown_second
):
    low = result((32, 40))
    full = result(
        (64, 80),
        ProviderOutcome.OUTCOME_UNKNOWN
        if unknown_second
        else ProviderOutcome.SUCCEEDED,
    )
    run, _, render, calls = renderer(tmp_path, [low, full])
    if unknown_second:
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(render())
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(render())
    else:
        path = asyncio.run(render())
        assert path.read_bytes() == full.artifact.payload
        assert asyncio.run(render()) == path
    assert len(calls) == 2
    assert calls[0].idempotency_key != calls[1].idempotency_key
    assert "FORMAT CORRECTION" in calls[1].prompt
    assert len(run.usage) == 2
    ledger = read_json(tmp_path / "invocations.json")["resolution-test"]
    first = ledger["canvas_attempts"][0]
    assert first["accepted_canvas"] is False
    assert run.path(first["path"]).read_bytes() == low.artifact.payload


def test_second_undersized_canvas_is_preserved_and_cannot_be_reissued(tmp_path):
    low = result((32, 40))
    _, _, render, calls = renderer(tmp_path, [low, low])
    for _ in range(2):
        with pytest.raises(ReplicaCanvasMismatch, match="分辨率"):
            asyncio.run(render())
    assert len(calls) == 2
    ledger = read_json(tmp_path / "invocations.json")["resolution-test"]
    assert ledger["state"] == "canvas_rejected"
    assert [r["accepted_canvas"] for r in ledger["canvas_attempts"]] == [False, False]


@pytest.mark.parametrize("method,size", [("exact", (128, 160)), ("style", (32, 40))])
def test_larger_native_result_and_existing_style_contract_keep_original_bytes(
    tmp_path, method, size
):
    response = result(size)
    _, _, render, calls = renderer(tmp_path, [response], method=method)
    assert asyncio.run(render()).read_bytes() == response.artifact.payload
    assert len(calls) == 1


def test_previously_recorded_undersized_result_stops_before_another_call(tmp_path):
    low = result((32, 40))
    _, _, render, calls = renderer(tmp_path, [])
    path = tmp_path / "prior.png"
    path.write_bytes(low.artifact.payload)
    write_json(
        tmp_path / "invocations.json",
        {
            "resolution-test": {
                "state": "returned",
                "path": str(path),
                "sha256": digest(low.artifact.payload),
            }
        },
    )
    with pytest.raises(ReplicaCanvasMismatch):
        asyncio.run(render())
    assert calls == []


def test_old_small_canvas_is_only_an_edit_input_and_fresh_result_keeps_bytes(tmp_path):
    run, provider, request, native, candidate, old, events = render_fixture(tmp_path)
    reference = tmp_path / "reference.png"
    Image.new("RGB", (128, 160), "blue").save(reference)
    full = result((128, 160))
    calls = []

    async def edit(value):
        calls.append(value)
        return full

    run.gateway.edit = edit
    prompt = "Reference image 1 is the binding composition blueprint;"

    def render():
        return provider._render(
            run, request, native, prompt, [reference], [128, 160], "M001", candidate
        )

    path = asyncio.run(render())
    assert path.read_bytes() == full.artifact.payload
    assert asyncio.run(render()) == path and len(calls) == 1
    assert events.count("cache_read") == 1
    assert calls[0].sources[0].payload == old
    assert "NATIVE RESOLUTION RECOVERY" in calls[0].prompt
    assert "Reference image 2 is the binding composition blueprint;" in calls[0].prompt
    bindings = json.loads(
        calls[0].prompt.split("IMAGE_BINDINGS=", 1)[1].split("\n", 1)[0]
    )
    assert bindings[0]["role"] == "current_artwork"
    assert bindings[1]["role"] == "composition_reference"
    assert [r["kind"] for r in run.usage] == ["image"]
    ledger = read_json(tmp_path / "invocations.json")[native["token"]]
    assert ledger["resolution_recovery"]["source_job_id"] == "job_old"
    assert ledger["resolution_recovery"]["actual_size"] == [64, 80]
    assert ledger["resolution_recovery"]["required_short_edge"] == 128


def test_small_retry_input_cannot_reissue_an_unknown_native_redraw(tmp_path):
    run, provider, request, native, candidate, _, events = render_fixture(tmp_path)
    reference = tmp_path / "reference.png"
    Image.new("RGB", (128, 160), "blue").save(reference)
    calls = []

    async def edit(value):
        calls.append(value)
        return result((128, 160), ProviderOutcome.OUTCOME_UNKNOWN)

    run.gateway.edit = edit
    for _ in range(2):
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(
                provider._render(
                    run,
                    request,
                    native,
                    "Reference image 1 is the binding composition blueprint;",
                    [reference],
                    [128, 160],
                    "M001",
                    candidate,
                )
            )
    assert len(calls) == 1 and events.count("cache_read") == 1
    ledger = read_json(tmp_path / "invocations.json")[native["token"]]
    assert ledger["state"] == "invoked"
    assert ledger["resolution_recovery"]["source_job_id"] == "job_old"


def test_resumed_small_artwork_never_reaches_the_pixel_auditor(tmp_path):
    run, provider, request, native, _, data, events = render_fixture(tmp_path)
    draft = tmp_path / "small.png"
    draft.write_bytes(data)
    state = {
        "state": "AWAITING_AUDIT",
        "module_index": 0,
        "plan": {
            "modules": [
                {
                    "id": "M001",
                    "output_canvas": {
                        "width": 128,
                        "height": 160,
                    },
                }
            ]
        },
        "modules": [{"attempts": [{"artwork": str(draft)}]}],
    }
    run.state = lambda: state
    write_json(tmp_path / "request.json", native)

    async def unexpected_audit(*args, **kwargs):
        raise AssertionError("Undersized artwork must stop before paid pixel review")

    run.contract = unexpected_audit
    with pytest.raises(ReplicaCanvasMismatch):
        asyncio.run(provider._exact(run, request))
    assert events == []
