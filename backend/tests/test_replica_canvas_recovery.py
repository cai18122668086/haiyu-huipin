"""Native format correction is bounded, durable and never replaces unknowns."""

import asyncio
from uuid import uuid4

import pytest

from app.provider import ProviderOutcome
from app.replica_failure import failure_fields
from app.replica_native import ReplicaNativeError, read_json, write_json
from app.replica_native_provider import (
    ReplicaCanvasMismatch,
    ReplicaImageOutcomeUnknown,
)
from tests.test_api import dispatch_pending
from tests.test_replica_canvas import native_renderer
from tests.test_replica_partial_delivery import create_case
from tests.test_replica_resolution import result


def correction_renderer(tmp_path, responses, *, save_hook=None):
    run, render, calls = native_renderer(
        tmp_path, responses, (1024, 1447), save_hook=save_hook
    )
    run.binding = {"version": "exact-compact-v9"}
    return run, render, calls


def test_wrong_provider_preset_is_corrected_once_with_original_plan_and_pixels(
    tmp_path,
):
    wrong, correct = result((1086, 1448)), result((1024, 1447))
    run, render, calls = correction_renderer(tmp_path, [wrong, correct])
    delivered = asyncio.run(render())
    assert delivered.read_bytes() == correct.artifact.payload
    assert asyncio.run(render()) == delivered and len(calls) == 2
    first, second = calls
    assert first.idempotency_key != second.idempotency_key
    assert second.idempotency_key.endswith(":format-2")
    assert first.size == second.size == "1024x1447"
    assert first.sources == second.sources
    assert second.prompt.startswith(first.prompt)
    assert "REQUIRED WIDTH/HEIGHT RATIO = 1024/1447" in second.prompt
    ledger = read_json(tmp_path / "invocations.json")["native-budget"]
    assert [a["accepted_canvas"] for a in ledger["canvas_attempts"]] == [False, True]
    assert (
        run.path(ledger["canvas_attempts"][0]["path"]).read_bytes()
        == wrong.artifact.payload
    )
    assert ledger["canvas_correction"]["maximum_automatic_corrections"] == 1
    assert len(run.usage) == 2


def test_repeated_wrong_ratio_stops_at_two_keeps_both_and_never_fakes_acceptance(
    tmp_path,
):
    run, render, calls = correction_renderer(tmp_path, [result((1086, 1448))] * 2)
    for _ in range(2):
        with pytest.raises(ReplicaCanvasMismatch, match="达到次数上限"):
            asyncio.run(render())
    assert len(calls) == 2
    ledger = read_json(tmp_path / "invocations.json")["native-budget"]
    assert ledger["state"] == "canvas_rejected"
    assert len(ledger["canvas_attempts"]) == 2
    assert all(not a["accepted_canvas"] for a in ledger["canvas_attempts"])
    assert "delivery_canvas" not in ledger
    assert all(run.path(a["path"]).exists() for a in ledger["canvas_attempts"])


@pytest.mark.parametrize("first_unknown", [False, True])
def test_unknown_initial_or_correction_task_is_never_replaced(tmp_path, first_unknown):
    unknown = result((1024, 1447), ProviderOutcome.OUTCOME_UNKNOWN)
    responses = [unknown] if first_unknown else [result((1086, 1448)), unknown]
    _, render, calls = correction_renderer(tmp_path, responses)
    for _ in range(2):
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(render())
    assert len(calls) == len(responses)
    assert (
        read_json(tmp_path / "invocations.json")["native-budget"]["state"] == "invoked"
    )


def interrupted_canvas(tmp_path):
    interrupted = False

    async def save(*args, **kwargs):
        nonlocal interrupted
        ledger = read_json(tmp_path / "invocations.json")["native-budget"]
        if ledger["state"] == "canvas_rejected" and not interrupted:
            interrupted = True
            raise RuntimeError(
                "process interrupted after rejected bytes were persisted"
            )

    run, render, calls = correction_renderer(
        tmp_path, [result((1086, 1448)), result((1024, 1447))], save_hook=save
    )
    with pytest.raises(RuntimeError, match="process interrupted"):
        asyncio.run(render())
    assert len(calls) == 1
    return run, render, calls


