import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.replica_canvas import LEGACY_POLICY, native_budget_matches, receipt_matches
from app.replica_native import ReplicaNativeError, digest, read_json, write_json
from app.replica_retained_canvas import promote_retained, retained_module
from tests.test_replica_canvas import native_renderer
from tests.test_replica_resolution import result


@pytest.mark.parametrize(
    "expected,actual",
    [
        ((1440, 1440), (1254, 1254)),
        ((2048, 3246), (996, 1579)),
        ((1024, 1623), (768, 1217)),
        ((1024, 1623), (966, 1531)),
    ],
)
def test_proportional_native_file_is_delivered_without_format_regeneration(
    tmp_path, expected, actual
):
    response = result(actual)
    run, render, calls = native_renderer(tmp_path, [response], expected)
    path = asyncio.run(render())
    assert path.read_bytes() == response.artifact.payload
    assert asyncio.run(render()) == path
    assert len(calls) == 1
    receipt = read_json(run.root / "invocations.json")["native-budget"][
        "delivery_canvas"
    ]
    assert receipt["requested_size"] == list(expected)
    assert receipt["actual_size"] == list(actual)
    assert receipt["locally_resampled"] is False


def test_ratio_rounding_boundary_and_preview_floor():
    assert native_budget_matches([1440, 1440], (1254, 1254))
    assert native_budget_matches([1440, 1440], (1254, 1266))
    assert not native_budget_matches([1440, 1440], (1254, 1270))
    assert not native_budget_matches([1440, 1440], (256, 256))
    assert not native_budget_matches([1440, 1440], (0, 0))
    assert not native_budget_matches([0, 1440], (1254, 1254))


def retained_fixture(tmp_path):
    data = result((1254, 1254)).artifact.payload
    path = tmp_path / "retained.png"
    path.write_bytes(data)
    module = {
        "id": "M001",
        "status": "AWAITING_ARTWORK",
        "review_status": "NOT_REVIEWED",
        "request": {
            "token": "native",
            "module_id": "M001",
            "prompt": "approved prompt",
            "canvas": {"width": 1440, "height": 1440},
        },
    }
    attempt = {
        "path": str(path),
        "sha256": digest(data),
        "size": [1254, 1254],
        "accepted_canvas": False,
    }
    ledger = {
        "native": {
            "state": "canvas_rejected",
            "job_id": "old-job",
            "target": "M001",
            "native_prompt_sha256": digest(b"approved prompt"),
            "path": str(path),
            "sha256": digest(data),
            "actual_size": [1254, 1254],
            "canvas_attempts": [attempt],
        }
    }
    write_json(tmp_path / "invocations.json", ledger)
    text = (tmp_path / "invocations.json").read_text(encoding="utf8")
    files = {
        "invocations.json": {"text": text, "sha256": digest(text.encode())},
        "retained.png": {
            "sha256": digest(data),
            "object_key": "ws/replica/doc/retained.png",
        },
    }
    snapshot = {"root": str(tmp_path), "files": files}
    return snapshot, module, ledger


def test_restored_retained_file_becomes_generated_without_rewriting_history(tmp_path):
    snapshot, module, ledger = retained_fixture(tmp_path)
    before = deepcopy(snapshot)
    state = {
        "modules": [module],
        "plan": {
            "modules": [
                {"id": "M001", "output_canvas": {"width": 1440, "height": 1440}}
            ]
        },
    }
    write_json(tmp_path / "direct-session.json", state)
    run = SimpleNamespace(
        root=tmp_path,
        workspace_id="ws",
        document_id="doc",
        snapshot_files=snapshot["files"],
        path=Path,
    )
    promote_retained(run)
    restored = read_json(tmp_path / "direct-session.json")["modules"][0]
    assert restored["status"] == "GENERATED"
    assert restored["size"] == [1254, 1254]
    assert restored["sha256"] == digest((tmp_path / "retained.png").read_bytes())
    assert restored["recovered_canvas_verdict"] == "canvas_rejected"
    assert snapshot == before
    assert read_json(tmp_path / "invocations.json") == ledger
    promote_retained(run)


@pytest.mark.parametrize(
    "tamper", ["unknown", "target", "hash", "prefix", "prompt", "canvas"]
)
def test_retained_recovery_keeps_unknown_and_tampered_receipts_closed(tmp_path, tamper):
    snapshot, module, ledger = retained_fixture(tmp_path)
    if tamper == "unknown":
        ledger["native"]["state"] = "invoked"
    elif tamper == "target":
        ledger["native"]["target"] = "M002"
    elif tamper == "hash":
        snapshot["files"]["retained.png"]["sha256"] = "wrong"
    elif tamper == "prefix":
        snapshot["files"]["retained.png"]["object_key"] = "other/retained.png"
    elif tamper == "prompt":
        module["request"]["prompt"] = "changed prompt"
    elif tamper == "canvas":
        module["request"]["canvas"]["width"] = 1024
    import json

    text = json.dumps(ledger)
    snapshot["files"]["invocations.json"] = {
        "text": text,
        "sha256": digest(text.encode()),
    }
    if tamper == "unknown":
        assert (
            retained_module(snapshot, module, [1440, 1440], "ws/replica/doc/") is None
        )
    else:
        with pytest.raises(ReplicaNativeError):
            retained_module(snapshot, module, [1440, 1440], "ws/replica/doc/")


def test_historical_delivery_receipt_remains_valid_without_rewriting_policy():
    from app.replica_canvas import delivery_receipt

    receipt = delivery_receipt([1024, 1623], (996, 1579), "hash")
    receipt["policy"] = LEGACY_POLICY
    original = deepcopy(receipt)
    assert receipt_matches(receipt, [1024, 1623], (996, 1579), "hash")
    assert receipt == original
    receipt["sha256"] = "changed"
    assert not receipt_matches(receipt, [1024, 1623], (996, 1579), "hash")


def test_http_retry_recovers_retained_first_screen_and_buys_only_missing_screen(
    client, monkeypatch, tmp_path
):
    from uuid import uuid4

    import app.replica_canvas as policy
    from tests.test_api import dispatch_pending
    from tests.test_replica_partial_delivery import create_case

    gateway, route, did, jid = create_case(
        client, monkeypatch, tmp_path, fallback=False
    )
    original_edit = gateway.edit
    calls = []

    async def smaller(request):
        calls.append(request)
        gateway.image_requests.append(request)
        width, height = map(int, request.size.split("x"))
        return result((round(width * 0.8), round(height * 0.8)))

    # Reproduce the previous resolution floor, with otherwise valid raw pixels.
    monkeypatch.setattr(policy, "MIN_NATIVE_EDGE", 1024)
    gateway.edit = smaller
    dispatch_pending(client)
    old = client.get(f"/api/v1/workspaces/ws_test/jobs/{jid}").json()
    assert old["status"] == "failed" and old["error_code"] == "REPLICA_CANVAS_MISMATCH"
    assert len(calls) == 2

    monkeypatch.setattr(policy, "MIN_NATIVE_EDGE", 512)
    gateway.edit = original_edit
    retried = client.post(
        route + f"/sessions/{did}/jobs/{jid}/retry", json={"request_id": uuid4().hex}
    )
    assert retried.status_code == 202, retried.text
    retry_id = retried.json()["payload"]["job_ids"][0]
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/ws_test/jobs/{retry_id}").json()
    assert current["status"] == "completed", current
    assert current["succeeded_item_count"] == 2
    assert current["items"][0]["result_source"] == "reused"
    assert len(gateway.image_requests) == 3  # 2 retained attempts + 1 missing screen.
