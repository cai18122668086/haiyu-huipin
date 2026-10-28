from __future__ import annotations

import json
import socket
from io import BytesIO
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

import pytest
from app.config import Settings
from app.main import create_app
from app.outbox import publish_pending_events
from app.task_runner import MockTaskRunner
from app.xinghe_agent import TengliOpenAICompatibleGateway
from fastapi.testclient import TestClient
from PIL import Image

WORKSPACE_ID = "ws_second_fix_boundary"
PAGE_COUNT = 6
BUDGET_LIMIT_MICROS = 200_000_000


def _block_network(*_: object, **__: object) -> NoReturn:
    raise AssertionError("Independent boundary verification forbids network access.")


def _is_loopback(address: object) -> bool:
    return (
        isinstance(address, tuple)
        and bool(address)
        and address[0] in {"127.0.0.1", "::1", "localhost"}
    )


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def loopback_connect(sock: socket.socket, address: Any) -> None:
        if not _is_loopback(address):
            _block_network()
        original_connect(sock, address)

    def loopback_connect_ex(sock: socket.socket, address: Any) -> int:
        if not _is_loopback(address):
            _block_network()
        return original_connect_ex(sock, address)

    def loopback_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if not _is_loopback(address):
            _block_network()
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", loopback_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", loopback_connect_ex)
    monkeypatch.setattr(socket, "create_connection", loopback_create_connection)


def _valid_payload(page_count: int = PAGE_COUNT) -> dict[str, Any]:
    return {
        "analysis": {
            "category_key": "fashion",
            "category_reason": "visible",
            "product_image_analysis": {"visible_facts": ["dark upper"]},
            "product_consistency_anchors": {"silhouette": "shoe"},
            "buyer_objections": ["appearance"],
        },
        "plan": {
            "campaign_style_lock": "consistent",
            "design_strength_lock": "varied",
            "screens": [
                {
                    "role": f"Role {page}",
                    "title": f"Title {page}",
                    "label": f"Label {page}",
                    "composition": f"Composition {page}",
                    "device": f"Device {page}",
                    "difference": f"Difference {page}",
                }
                for page in range(1, page_count + 1)
            ],
        },
    }


class _OfflineTransport:
    def __init__(self, content: str) -> None:
        self.content = content
        self.planner_calls = 0
        self.image_calls = 0

    async def post_json(self, **_: Any) -> tuple[int, dict[str, Any]]:
        self.planner_calls += 1
        return (
            200,
            {
                "id": "offline-second-fix-planner",
                "choices": [{"message": {"content": self.content}}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                },
            },
        )

    async def post_multipart(self, **_: Any) -> tuple[int, dict[str, Any]]:
        self.image_calls += 1
        raise AssertionError("Planner boundary verification must not call images.")


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 32), color=(10, 20, 30)).save(output, format="JPEG")
    return output.getvalue()


def _run_content(tmp_path: Path, content: str) -> dict[str, Any]:
    run_id = uuid4().hex
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_mode="development",
        development_user_id=f"usr_{run_id}",
        development_workspace_id=WORKSPACE_ID,
        development_workspace_name="Second Fix Boundary",
        access_url_secret="offline-boundary-placeholder",
        database_url=f"sqlite:///{tmp_path / f'{run_id}.db'}",
        auto_create_schema=True,
        storage_backend="local",
        storage_dir=tmp_path / f"storage-{run_id}",
        auto_process_jobs=False,
        task_dispatcher="inline",
        mock_job_delay_seconds=0,
        tengli_api_key="offline-placeholder",
        tengli_base_url="https://offline.invalid",
        tengli_image_model="offline-image",
        tengli_vision_model="offline-planner",
        tengli_smoke_budget_usd=200,
        xinghe_concurrency=2,
        max_upload_bytes=2 * 1024 * 1024,
        max_image_pixels=2_000_000,
    )
    transport = _OfflineTransport(content)
    with TestClient(create_app(settings)) as client:
        gateway = TengliOpenAICompatibleGateway(
            api_key="offline-placeholder",
            base_url="https://offline.invalid",
            planner_model="offline-planner",
            image_model="offline-image",
            transport=transport,
        )
        runner = MockTaskRunner(
            client.app.state.session_factory,
            0,
            storage=client.app.state.storage,
            xinghe_gateway=gateway,
            xinghe_concurrency=2,
            budget_limit_micros=BUDGET_LIMIT_MICROS,
        )
        client.app.state.task_runner = runner
        assert runner.coordinator.detail_planner is not None
        assert (
            runner.coordinator.detail_planner.budget_limit_micros
            == BUDGET_LIMIT_MICROS
        )
        upload = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
            headers={"Idempotency-Key": str(uuid4())},
            data={"origin_kind": "upload", "media_kind": "image"},
            files={"file": ("offline.jpg", _image_bytes(), "image/jpeg")},
        )
        assert upload.status_code == 201
        job = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "type": "detail_pack",
                "intent": "submit",
                "display_name": "Independent second-fix boundary",
                "input": {
                    "product_groups": [
                        {
                            "group_key": "shoe",
                            "asset_ids": [upload.json()["id"]],
                        }
                    ]
                },
                "configuration": {
                    "product_name": "Visible shoe",
                    "selling_points": ["visible facts only"],
                    "platform_code": "amazon",
                    "language": "en",
                    "page_count": PAGE_COUNT,
                },
            },
        )
        assert job.status_code == 202
        assert (
            publish_pending_events(
                settings,
                client.app.state.session_factory,
                client.app.state.task_runner,
            )
            == 1
        )
        result = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job.json()['id']}"
        ).json()
    plan = result["plan"]["payload"]
    planner_usage = plan["usage"]["planner"][0]
    return {
        "status": result["status"],
        "error_code": result.get("error_code"),
        "requested_page_count": result["configuration"]["settings"]["page_count"],
        "expected_item_count": result["expected_item_count"],
        "planner_request_count": plan.get(
            "planner_request_count", transport.planner_calls
        ),
        "image_request_count": plan.get("image_request_count", 0),
        "usage_reported": planner_usage["reported"],
        "total_tokens": planner_usage["normalized"]["total_tokens"],
        "planner_actual_cost_micros": planner_usage["actual_cost_micros"],
        "job_actual_cost": result["cost"]["actual"],
        "usage_completeness": result["cost"]["usage_completeness"],
        "api_request_count": result["api_request_count"],
        "transport_planner_calls": transport.planner_calls,
        "transport_image_calls": transport.image_calls,
        "page_count": len(plan.get("pages", [])),
        "budget_limit_micros": plan.get("cost", {}).get("budget_limit_micros"),
        "configured_budget_limit_micros": BUDGET_LIMIT_MICROS,
        "estimated_pending_micros": plan.get("cost", {}).get(
            "estimated_pending_micros"
        ),
    }


