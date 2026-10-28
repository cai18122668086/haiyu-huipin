"""Creation rollback preserves existing native ownership and raw unrelated bodies."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from app import legacy_detail_proxy
from app.config import Settings
from app.legacy_detail_proxy import LegacyDetailProxy
from tests.test_api import WORKSPACE_ID, upload_asset
from tests.test_domestic_detail_execution import _create_detail_job
from tests.test_legacy_detail_proxy import FrozenAPI


async def echo(request: Request):
    return Response(await request.body(), headers={"X-Runtime": "current"})


def wrapped(client, frozen, monkeypatch, *, enabled=False):
    monkeypatch.setattr(
        legacy_detail_proxy,
        "upstream_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(frozen.handle)),
    )
    app = Starlette(
        routes=[
            Route("/api/v1/workspaces/{workspace}/jobs", echo, methods=["POST"]),
            Route("/api/v1/workspaces/{workspace}/jobs/{job}", echo),
        ]
    )
    return LegacyDetailProxy(
        app,
        origin="http://legacy-api:8000",
        session_factory=client.app.state.session_factory,
        native_creation_enabled=enabled,
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_rollback_switch_preserves_new_domestic_request_bytes_and_key(
    client, monkeypatch, enabled
):
    frozen = FrozenAPI()
    raw = (
        b'{ "type": "detail_pack", '
        b'"configuration": {"market_region":"domestic"}, "intent":"submit" }'
    )
    with TestClient(wrapped(client, frozen, monkeypatch, enabled=enabled)) as routed:
        response = routed.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs?scope=original%2Fscope",
            content=raw,
            headers={
                "Idempotency-Key": "same-original-key",
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == (200 if enabled else 202)
    if enabled:
        assert response.content == raw and not frozen.calls
    else:
        assert len(frozen.calls) == 1
        assert frozen.calls[0].content == raw
        assert frozen.calls[0].headers["Idempotency-Key"] == "same-original-key"
        assert frozen.calls[0].url.query == b"scope=original%2Fscope"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"type":"domestic_main_image","configuration":{"market_region":"domestic"}}',
        b'{"type":"detail_pack","configuration":{"market_region":"international"}}',
        b'{"type":"detail_pack","configuration":{}}',
        b'{"type":"detail_pack","configuration":null}',
        b'["domestic","detail_pack"]',
        b"{invalid json",
        b"\xff\x00",
    ],
)
def test_unrelated_creation_and_invalid_json_retain_exact_body(
    client, monkeypatch, raw
):
    frozen = FrozenAPI()
    with TestClient(wrapped(client, frozen, monkeypatch)) as routed:
        response = routed.post(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs", content=raw)
    assert response.status_code == 200
    assert response.headers["X-Runtime"] == "current"
    assert response.content == raw and not frozen.calls


def test_existing_native_job_stays_on_native_runtime_during_creation_rollback(
    client, monkeypatch
):
    source = upload_asset(client)
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    frozen = FrozenAPI()
    with TestClient(wrapped(client, frozen, monkeypatch)) as routed:
        response = routed.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}")
    assert response.status_code == 200 and response.headers["X-Runtime"] == "current"
    assert not frozen.calls


def test_creation_rollback_cannot_silently_ignore_missing_frozen_runtime():
    settings = Settings(_env_file=None, domestic_detail_native_creation_enabled=False)
    with pytest.raises(RuntimeError, match="requires its frozen API"):
        settings.validate_runtime_safety()


def test_refined_creation_never_falls_back_to_a_different_legacy_skill(
    client, monkeypatch
):
    frozen = FrozenAPI()
    with TestClient(wrapped(client, frozen, monkeypatch)) as routed:
        response = routed.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
            json={
                "type": "detail_pack",
                "configuration": {
                    "market_region": "domestic",
                    "detail_skill": "refined",
                },
            },
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REFINED_DETAIL_SERVICE_UNAVAILABLE"
    assert not frozen.calls


@pytest.mark.parametrize("enabled", [False, True])
def test_auxiliary_api_can_start_without_touching_crawl_recovery(
    settings, monkeypatch, enabled
):
    from app import main

    recovered, resumed = [], []
    monkeypatch.setattr(
        main,
        "recover_interrupted_crawl_tasks",
        lambda factory: recovered.append(factory),
    )
    monkeypatch.setattr(
        main, "resume_queued_crawl_tasks", lambda app, factory: resumed.append(factory)
    )
    candidate = main.create_app(
        settings.model_copy(update={"ecommerce_startup_recovery_enabled": enabled})
    )
    with TestClient(candidate) as api:
        assert api.get("/health/ready").status_code == 200
    assert len(recovered) == int(enabled)
    assert len(resumed) == int(enabled)
