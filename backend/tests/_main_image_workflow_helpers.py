"""Synthetic website-route acceptance for the independent domestic fast suite.

Images are synthetic; these tests assert delivery/recovery, never visual quality
or production latency.
"""

import hashlib
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

from app.aurora_main_image_native_fast import PROFILE
from app.provider import ProviderOutcome
from app.task_runner import MockTaskRunner
from app.xinghe_agent import FakeXingheGateway

from .test_api import WORKSPACE_ID, idem, upload_asset

BASE = f"/api/v1/workspaces/{WORKSPACE_ID}"


class Gateway(FakeXingheGateway):
    provider_name = "synthetic-real-route"

    def __init__(self, raw=None, *, failure_page=None):
        super().__init__(delay_seconds=0.01)
        self.raw = raw
        self.failure_page = failure_page
        self.finished = []

    async def plan(self, request):
        assert request.planning_profile == PROFILE, (
            "Fast mode called an original audit/planner"
        )
        self.planner_calls.append(request)
        return SimpleNamespace(
            payload=deepcopy(self.raw),
            raw_usage={"input_tokens": 100, "output_tokens": 100},
            normalized_usage={
                "reported": True,
                "input_tokens": 100,
                "output_tokens": 100,
            },
            actual_cost_micros=0,
            provider_request_id="synthetic-plan",
            api_latency_seconds=0.01,
            error_code=None,
            error_message=None,
        )

    async def edit(self, request):
        result = await super().edit(request)
        self.finished.append(request.page_number)
        if request.page_number == self.failure_page:
            self.failure_page = None
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                artifact=None,
                error_code="KNOWN_REJECTION",
                retryable=False,
            )
        return result


def create(client, tmp_path, *, auto=True, gateway=None, concurrency=6):
    gateway = gateway or Gateway()
    state = client.app.state
    state.settings.task_runner = "tengli"
    state.settings.domestic_main_image_provider_enabled = True
    state.task_runner = MockTaskRunner(
        state.session_factory,
        0,
        storage=state.storage,
        xinghe_gateway=gateway,
        xinghe_concurrency=concurrency,
        domestic_main_image_provider_enabled=True,
        aurora_work_root=tmp_path / "fast-main",
    )
    asset = upload_asset(client)
    response = client.post(
        BASE + "/jobs",
        headers=idem(),
        json={
            "type": "domestic_main_image",
            "intent": "submit",
            "input": {"product_asset_ids": [asset["id"]]},
            "configuration": {
                "main_image_speed": "native_fast",
                "product_name": "轻量随行杯",
                "category": "保温杯",
                "selling_points": ["杯盖防误开"],
                "auto_start_after_plan": auto,
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"], gateway


def detail(client, job_id):
    response = client.get(BASE + f"/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, job_id):
    response = client.post(
        BASE + f"/jobs/{job_id}/plan/confirm",
        headers=idem(),
        json={"expected_revision": 1, "approval_text": "确认六图方案"},
    )
    assert response.status_code == 202, response.text


def retry(client, job_id):
    response = client.post(
        BASE + f"/jobs/{job_id}/retry",
        headers=idem(),
        json={"scope": "failed", "item_ids": []},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def assert_delivery(client, job_id):
    current = detail(client, job_id)
    assert current["status"] == "completed", current
    outputs = [
        a
        for a in current["assets"]
        if a["direction"] == "output" and a["is_deliverable"]
    ]
    assert sorted(a["ordinal"] for a in outputs) == list(range(6))
    for asset in outputs:
        access = client.post(
            BASE + f"/assets/{asset['asset_id']}/access-url",
            json={"disposition": "attachment", "variant": "original"},
        )
        assert access.status_code == 200, access.text
        downloaded = client.get(access.json()["url"])
        assert downloaded.status_code == 200
        assert (
            hashlib.sha256(downloaded.content).hexdigest()
            == asset["validation"]["sha256"]
        )
    archive = client.get(BASE + f"/jobs/{job_id}/deliverables.zip")
    assert archive.status_code == 200, archive.text
    with ZipFile(BytesIO(archive.content)) as zipped:
        image_names = [
            name
            for name in zipped.namelist()
            if name.endswith((".jpg", ".png", ".webp"))
        ]
        assert len(image_names) == 6, zipped.namelist()
    return current