def test_resume_known_first_canvas_uses_the_one_remaining_correction_only(tmp_path):
    run, render, calls = interrupted_canvas(tmp_path)
    delivered = asyncio.run(render())
    assert asyncio.run(render()) == delivered and len(calls) == 2
    ledger = read_json(tmp_path / "invocations.json")["native-budget"]
    assert len(ledger["canvas_attempts"]) == 2
    assert calls[-1].idempotency_key.endswith(":format-2")
    assert run.path(ledger["canvas_attempts"][0]["path"]).exists()


@pytest.mark.parametrize(
    "tamper",
    [
        "target",
        "native_prompt_sha256",
        "source_bindings",
        "dispatch_prompt_sha256",
        "job_id",
    ],
)
def test_recovery_rejects_changed_binding_before_any_new_purchase(tmp_path, tamper):
    _, render, calls = interrupted_canvas(tmp_path)
    ledger = read_json(tmp_path / "invocations.json")
    ledger["native-budget"][tamper] = "tampered"
    write_json(tmp_path / "invocations.json", ledger)
    with pytest.raises(ReplicaNativeError):
        asyncio.run(render())
    assert len(calls) == 1


def test_old_direct_protocol_keeps_its_original_single_dispatch_boundary(tmp_path):
    run, render, calls = correction_renderer(tmp_path, [result((1086, 1448))])
    run.binding = {"version": "exact-compact-v8"}
    with pytest.raises(ReplicaCanvasMismatch):
        asyncio.run(render())
    assert len(calls) == 1


def test_canvas_validation_failure_keeps_successful_http_receipt_and_dimensions():
    fields = failure_fields(
        ReplicaCanvasMismatch([1024, 1447], (1086, 1448), 2),
        [
            {
                "kind": "image",
                "http_status": 200,
                "metrics": {
                    "provider_attempts": [
                        {"request_id": "original-provider-task", "http_status": 200}
                    ]
                },
            }
        ],
        6,
    )
    assert fields["provider_request_id"] == "original-provider-task"
    assert fields["http_status"] == 200
    assert "达到次数上限" in fields["error_message"]
    assert fields["raw_usage_metrics"]["canvas_validation"] == {
        "requested_size": [1024, 1447],
        "actual_size": [1086, 1448],
        "image_attempts": 2,
    }


def test_public_retry_replaces_only_explicit_failed_canvas_and_reuses_success(
    client, monkeypatch, tmp_path
):
    gateway, route, did, jid = create_case(
        client, monkeypatch, tmp_path, fallback=False
    )
    original = gateway.edit
    wrong = []

    async def wrong_second_screen(request):
        value = await original(request)
        if request.group_key == "M002" and len(wrong) < 2:
            wrong.append(True)
            value.artifact.payload = result((1086, 1448)).artifact.payload
        return value

    gateway.edit = wrong_second_screen
    dispatch_pending(client)
    first = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert first["status"] == "partial", first
    assert first["error_code"] == "REPLICA_CANVAS_MISMATCH"
    assert len(gateway.image_requests) == 3
    preserved = next(
        a["asset_id"] for a in first["assets"] if a["direction"] == "output"
    )
    old_key = gateway.image_requests[-1].idempotency_key
    planned_calls = len(gateway.requests)
    retry = client.post(
        route + f"/sessions/{did}/jobs/{jid}/retry", json={"request_id": uuid4().hex}
    )
    assert retry.status_code == 202, retry.text
    new_id = retry.json()["payload"]["job_ids"][0]
    dispatch_pending(client)
    final = client.get(f"/api/v1/workspaces/ws_test/jobs/{new_id}").json()
    assert final["status"] == "completed", final
    assert len(gateway.image_requests) == 4 and len(gateway.requests) == planned_calls
    assert gateway.image_requests[-1].group_key == "M002"
    assert gateway.image_requests[-1].idempotency_key != old_key
    assert (
        next(
            a["asset_id"]
            for a in final["assets"]
            if a["direction"] == "output" and a["ordinal"] == 0
        )
        == preserved
    )
