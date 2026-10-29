"""Website routes and execution with synthetic upstream bytes, never paid calls."""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from zipfile import ZipFile

import pytest
from PIL import Image
from sqlalchemy import select

from app import detail_fast, refined_detail
from app.execution import ExecutionCoordinator
from app.models import (
    AssetRecord,
    CreditReservationRecord,
    JobRecord,
    WorkspaceCreditAccountRecord,
)
from app.provider import ProviderArtifact, ProviderOutcome
from app.schemas_v1 import DetailPackConfiguration
from app.services import canonical_hash
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_domestic_detail_execution import _gateway

BASE = f"/api/v1/workspaces/{WORKSPACE_ID}"


def request_payload(source_id, count=6, speed="fast"):
    configuration = {
        "detail_skill": "refined",
        "product_name": "合成测试商品",
        "selling_points": ["用户确认的合成商品信息"],
        "platform_code": "tmall",
        "market_region": "domestic",
        "page_count": count,
        "auto_start_after_plan": False,
    }
    if speed is not None:
        configuration["detail_speed"] = speed
    return {
        "type": "detail_pack",
        "intent": "submit",
        "display_name": "快速详情合成验收",
        "input": {
            "product_groups": [{"group_key": "primary", "asset_ids": [source_id]}]
        },
        "configuration": configuration,
    }


def planned(client, count=6):
    source = upload_asset(client)
    response = client.post(
        BASE + "/jobs", headers=idem(), json=request_payload(source["id"], count)
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    dispatch_pending(client)
    job = client.get(BASE + f"/jobs/{job_id}").json()
    assert job["status"] == "awaiting_confirmation", job
    return job


def confirm(client, job):
    response = client.post(
        BASE + f"/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json={
            "expected_revision": job["plan"]["revision"],
            "approval_text": "我已审阅完整方案，确认按此生成。",
            "approval_document_sha256": job["plan"]["payload"]["refined_approval"][
                "document_sha256"
            ],
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def synthetic_images(
    client,
    monkeypatch,
    gate=None,
    fail_page=None,
    size=(1440, 1920),
    image_format="PNG",
):
    gateway = _gateway(client)
    gateway.provider_name = "synthetic"
    original_edit = getattr(gateway, "_detail_fast_test_original_edit", gateway.edit)
    gateway._detail_fast_test_original_edit = original_edit
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    originals = {}

    async def edit(request):
        if gate is not None:
            await gate(request)
        result = await original_edit(request)
        if request.page_number == fail_page:
            return replace(
                result,
                outcome=ProviderOutcome.FAILED,
                artifact=None,
                error_code="KNOWN_TEST_REJECTION",
                error_message="Explicit synthetic refusal",
                retryable=False,
                http_status=400,
            )
        output = BytesIO()
        Image.new("RGB", size, (request.page_number, 50, 90)).save(output, image_format)
        payload = output.getvalue()
        originals[request.page_number] = payload
        return replace(
            result,
            artifact=ProviderArtifact(
                payload=payload,
                filename=f"page-{request.page_number:03d}."
                + ("png" if image_format == "PNG" else "jpg"),
                content_type="image/png" if image_format == "PNG" else "image/jpeg",
                width=size[0],
                height=size[1],
                sha256=hashlib.sha256(payload).hexdigest(),
            ),
        )

    monkeypatch.setattr(gateway, "edit", edit)
    return gateway, originals


@pytest.mark.parametrize("count", [1, 6, 12])
def test_fast_route_preserves_all_pages_with_one_plan_and_original_downloads(
    client, monkeypatch, count
):
    gateway, originals = synthetic_images(client, monkeypatch)
    job = planned(client, count)
    assert (
        job["configuration"]["domestic_detail_pipeline"] == detail_fast.PIPELINE_VERSION
    )
    assert len(gateway.planner_calls) == 1
    assert gateway.planner_calls[0].planning_profile == detail_fast.PROFILE
    payload = job["plan"]["payload"]
    detail_fast.validate_payload(payload, count)
    assert len(payload["pages"]) == count
    assert all("pair_id" not in page for page in payload["pages"])
    confirm(client, job)
    dispatch_pending(client)
    final = client.get(BASE + f"/jobs/{job['id']}").json()
    assert final["status"] == "completed", final
    assert len(gateway.planner_calls) == 1  # No extra audits or automatic redraws.
    assert len(gateway.image_calls) == count
    assert all(
        r.quality == "high" and r.size == "1440x1920" and r.preserve_provider_artifact
        for r in gateway.image_calls
    )
    assert {r.prompt for r in gateway.image_calls} == {
        p["image_prompt"] for p in payload["pages"]
    }
    outputs = [
        a for a in final["assets"] if a["direction"] == "output" and a["is_deliverable"]
    ]
    assert len(outputs) == count and final["output_asset_count"] == count
    for asset in outputs:
        link = client.post(
            BASE + f"/assets/{asset['asset_id']}/access-url",
            json={"variant": "original", "disposition": "attachment"},
        )
        assert link.status_code == 200, link.text
        response = client.get(link.json()["url"])
        assert response.status_code == 200
        assert response.content == originals[asset["ordinal"] + 1]
    archive = client.get(BASE + f"/jobs/{job['id']}/deliverables.zip")
    assert archive.status_code == 200, archive.text
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert len(bundle.namelist()) == count and bundle.testzip() is None
        for number in range(1, count + 1):
            assert bundle.read(f"page-{number:03d}.png") == originals[number]
    assert gateway.external_network_request_count == 0
    with client.app.state.session_factory() as db:
        reservation = db.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == job["id"]
            )
        )
        assert reservation.reserved_credits == reservation.settled_credits == 15 * count
        assert db.get(WorkspaceCreditAccountRecord, WORKSPACE_ID).reserved_credits == 0


