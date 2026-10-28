"""Automatic website plans and PostgreSQL JSONB order regression coverage."""

import asyncio
import hashlib
import json
from contextlib import suppress
from copy import deepcopy
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from app import refined_detail
from app.domestic_detail.delivery_authorization import receipt_authorization
from app.models import AuditEventRecord, JobPlanRecord
from app.provider import ProviderArtifact
from app.schemas_v1 import DetailPackConfiguration
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_execution import _gateway
from tests.test_domestic_detail_website_v1 import (
    explicit_synthetic_storage,  # noqa: F401
)
from tests.test_domestic_detail_website_v1 import (
    planned as quick_planned,
)
from tests.test_refined_detail import confirmation, create, planned


@pytest.fixture
def refined_png(client, monkeypatch):
    gateway = _gateway(client)
    original = gateway.edit
    client.app.state.storage.validator.max_image_pixels = 6_000_000

    async def edit(request):
        result = await original(request)
        output = BytesIO()
        Image.new("RGB", (1440, 1920), (30, 60, 90)).save(output, "PNG")
        data = output.getvalue()
        return replace(result, artifact=ProviderArtifact(
            payload=data, filename="synthetic.png", content_type="image/png",
            width=1440, height=1920, sha256=hashlib.sha256(data).hexdigest(),
        ))

    monkeypatch.setattr(gateway, "edit", edit)


def reverse_keys(value):
    if isinstance(value, dict):
        return {key: reverse_keys(value[key]) for key in reversed(value)}
    if isinstance(value, list):
        return [reverse_keys(item) for item in value]
    return value


def test_new_website_defaults_to_auto_but_explicit_manual_and_legacy_survive():
    common = dict(product_name="test", platform_code="tmall",
                  market_region="domestic", page_count=8)
    assert DetailPackConfiguration(
        **common, detail_skill="refined"
    ).auto_start_after_plan
    assert DetailPackConfiguration(
        **common, website_contract_version="WEBSITE_DIRECT_V2"
    ).auto_start_after_plan
    assert not DetailPackConfiguration(
        **common, detail_skill="refined", auto_start_after_plan=False
    ).auto_start_after_plan
    assert not DetailPackConfiguration(**common).auto_start_after_plan


def test_refined_confirmation_survives_jsonb_order_and_preserves_old_document(client):
    job = planned(client, count=1)
    payload = deepcopy(job["plan"]["payload"])
    approval = payload["refined_approval"]
    # Simulate the previous renderer's insertion-dependent document order.
    paragraphs = approval["document_markdown"].split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        label, sep, raw = paragraph.partition("：")
        if sep:
            with suppress(ValueError):
                paragraphs[index] = label + sep + json.dumps(
                    reverse_keys(json.loads(raw)), ensure_ascii=False
                )
    approval["document_markdown"] = "\n\n".join(paragraphs)
    approval["document_sha256"] = hashlib.sha256(
        approval["document_markdown"].encode()
    ).hexdigest()
    payload = reverse_keys(payload)
    assert refined_detail.approval_matches(payload)
    assert refined_detail.approval_hash(payload) == refined_detail.approval_hash(
        reverse_keys(payload)
    )
    tampered = deepcopy(payload)
    tampered["pages"][0]["image_prompt"] += " altered execution"
    assert not refined_detail.approval_matches(tampered)
    with client.app.state.session_factory() as db:
        record = db.get(JobPlanRecord, job["plan"]["id"])
        record.payload = payload
        db.commit()
    job["plan"]["payload"] = payload
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(), json=confirmation(job),
    )
    assert response.status_code == 202, response.text
    assert response.json()["plan"]["payload"]["refined_approval"] == approval
    assert not _gateway(client).image_calls


def test_refined_automatic_plan_generates_once_without_confirm_api(client, refined_png):
    job = create(client, count=1, auto_start=True)
    dispatch_pending(client)
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}"
    ).json()
    assert current["status"] == "completed", current
    assert current["plan"]["confirmed_at"]
    assert current["plan"]["payload"]["automatic_plan_confirmation"][
        "confirmation_source"
    ] == "task_submission_default"
    gateway = _gateway(client)
    before = len(gateway.image_calls)
    assert before == 1
    asyncio.run(client.app.state.task_runner.run(job["id"]))
    assert len(gateway.image_calls) == before
    with client.app.state.session_factory() as db:
        audits = db.scalars(select(AuditEventRecord).where(
            AuditEventRecord.action == "detail_plan.auto_confirmed",
            AuditEventRecord.resource_id == job["id"],
        )).all()
        assert len(audits) == 1


def test_quick_automatic_plan_reaches_download_without_final_confirmation(client):
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    job = quick_planned(client, profile="WEBSITE_DIRECT_V2", auto_start=True)
    assert job["status"] == "completed", job
    assert len(_gateway(client).image_calls) == 4
    authority = receipt_authorization(job["plan"]["payload"])
    assert authority["authorization_source"] == "task_submission_default"
    assert "已审阅" not in authority["authorization_text"]
    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/deliverables.zip"
    )
    assert response.status_code == 200, response.text


def test_six_automatic_detail_jobs_can_enter_generation_together(
    client, monkeypatch, refined_png
):
    jobs = [create(client, count=1, auto_start=True) for _ in range(6)]
    gateway = _gateway(client)
    original = gateway.edit
    entered = set()

    async def run_both():
        ready = asyncio.Event()

        async def edit(request):
            entered.add(request.job_id)
            if len(entered) == 6:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=15)
            return await original(request)

        monkeypatch.setattr(gateway, "edit", edit)
        return await asyncio.gather(*(
            client.app.state.task_runner.run(job["id"]) for job in jobs
        ))

    assert asyncio.run(run_both()) == ["completed"] * 6
    assert entered == {job["id"] for job in jobs}
    assert len(gateway.image_calls) == 6
