"""Frozen-job HTTP compatibility without provider calls or production data."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import legacy_detail_proxy
from app.config import Settings
from app.main import create_app
from app.models import JobRecord
from tests.test_api import WORKSPACE_ID, upload_asset
from tests.test_domestic_detail_execution import _create_detail_job


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        for part in (b"PK\x03\x04", b"frozen-result", b"\x00\xff"):
            yield part


@dataclass
class FrozenAPI:
    calls: list[httpx.Request] = field(default_factory=list)
    unavailable: bool = False

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.unavailable:
            raise httpx.ConnectError("isolated upstream unavailable", request=request)
        if request.url.path.endswith("/download"):
            return httpx.Response(
                200,
                headers=[
                    ("Content-Type", "application/zip"),
                    ("Content-Length", "19"),
                    ("Content-Disposition", 'attachment; filename="original.zip"'),
                    ("Set-Cookie", "first=1; HttpOnly"),
                    ("Set-Cookie", "second=2; HttpOnly"),
                    ("Connection", "keep-alive, x-internal"),
                    ("X-Internal", "omit-me"),
                ],
                stream=Chunks(),
            )
        return httpx.Response(
            202,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(
                json.dumps(
                    {"runtime": "frozen", "original_body": request.content.decode()}
                ).encode()
            ),
        )


@contextmanager
def isolated(
    settings: Settings, monkeypatch
) -> Iterator[tuple[TestClient, FrozenAPI, str]]:
    frozen = FrozenAPI()
    monkeypatch.setattr(
        legacy_detail_proxy,
        "upstream_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(frozen.handle)),
    )
    app = create_app(
        settings.model_copy(update={"legacy_detail_api_url": "http://legacy-api:8000"})
    )
    with TestClient(app) as client:
        source = upload_asset(client)
        job = _create_detail_job(
            client, source["id"], market_region="domestic", auto_start_after_plan=False
        )
        job_id = str(job["id"])
        with app.state.session_factory() as session:
            record = session.get(JobRecord, job_id)
            assert record is not None
            config = dict(record.configuration)
            config.pop("domestic_detail_pipeline")
            record.configuration = config
            session.commit()
        yield client, frozen, job_id


def test_old_confirmation_preserves_request_and_idempotency(settings, monkeypatch):
    with isolated(settings, monkeypatch) as (client, frozen, job_id):
        original = b'{"revision":1,"approval_text":"original approval"}'
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}/plan/confirm?original=a%2Fb&x=1&x=2",
            content=original,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "original-key",
                "Cookie": "sAccessToken=original-session",
                "Authorization": "Bearer original-session",
                "anti-csrf": "original-csrf",
                "Host": "polaris.example.test",
                "Connection": "keep-alive, x-hop-only",
                "X-Hop-Only": "omit-me",
            },
        )
        assert response.status_code == 202
        assert response.json() == {
            "runtime": "frozen",
            "original_body": original.decode(),
        }
        assert len(frozen.calls) == 1
        request = frozen.calls[0]
        assert request.method == "POST" and request.content == original
        assert request.url.host == "legacy-api"
        assert request.url.query == b"original=a%2Fb&x=1&x=2"
        for name, value in {
            "Idempotency-Key": "original-key",
            "Cookie": "sAccessToken=original-session",
            "Authorization": "Bearer original-session",
            "anti-csrf": "original-csrf",
            "Host": "polaris.example.test",
            "X-Polaris-Legacy-Detail-Hop": "1",
        }.items():
            assert request.headers[name] == value
        assert "x-hop-only" not in request.headers


def test_original_zip_is_streamed_with_original_end_to_end_headers(
    settings, monkeypatch
):
    with isolated(settings, monkeypatch) as (client, frozen, job_id):
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}/download"
        )
        assert response.status_code == 200
        assert response.content == b"PK\x03\x04frozen-result\x00\xff"
        assert response.headers["content-length"] == "19"
        assert (
            response.headers["content-disposition"]
            == 'attachment; filename="original.zip"'
        )
        assert response.headers.get_list("set-cookie") == [
            "first=1; HttpOnly",
            "second=2; HttpOnly",
        ]
        assert "x-internal" not in response.headers
        assert len(frozen.calls) == 1


@pytest.mark.parametrize(
    "change", ["native", "future", "international", "main", "wrong_workspace", "absent"]
)
def test_only_original_domestic_detail_jobs_use_frozen_api(
    settings, monkeypatch, change
):
    with isolated(settings, monkeypatch) as (client, frozen, job_id):
        with client.app.state.session_factory() as session:
            record = session.get(JobRecord, job_id)
            assert record is not None
            config = dict(record.configuration)
            if change in {"native", "future"}:
                config["domestic_detail_pipeline"] = (
                    "aurora-domestic-detail-v4.3.1"
                    if change == "native"
                    else "future-version"
                )
            elif change == "international":
                config["settings"] = {
                    **config["settings"],
                    "market_region": "international",
                }
            elif change == "main":
                record.type = "domestic_main_image"
            record.configuration = config
            session.commit()
        workspace = "another-workspace" if change == "wrong_workspace" else WORKSPACE_ID
        target = "job_absent" if change == "absent" else job_id
        response = client.get(f"/api/v1/workspaces/{workspace}/jobs/{target}")
        assert response.status_code == (
            404 if change in {"wrong_workspace", "absent"} else 200
        )
        assert frozen.calls == []


def test_unavailable_original_outcome_never_falls_back_to_new_approval(
    settings, monkeypatch
):
    with isolated(settings, monkeypatch) as (client, frozen, job_id):
        with client.app.state.session_factory() as session:
            before = session.get(JobRecord, job_id).state_version
        frozen.unavailable = True
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}/plan/confirm",
            json={"revision": 1},
            headers={"Idempotency-Key": "keep-original-key"},
        )
        assert response.status_code == 502
        error = response.json()["error"]
        assert error["code"] == "DOMESTIC_DETAIL_LEGACY_API_UNAVAILABLE"
        assert error["details"] == {
            "original_result": "unknown",
            "same_idempotency_key_required": True,
        }
        assert len(frozen.calls) == 1
        current = client.app.state.session_factory()
        with current:
            assert current.get(JobRecord, job_id).state_version == before


def test_loop_is_rejected_without_forwarding_or_mutating(settings, monkeypatch):
    with isolated(settings, monkeypatch) as (client, frozen, job_id):
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}",
            headers={"X-Polaris-Legacy-Detail-Hop": "1"},
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "DOMESTIC_DETAIL_LEGACY_API_LOOP"
        assert frozen.calls == []


@pytest.mark.parametrize(
    "origin",
    [
        "https://outside.example",
        "http://legacy-api:8000/extra",
        "http://127.0.0.1:8000",
    ],
)
def test_arbitrary_proxy_origins_are_not_configurable(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, legacy_detail_api_url=origin)