def test_fast_canvases_start_together_without_three_second_stagger(client, monkeypatch):
    entered = set()
    ready = None

    async def gate(request):
        entered.add(request.page_number)
        if len(entered) == 6:
            ready.set()
        # A synchronization barrier verifies all requests are independently
        # dispatched. Timeout only detects deadlock, not a speed benchmark.
        await asyncio.wait_for(ready.wait(), timeout=2)

    gateway, _ = synthetic_images(client, monkeypatch, gate)
    job = planned(client)
    confirm(client, job)

    async def run():
        nonlocal ready
        ready = asyncio.Event()
        return await client.app.state.task_runner.coordinator.run(job["id"])

    assert asyncio.run(run()) == "completed"
    assert entered == set(range(1, 7)) and len(gateway.image_calls) == 6


def test_fast_two_slots_refill_while_first_canvas_is_still_running(client, monkeypatch):
    third_started = None
    finished = []

    async def gate(request):
        if request.page_number == 1:
            await asyncio.wait_for(third_started.wait(), timeout=3)
        if request.page_number == 3:
            third_started.set()
        finished.append(request.page_number)

    gateway, _ = synthetic_images(client, monkeypatch, gate)
    job = planned(client)
    confirm(client, job)
    coordinator = client.app.state.task_runner.coordinator
    coordinator.max_concurrency = 2

    async def run():
        nonlocal third_started
        third_started = asyncio.Event()
        return await coordinator.run(job["id"])

    assert asyncio.run(run()) == "completed"
    assert finished.index(3) < finished.index(1)
    assert len(gateway.image_calls) == 6


def test_duplicate_workers_share_the_smaller_job_claim_limit(client):
    job = planned(client, 12)
    confirm(client, job)
    first = client.app.state.task_runner.coordinator
    first.max_concurrency = 2
    second = ExecutionCoordinator(
        client.app.state.session_factory, first.provider, max_concurrency=2
    )
    assert first.prepare(job["id"]) == "processing"
    assert first.claim_next_item(job["id"]) is not None
    assert second.claim_next_item(job["id"]) is not None
    assert first.claim_next_item(job["id"]) is None
    assert second.claim_next_item(job["id"]) is None