@pytest.mark.parametrize(
    "content",
    [
        f" \r\n{json.dumps(_valid_payload())}\n\t",
        f"```json\n{json.dumps(_valid_payload())}\n```",
        f"```JsOn \t\r\n{json.dumps(_valid_payload())}\r\n```\t",
        f"\n\t``` \t\n{json.dumps(_valid_payload())}\n```\n ",
    ],
    ids=[
        "whole-object-with-outer-whitespace",
        "json-fence-lf",
        "mixed-case-json-fence-crlf-horizontal-whitespace",
        "untagged-fence-lf-horizontal-and-outer-whitespace",
    ],
)
def test_independent_valid_content_stops_before_confirmation(
    tmp_path: Path,
    content: str,
) -> None:
    assert _run_content(tmp_path, content) == {
        "status": "awaiting_confirmation",
        "error_code": None,
        "requested_page_count": PAGE_COUNT,
        "expected_item_count": PAGE_COUNT,
        "planner_request_count": 1,
        "image_request_count": 0,
        "usage_reported": True,
        "total_tokens": 150,
        "planner_actual_cost_micros": None,
        "job_actual_cost": None,
        "usage_completeness": "none",
        "api_request_count": 0,
        "transport_planner_calls": 1,
        "transport_image_calls": 0,
        "page_count": PAGE_COUNT,
        "budget_limit_micros": BUDGET_LIMIT_MICROS,
        "configured_budget_limit_micros": BUDGET_LIMIT_MICROS,
        "estimated_pending_micros": 270_000,
    }


@pytest.mark.parametrize(
    "content",
    [
        "plain non-JSON text",
        "7",
        '["scalar", 7]',
        f"{json.dumps(_valid_payload())}\n{json.dumps(_valid_payload())}",
        (
            f"```json\n{json.dumps(_valid_payload())}\n```\n"
            "```\n7\n```"
        ),
        f"Planner result:\n```json\n{json.dumps(_valid_payload())}\n```",
        (
            f"```json\n{json.dumps(_valid_payload())}\n```\n"
            "End of planner result."
        ),
        (
            f"```json\n{json.dumps(_valid_payload())}\n```\n"
            f"```json\n{json.dumps(_valid_payload())}\n```"
        ),
        f"```json\n{json.dumps(_valid_payload())}",
        f"```json\n{json.dumps(_valid_payload())}\n``",
        json.dumps({"analysis": {}, "plan": {}}),
        f"```json\n{json.dumps(_valid_payload(5))}\n```",
        "```json\n7\n```",
        '```json\n[{"analysis": {}, "plan": {}}]\n```',
        f"```javascript\n{json.dumps(_valid_payload())}\n```",
        f"```json {json.dumps(_valid_payload())}\n```",
        f"{json.dumps(_valid_payload())}\n7",
        f"7\n{json.dumps(_valid_payload())}",
    ],
    ids=[
        "plain-text",
        "whole-json-scalar",
        "whole-json-array",
        "multiple-whole-objects",
        "v5-second-scalar-fence",
        "v5-nonempty-prefix",
        "v5-nonempty-suffix",
        "two-valid-object-fences",
        "unclosed-fence",
        "malformed-closing-fence",
        "missing-required-fields",
        "screens-not-six",
        "fenced-json-scalar",
        "fenced-json-array",
        "unsupported-language-tag",
        "opening-fence-with-body-on-same-line",
        "whole-object-with-scalar-suffix",
        "whole-object-with-scalar-prefix",
    ],
)
def test_independent_unsafe_content_fails_closed_with_accounting(
    tmp_path: Path,
    content: str,
) -> None:
    assert _run_content(tmp_path, content) == {
        "status": "failed",
        "error_code": "PLANNER_SCHEMA_INVALID",
        "requested_page_count": PAGE_COUNT,
        "expected_item_count": PAGE_COUNT,
        "planner_request_count": 1,
        "image_request_count": 0,
        "usage_reported": True,
        "total_tokens": 150,
        "planner_actual_cost_micros": None,
        "job_actual_cost": None,
        "usage_completeness": "complete",
        "api_request_count": 1,
        "transport_planner_calls": 1,
        "transport_image_calls": 0,
        "page_count": 0,
        "budget_limit_micros": None,
        "configured_budget_limit_micros": BUDGET_LIMIT_MICROS,
        "estimated_pending_micros": None,
    }
