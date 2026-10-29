"""Real delivery/persistence boundaries; synthetic upstream responses only."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from app.provider import ProviderOutcome
from app.provider_diagnostics import error_summary, retained_metrics
from app.replica_native import NativeReplicaRun, digest, read_json, write_json
from app.replica_native_provider import NativeReplicaProvider
from tests.test_replica_execution_resume import interrupted_run
from tests.test_replica_nonimage_fallback import dispatch


@pytest.mark.parametrize(
    "code", ["model_not_found", "unsupported_model", "no_available_channel"]
)
def test_explicit_route_rejection_uses_backup_and_preserves_primary_receipt(code):
    result, calls, count = dispatch(
        {"error": {"code": code, "message": "Unavailable model"}}
    )
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert count == len(calls) == 2
    trace = result.raw_usage["metrics"]["provider_attempts"]
    assert trace[0]["error"]["code"] == code
    assert trace[0]["http_status"] == 400 and trace[1]["http_status"] == 200
    assert result.raw_usage["network_requests"] == 2


def test_unknown_400_is_diagnostic_and_never_blindly_resent():
    result, calls, _ = dispatch(
        {
            "error": {
                "code": "invalid_parameter",
                "param": "size",
                "message": "Invalid size",
            }
        }
    )
    assert len(calls) == 1
    assert result.raw_usage["metrics"]["provider_error"]["param"] == "size"


def test_provider_receipt_redacts_secrets_but_keeps_rejection():
    raw = {
        "error": {
            "code": "invalid_image",
            "param": "image",
            "message": "Invalid image; key=sk-secret123 authorization=secret123 "
            "Bearer abc.def https://private.example/?token=secret "
            "data:image/png;base64,AAAA",
        }
    }
    result = error_summary(raw)
    assert result["code"] == "invalid_image"
    assert result["param"] == "image"
    assert "Invalid image" in result["message"]
    assert all(
        value not in str(result)
        for value in ("sk-secret123", "secret123", "abc.def", "private.example", "AAAA")
    )
    assert (
        retained_metrics({"metrics": {"provider_error": raw["error"]}})[
            "provider_error"
        ]
        == result
    )


def test_legacy_exact_worker_delivers_without_audit_or_repeating_existing_image(
    tmp_path,
):
    old, gateway, _, request, _ = interrupted_run(tmp_path)
    # Model the pre-pilot legacy snapshot: same native harness and plan, no
    # compact model binding. Preserve the real failed session byte for byte.
    legacy = NativeReplicaRun(
        "exact", old.document_id, old.workspace_id, gateway, old.storage, root=old.root
    )
    legacy.run, legacy.context = old.run, deepcopy(old.context)
    legacy.context.pop("model_protocol", None)
    write_json(legacy.root / "context.json", legacy.context)
    snapshot = asyncio.run(legacy.snapshot())
    original = (legacy.run / "session.json").read_bytes()
    existing = read_json(legacy.run / "session.json")["modules"][0]["attempts"][-1][
        "artwork"
    ]
    original_image = Path(existing).read_bytes()
    assert "model_protocol" not in snapshot
    count = len(gateway.image_requests)

    async def forbidden(*args, **kwargs):
        raise AssertionError("No post-generation model review is authorized")

    gateway.plan = forbidden
    provider = NativeReplicaProvider(None, gateway, old.storage)
    provider._load = lambda _: (
        {
            "replication_method": "exact",
            "plans": [{}, {}],
            "native_snapshot": snapshot,
            "native_executions": {request.job_id: {"snapshot": snapshot}},
        },
        old.document_id,
        0,
    )
    saved = []

    async def save(run, *args, **kwargs):
        saved.append(await run.snapshot())

    provider._save = save
    result = asyncio.run(provider.execute(request))
    assert result.outcome == ProviderOutcome.SUCCEEDED, result.error_message
    # Delivery is page-local: the already generated first page is reused with
    # zero new image calls. Only explicitly requesting the unfinished second
    # page may purchase its image.
    assert len(gateway.image_requests) == count
    assert result.artifact.payload == original_image
    assert (legacy.run / "session.json").read_bytes() == original
    assert Path(existing).read_bytes() == original_image
    assert result.raw_usage["post_generation_review"] == "disabled"
    assert saved[-1].get("model_protocol") is None
    assert digest(original_image) in saved[-1]["files"]["direct-session.json"]["text"]

    snapshot = saved[-1]
    provider._load = lambda _: (
        {
            "replication_method": "exact",
            "plans": [{}, {}],
            "native_snapshot": snapshot,
            "native_executions": {request.job_id: {"snapshot": snapshot}},
        },
        old.document_id,
        1,
    )
    second = asyncio.run(provider.execute(request))
    assert second.outcome == ProviderOutcome.SUCCEEDED, second.error_message
    assert len(gateway.image_requests) == count + 1
    assert (legacy.run / "session.json").read_bytes() == original
    assert Path(existing).read_bytes() == original_image