def test_known_failure_retry_reuses_successful_pages_without_replanning(
    client, monkeypatch
):
    gateway, originals = synthetic_images(client, monkeypatch, fail_page=2)
    job = planned(client, 3)
    confirm(client, job)
    dispatch_pending(client)
    original = client.get(BASE + f"/jobs/{job['id']}").json()
    assert original["status"] == "partial", original
    assert len(gateway.image_calls) == 3
    saved = dict(originals)
    synthetic_images(client, monkeypatch)
    response = client.post(
        BASE + f"/jobs/{job['id']}/retry", headers=idem(), json={"scope": "failed"}
    )
    assert response.status_code == 201, response.text
    revision = response.json()
    assert (
        revision["configuration"]["domestic_detail_pipeline"]
        == detail_fast.PIPELINE_VERSION
    )
    assert sum(i["status"] == "reused" for i in revision["items"]) == 2
    if revision["status"] == "awaiting_confirmation":
        confirm(client, revision)
    dispatch_pending(client)
    final = client.get(BASE + f"/jobs/{revision['id']}").json()
    assert final["status"] == "completed", final
    assert len(gateway.planner_calls) == 1
    assert len(gateway.image_calls) == 4
    assert gateway.image_calls[-1].page_number == 2
    archive = client.get(BASE + f"/jobs/{revision['id']}/deliverables.zip")
    assert archive.status_code == 200
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert bundle.read("page-001.png") == saved[1]
        assert bundle.read("page-003.png") == saved[3]
        assert len(bundle.namelist()) == 3


def test_standard_default_preserves_request_identity_and_fast_is_explicit(client):
    source = upload_asset(client)
    request = request_payload(source["id"], 1, None)
    omitted = DetailPackConfiguration.model_validate(
        request["configuration"]
    ).model_dump(mode="json")
    explicit = DetailPackConfiguration.model_validate(
        {**request["configuration"], "detail_speed": "standard"}
    ).model_dump(mode="json")
    fast = DetailPackConfiguration.model_validate(
        {**request["configuration"], "detail_speed": "fast"}
    ).model_dump(mode="json")
    assert omitted == explicit and "detail_speed" not in omitted
    assert canonical_hash(omitted) == canonical_hash(explicit) != canonical_hash(fast)
    key = idem()
    first = client.post(BASE + "/jobs", headers=key, json=request)
    assert first.status_code == 202
    explicit_request = deepcopy(request)
    explicit_request["configuration"]["detail_speed"] = "standard"
    replay = client.post(BASE + "/jobs", headers=key, json=explicit_request)
    assert replay.status_code == 202 and replay.json()["id"] == first.json()["id"]
    assert (
        first.json()["configuration"]["domestic_detail_pipeline"]
        == refined_detail.PIPELINE_VERSION
    )
    explicit_request["configuration"]["detail_speed"] = "fast"
    conflict = client.post(BASE + "/jobs", headers=key, json=explicit_request)
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "market,skill", [("international", "refined"), ("domestic", "quick")]
)
def test_fast_cannot_silently_select_another_detail_mode(client, market, skill):
    source = upload_asset(client)
    request = request_payload(source["id"], 8)
    request["configuration"].update(market_region=market, detail_skill=skill)
    response = client.post(BASE + "/jobs", headers=idem(), json=request)
    assert response.status_code == 422


def test_fast_auto_start_uses_submission_authorization_and_needs_no_second_plan(
    client, monkeypatch
):
    gateway, _ = synthetic_images(client, monkeypatch)
    source = upload_asset(client)
    request = request_payload(source["id"], 3)
    request["configuration"]["auto_start_after_plan"] = True
    response = client.post(BASE + "/jobs", headers=idem(), json=request)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    dispatch_pending(client)
    final = client.get(BASE + f"/jobs/{job_id}").json()
    assert final["status"] == "completed", final
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 3
    receipt = final["plan"]["payload"]["automatic_plan_confirmation"]
    assert receipt["actor_user_id"] == "usr_test"
    assert (
        receipt["approval_document_sha256"]
        == final["plan"]["payload"]["refined_approval"]["document_sha256"]
    )


def test_fast_private_receipts_are_saved_but_not_exposed_by_job_routes(
    client, monkeypatch
):
    synthetic_images(client, monkeypatch)
    job = planned(client, 1)
    confirm(client, job)
    dispatch_pending(client)
    with client.app.state.session_factory() as db:
        record = db.get(JobRecord, job["id"])
        receipts = record.configuration["_detail_fast_invocations"]
        assert len(receipts) == 1
        receipt = next(iter(receipts.values()))
        assert receipt["state"] == "returned"
        private_key = receipt["artifact"]["object_key"]
        assert "/detail-fast/" in private_key
        assert receipt["binding"]["prompt"]
    detail = client.get(BASE + f"/jobs/{job['id']}")
    listing = client.get(BASE + "/jobs")
    assert detail.status_code == listing.status_code == 200
    for response in (detail, listing):
        assert "_detail_fast_invocations" not in response.text
        assert private_key not in response.text


@pytest.mark.parametrize("speed", [None, "fast"])
def test_saved_draft_cannot_change_its_detail_speed(client, speed):
    source = upload_asset(client)
    request = request_payload(source["id"], 1, speed)
    request["intent"] = "draft"
    response = client.post(BASE + "/jobs", headers=idem(), json=request)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "draft"
    changed = deepcopy(job["configuration"])
    changed["settings"]["detail_speed"] = "fast" if speed is None else "standard"
    response = client.patch(
        BASE + f"/jobs/{job['id']}", json={"configuration": changed}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DETAIL_SKILL_IMMUTABLE"
    current = client.get(BASE + f"/jobs/{job['id']}").json()
    assert current["configuration"] == job["configuration"]


def test_completed_selected_revision_requires_new_approval_and_only_redraws_selection(
    client, monkeypatch
):
    gateway, originals = synthetic_images(client, monkeypatch)
    job = planned(client, 3)
    confirm(client, job)
    dispatch_pending(client)
    original = client.get(BASE + f"/jobs/{job['id']}").json()
    assert original["status"] == "completed"
    saved = dict(originals)
    response = client.post(
        BASE + f"/jobs/{job['id']}/retry",
        headers=idem(),
        json={"scope": "selected", "item_ids": [original["items"][0]["id"]]},
    )
    assert response.status_code == 201, response.text
    revision = response.json()
    assert revision["status"] == "awaiting_confirmation"
    assert revision["plan"]["confirmed_at"] is None
    assert sum(i["status"] == "reused" for i in revision["items"]) == 2
    dispatch_pending(client)
    assert len(gateway.image_calls) == 3  # Selection alone is not approval.
    confirm(client, revision)
    dispatch_pending(client)
    final = client.get(BASE + f"/jobs/{revision['id']}").json()
    assert final["status"] == "completed", final
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 4
    assert gateway.image_calls[-1].page_number == 1
    assert gateway.image_calls[-1].job_id == revision["id"]
    archive = client.get(BASE + f"/jobs/{revision['id']}/deliverables.zip")
    assert archive.status_code == 200
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert len(bundle.namelist()) == 3
        assert bundle.read("page-002.png") == saved[2]
        assert bundle.read("page-003.png") == saved[3]
    with client.app.state.session_factory() as db:
        reservation = db.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == revision["id"]
            )
        )
        assert reservation.reserved_credits == reservation.settled_credits == 15


@pytest.mark.parametrize("size", [(1440, 1920), (1086, 1448)])
def test_delivery_storage_failure_reuses_private_original_without_new_generation(
    client, monkeypatch, size
):
    gateway, originals = synthetic_images(client, monkeypatch, size=size)
    job = planned(client, 2)
    confirm(client, job)
    storage = client.app.state.storage
    save = storage.save_validated
    first_item = job["items"][0]["id"]
    rejected = []

    async def fail_public_delivery(image):
        first_sha = hashlib.sha256(originals[1]).hexdigest() if 1 in originals else None
        if "/generated/" in image.object_key and image.sha256 == first_sha:
            rejected.append(image.object_key)
            raise OSError("synthetic public-delivery write failure")
        return await save(image)  # Private /detail-fast/ receipt succeeds.

    monkeypatch.setattr(storage, "save_validated", fail_public_delivery)
    dispatch_pending(client)
    original = client.get(BASE + f"/jobs/{job['id']}").json()
    assert rejected
    assert original["status"] == "partial", original
    assert len(gateway.image_calls) == 2
    with client.app.state.session_factory() as db:
        receipts = db.get(JobRecord, job["id"]).configuration[
            "_detail_fast_invocations"
        ]
        assert receipts[first_item]["state"] == "returned"
    monkeypatch.setattr(storage, "save_validated", save)
    response = client.post(
        BASE + f"/jobs/{job['id']}/retry", headers=idem(), json={"scope": "failed"}
    )
    assert response.status_code == 201, response.text
    revision = response.json()
    if revision["status"] == "awaiting_confirmation":
        confirm(client, revision)
    dispatch_pending(client)
    final = client.get(BASE + f"/jobs/{revision['id']}").json()
    assert final["status"] == "completed", final
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 2
    archive = client.get(BASE + f"/jobs/{revision['id']}/deliverables.zip")
    assert archive.status_code == 200
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert len(bundle.namelist()) == 2
        for page in (1, 2):
            assert bundle.read(f"page-{page:03d}.png") == originals[page]


@pytest.mark.parametrize("size", [(1440, 1920), (1086, 1448)])
def test_fast_native_canvas_keeps_actual_size_receipt_and_original_download_bytes(
    client, monkeypatch, size
):
    gateway, originals = synthetic_images(client, monkeypatch, size=size)
    job = planned(client, 2)
    confirm(client, job)
    dispatch_pending(client)
    final = client.get(BASE + f"/jobs/{job['id']}").json()
    assert final["status"] == "completed", final
    outputs = [
        a for a in final["assets"] if a["direction"] == "output" and a["is_deliverable"]
    ]
    assert len(outputs) == 2
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 2
    for output in outputs:
        validation = output["validation"]
        assert (validation["actual_width"], validation["actual_height"]) == size
        assert validation["requested_size"] == "1440x1920"
        assert validation["locally_resampled"] is False
        info = client.get(BASE + f"/assets/{output['asset_id']}").json()
        assert (info["width"], info["height"]) == size
        link = client.post(
            BASE + f"/assets/{output['asset_id']}/access-url",
            json={"variant": "original", "disposition": "attachment"},
        )
        assert link.status_code == 200
        downloaded = client.get(link.json()["url"])
        assert downloaded.status_code == 200
        assert downloaded.content == originals[output["ordinal"] + 1]
        if size != (1440, 1920):
            with client.app.state.session_factory() as db:
                asset = db.get(AssetRecord, output["asset_id"])
                receipt = asset.asset_metadata["provider_metrics"]["delivery_canvas"]
                assert receipt["actual_size"] == list(size)
                assert receipt["requested_size"] == [1440, 1920]
                assert receipt["sha256"] == asset.sha256
                assert receipt["locally_resampled"] is False
                assert receipt["policy"] == refined_detail.NATIVE_DELIVERY_POLICY
    archive = client.get(BASE + f"/jobs/{job['id']}/deliverables.zip")
    assert archive.status_code == 200
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert len(bundle.namelist()) == 2
        for page in (1, 2):
            assert bundle.read(f"page-{page:03d}.png") == originals[page]


@pytest.mark.parametrize("size", [(768, 1024), (1254, 1254)])
def test_fast_native_policy_rejects_preview_pixels_and_wrong_aspect(
    client, monkeypatch, size
):
    gateway, _ = synthetic_images(client, monkeypatch, size=size)
    job = planned(client, 1)
    confirm(client, job)
    dispatch_pending(client)
    final = client.get(BASE + f"/jobs/{job['id']}").json()
    assert final["status"] == "failed", final
    assert final["items"][0]["error_code"] == "DETAIL_OUTPUT_DIMENSIONS_INVALID"
    assert final["output_asset_count"] == 0
    assert len(gateway.planner_calls) == len(gateway.image_calls) == 1
    with client.app.state.session_factory() as db:
        receipts = db.get(JobRecord, job["id"]).configuration[
            "_detail_fast_invocations"
        ]
        assert next(iter(receipts.values()))["state"] == "returned"


def test_fast_keeps_original_png_format_contract_and_rejects_jpeg(client, monkeypatch):
    gateway, _ = synthetic_images(client, monkeypatch, image_format="JPEG")
    job = planned(client, 1)
    confirm(client, job)
    dispatch_pending(client)
    final = client.get(BASE + f"/jobs/{job['id']}").json()
    assert final["status"] == "failed", final
    assert final["items"][0]["error_code"] == "DETAIL_OUTPUT_FORMAT_INVALID"
    assert final["output_asset_count"] == 0
    assert len(gateway.image_calls) == 1
