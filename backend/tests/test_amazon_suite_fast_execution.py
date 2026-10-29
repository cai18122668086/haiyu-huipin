"""Website-route coverage for the independent Amazon fast 21-image runtime."""

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

from app.amazon_suite_fast import native_canvas_valid
from app.amazon_suite_fast_provider import AmazonFastProvider
from app.amazon_visual_suite_provider import (
    AMAZON_ITEM_SPECS,
    AMAZON_PLAN_CONFIGURATION_KEY,
)
from app.errors import ApiError
from app.execution import ExecutionCoordinator
from app.models import (
    CreditReservationRecord,
    JobItemRecord,
    WorkspaceCreditAccountRecord,
)
from app.schemas_v1 import AmazonVisualSuiteConfiguration
from tests.test_api import WORKSPACE_ID, idem, upload_asset
from tests.test_p0_amazon_fail_closed import _CountingGateway, _install_counting_runner

BASE = f"/api/v1/workspaces/{WORKSPACE_ID}"


class Gateway(_CountingGateway):
    def __init__(self):
        super().__init__(delay_seconds=0.01)


def install(client, gateway_type=Gateway, concurrency=20):
    gateway = _install_counting_runner(client, gateway_type=gateway_type)
    client.app.state.task_runner.coordinator.max_concurrency = concurrency
    return gateway


def create(client, *, speed="fast", headers=None, auto_start=True):
    asset = upload_asset(client)
    configuration = {
        "product_name": "Visible red product",
        "marketplace": "amazon-us",
        "product_facts": ["Visible red product"],
        "auto_start_after_plan": auto_start,
        **({"amazon_speed": speed} if speed is not None else {}),
    }
    payload = {
        "type": "amazon_visual_suite",
        "intent": "submit",
        "display_name": "Amazon fast contract verification",
        "input": {"white_background_product_asset_ids": [asset["id"]]},
        "configuration": configuration,
    }
    response = client.post(BASE + "/jobs", headers=headers or idem(), json=payload)
    assert response.status_code == 202, response.text
    return response.json(), payload


def run(client, job_id):
    status = asyncio.run(client.app.state.task_runner.run(job_id))
    if status != "completed":
        result = detail(client, job_id)
        print(
            "FAST ERRORS",
            [
                (
                    item.get("item_key"),
                    item.get("error_code"),
                    item.get("error_message"),
                )
                for item in result.get("items", [])
                if item.get("error_code")
            ],
        )
    return status


def detail(client, job_id):
    return client.get(f"{BASE}/jobs/{job_id}").json()


def retry(client, job_id):
    response = client.post(
        f"{BASE}/jobs/{job_id}/retry", headers=idem(), json={"scope": "failed"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_fast_full_21_real_router_original_downloads_and_single_credit_settlement(
    client,
):
    gateway = install(client)
    job, _ = create(client)
    assert run(client, job["id"]) == "completed", detail(client, job["id"])
    result = detail(client, job["id"])
    assert result["succeeded_item_count"] == 21
    assert result["output_asset_count"] == 21
    assert result["plan"]["payload"]["business_quality"]["status"] == "NOT_REVIEWED"
    assert (
        result["plan"]["payload"]["business_quality"]["automatic_redraw_enabled"]
        is False
    )
    assert len(gateway.planner_calls) == 1
    assert gateway.planner_calls[0].planning_profile == "amazon_visual_suite_fast"
    assert len(gateway.image_calls) == 21
    assert not gateway.amazon_review_calls
    assert gateway.max_active_images == 20
    assert all(
        call.quality == "high"
        and call.preserve_provider_artifact
        and call.single_dispatch
        for call in gateway.image_calls
    )
    assert len({call.group_key for call in gateway.image_calls}) == 21
    outputs = [
        asset
        for asset in result["assets"]
        if asset["direction"] == "output" and asset["is_deliverable"]
    ]
    hashes = []
    for link in outputs:
        asset_id = link["asset_id"]
        access = client.post(
            f"{BASE}/assets/{asset_id}/access-url",
            json={"disposition": "attachment", "variant": "original"},
        )
        assert access.status_code == 200, access.text
        downloaded = client.get(access.json()["url"])
        assert downloaded.status_code == 200
        hashes.append(hashlib.sha256(downloaded.content).hexdigest())
    archive = client.get(f"{BASE}/jobs/{job['id']}/deliverables.zip")
    assert archive.status_code == 200, archive.text
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert bundle.testzip() is None
        zipped = [
            hashlib.sha256(bundle.read(name)).hexdigest()
            for name in bundle.namelist()
            if not name.endswith("/")
        ]
    assert len(zipped) == 21 and sorted(zipped) == sorted(hashes)
    with client.app.state.session_factory() as session:
        reservation = session.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == job["id"]
            )
        )
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
        assert reservation.reserved_credits == reservation.settled_credits == 315
        assert reservation.status == "settled"
        assert account.reserved_credits == 0
        balance = account.balance_credits
    assert run(client, job["id"]) == "completed"
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 21
    with client.app.state.session_factory() as session:
        assert (
            session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID).balance_credits
            == balance
        )


def test_fast_refills_the_21st_canvas_while_first_is_still_running(client):
    class RollingGateway(Gateway):
        async def edit(self, request):
            if request.page_number == 1:
                await asyncio.wait_for(self.last_started.wait(), timeout=10)
            if request.page_number == 21:
                self.last_started.set()
            return await super().edit(request)

    gateway = install(client, RollingGateway)
    job, _ = create(client)

    async def execute():
        gateway.last_started = asyncio.Event()
        return await client.app.state.task_runner.run(job["id"])

    assert asyncio.run(execute()) == "completed", detail(client, job["id"])
    order = [request.page_number for request in gateway.image_calls]
    assert order.index(21) < order.index(1)
    assert len(order) == 21 and not gateway.amazon_review_calls


def test_duplicate_workers_share_single_suite_twenty_claim_limit(client):
    install(client)
    job, _ = create(client)
    coordinator = client.app.state.task_runner.coordinator
    assert coordinator.prepare(job["id"]) == "planning"
    assert asyncio.run(coordinator.detail_planner.plan(job["id"])) == "processing"
    first = ExecutionCoordinator(
        coordinator.session_factory, coordinator.provider, max_concurrency=3000
    )
    second = ExecutionCoordinator(
        coordinator.session_factory, coordinator.provider, max_concurrency=3000
    )
    for index in range(20):
        assert (first if index % 2 else second).claim_next_item(job["id"]) is not None
    assert first.claim_next_item(job["id"]) is None
    assert second.claim_next_item(job["id"]) is None
    with client.app.state.session_factory() as session:
        running = session.scalars(
            select(JobItemRecord).where(
                JobItemRecord.job_id == job["id"], JobItemRecord.status == "running"
            )
        ).all()
        assert len(running) == 20


def test_fast_known_failure_waits_for_explicit_retry_and_reuses_successes_and_plan(
    client,
):
    class OneFailure(Gateway):
        def __init__(self):
            super().__init__()
            self.image_failures = {2: 1}

    gateway = install(client, OneFailure)
    job, _ = create(client)
    assert run(client, job["id"]) == "partial", detail(client, job["id"])
    assert len(gateway.image_calls) == 21
    assert not gateway.amazon_review_calls
    assert [r.page_number for r in gateway.image_calls].count(2) == 1
    replacement = retry(client, job["id"])
    assert run(client, replacement) == "completed", detail(client, replacement)
    assert len(gateway.planner_calls) == 1
    assert len(gateway.image_calls) == 22
    assert [r.page_number for r in gateway.image_calls].count(2) == 2
    assert not gateway.amazon_review_calls


def test_unknown_fast_submission_cannot_charge_a_duplicate_image(client):
    class UnknownGateway(Gateway):
        async def edit(self, request):
            if request.page_number == 2:
                self.image_calls.append(request)
                raise TimeoutError("synthetic unknown upstream submission")
            return await super().edit(request)

    gateway = install(client, UnknownGateway)
    job, _ = create(client)
    assert run(client, job["id"]) == "partial", detail(client, job["id"])
    assert len(gateway.image_calls) == 21
    response = client.post(
        f"{BASE}/jobs/{job['id']}/retry", headers=idem(), json={"scope": "failed"}
    )
    if response.status_code == 201:
        replacement = response.json()["id"]
        assert run(client, replacement) != "completed"
    else:
        assert response.status_code == 409, response.text
    assert len(gateway.image_calls) == 21
    assert len(gateway.planner_calls) == 1


def test_fast_final_storage_failure_recovers_provider_original_with_zero_new_calls(
    client, monkeypatch
):
    gateway = install(client)
    job, _ = create(client)
    storage = client.app.state.storage
    original_save = storage.save_validated

    async def fail_delivery(image):
        if "/generated/" in image.object_key:
            raise OSError("synthetic final publication outage")
        return await original_save(image)

    monkeypatch.setattr(storage, "save_validated", fail_delivery)
    assert run(client, job["id"]) == "failed", detail(client, job["id"])
    assert len(gateway.image_calls) == 21
    monkeypatch.setattr(storage, "save_validated", original_save)
    replacement = retry(client, job["id"])
    assert run(client, replacement) == "completed", detail(client, replacement)
    assert len(gateway.image_calls) == 21 and len(gateway.planner_calls) == 1
    assert client.get(f"{BASE}/jobs/{replacement}/deliverables.zip").status_code == 200


def test_standard_default_serialization_and_api_idempotency_remain_unchanged(client):
    assert "amazon_speed" not in AmazonVisualSuiteConfiguration().model_dump(
        mode="json"
    )
    assert AmazonVisualSuiteConfiguration().model_dump(
        mode="json"
    ) == AmazonVisualSuiteConfiguration(amazon_speed="standard").model_dump(mode="json")
    assert (
        AmazonVisualSuiteConfiguration(amazon_speed="fast").model_dump(mode="json")[
            "amazon_speed"
        ]
        == "fast"
    )
    install(client)
    headers = idem()
    job, payload = create(client, speed=None, headers=headers)
    assert "amazon_speed" not in job["configuration"]["settings"]
    payload["configuration"]["amazon_speed"] = "standard"
    replay = client.post(BASE + "/jobs", headers=headers, json=payload)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == job["id"]


@pytest.mark.parametrize("scope", ["full", "selected"])
def test_full_or_selected_retry_bills_only_failed_canvas_and_preserves_successes(
    client, scope
):
    class OneFailure(Gateway):
        def __init__(self):
            super().__init__()
            self.image_failures = {2: 1}

    gateway = install(client, OneFailure)
    job, _ = create(client)
    assert run(client, job["id"]) == "partial"
    initial = detail(client, job["id"])
    selected = [item["id"] for item in initial["items"][:2]]
    response = client.post(
        f"{BASE}/jobs/{job['id']}/retry",
        headers=idem(),
        json={
            "scope": scope,
            **({"item_ids": selected} if scope == "selected" else {}),
        },
    )
    assert response.status_code == 201, response.text
    replacement = response.json()["id"]
    assert run(client, replacement) == "completed", detail(client, replacement)
    assert len(gateway.image_calls) == 22 and len(gateway.planner_calls) == 1
    assert [request.page_number for request in gateway.image_calls].count(1) == 1
    with client.app.state.session_factory() as session:
        reservations = session.scalars(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id.in_([job["id"], replacement])
            )
        ).all()
        assert sorted(reservation.settled_credits for reservation in reservations) == [
            15,
            300,
        ]
        assert (
            session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID).reserved_credits
            == 0
        )


def test_tampered_item_shared_strategy_cannot_bypass_frozen_plan(client):
    gateway = install(client)
    job, _ = create(client)
    coordinator = client.app.state.task_runner.coordinator
    assert coordinator.prepare(job["id"]) == "planning"
    assert asyncio.run(coordinator.detail_planner.plan(job["id"])) == "processing"
    with client.app.state.session_factory() as session:
        item = session.scalar(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == job["id"])
            .order_by(JobItemRecord.ordinal)
            .limit(1)
        )
        configuration = deepcopy(item.configuration)
        configuration[AMAZON_PLAN_CONFIGURATION_KEY]["campaign_style_lock"] = (
            "Unconfirmed hostile substitute style"
        )
        item.configuration = configuration
        session.commit()
    assert run(client, job["id"]) == "partial"
    assert len(gateway.image_calls) == 20
    assert all(request.page_number != 1 for request in gateway.image_calls)
    assert (
        detail(client, job["id"])["items"][0]["error_code"]
        == "AMAZON_FAST_PLAN_CHANGED"
    )


def test_editable_fast_plan_rejects_primary_image_outside_submitted_product_sources(
    client,
):
    gateway = install(client)
    job, _ = create(client, auto_start=False)
    coordinator = client.app.state.task_runner.coordinator
    assert coordinator.prepare(job["id"]) == "planning"
    assert (
        asyncio.run(coordinator.detail_planner.plan(job["id"]))
        == "awaiting_confirmation"
    )
    payload = deepcopy(detail(client, job["id"])["plan"]["payload"])
    payload["groups"][0]["analysis"]["primary_product_source_id"] = "asset_not_supplied"
    updated = client.put(
        f"{BASE}/jobs/{job['id']}/plan",
        json={"expected_revision": 1, "payload": payload},
    )
    if updated.status_code == 200:
        confirmed = client.post(
            f"{BASE}/jobs/{job['id']}/plan/confirm",
            headers=idem(),
            json={"expected_revision": 2},
        )
        assert (
            updated.json()["validation_status"] == "invalid"
            or confirmed.status_code == 409
        )
    else:
        assert updated.status_code in {409, 422}, updated.text
    assert not gateway.image_calls


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_fast_native_original_format_contract_preserves_every_byte(fmt):
    spec = AMAZON_ITEM_SPECS[0]
    stream = BytesIO()
    Image.new("RGB", (spec.width, spec.height), "red").save(stream, fmt)
    original = stream.getvalue()
    artifact = AmazonFastProvider.artifact(original, spec)
    assert artifact.payload == original
    assert artifact.sha256 == hashlib.sha256(original).hexdigest()
    assert not artifact.locally_resampled


@pytest.mark.parametrize(
    "fmt,size", [("GIF", (2000, 2000)), ("PNG", (500, 500)), ("JPEG", (2000, 1200))]
)
def test_fast_invalid_format_or_canvas_is_blocked_without_resampling(fmt, size):
    stream = BytesIO()
    Image.new("RGB", size, "red").save(stream, fmt)
    with pytest.raises(ApiError) as raised:
        AmazonFastProvider.artifact(stream.getvalue(), AMAZON_ITEM_SPECS[0])
    assert raised.value.code == "AMAZON_FAST_CANVAS_INVALID"


def test_still_invalid_cached_raw_canvas_retries_only_explicit_failed_item(client):
    class WrongSize(Gateway):
        wrong = True

        async def edit(self, request):
            if request.page_number == 2 and self.wrong:
                request = replace(request, size="600x600")
            return await super().edit(request)

    gateway = install(client, WrongSize)
    job, _ = create(client)
    assert run(client, job["id"]) == "partial"
    assert len(gateway.image_calls) == 21
    assert (
        detail(client, job["id"])["items"][1]["error_code"]
        == "AMAZON_FAST_CANVAS_INVALID"
    )
    gateway.wrong = False
    replacement = retry(client, job["id"])
    assert run(client, replacement) == "completed", detail(client, replacement)
    assert len(gateway.image_calls) == 22 and len(gateway.planner_calls) == 1


def test_cached_original_blocked_by_legacy_gate_recovers_without_any_paid_call(
    client, monkeypatch
):
    gateway = install(client)
    job, _ = create(client)
    current_artifact = AmazonFastProvider.artifact

    def old_gate(data, spec):
        if spec.item_key == "MAIN-01":
            raise ApiError(502, "AMAZON_FAST_CANVAS_INVALID", "synthetic obsolete gate")
        return current_artifact(data, spec)

    monkeypatch.setattr(AmazonFastProvider, "artifact", staticmethod(old_gate))
    assert run(client, job["id"]) == "partial"
    assert len(gateway.image_calls) == 21
    monkeypatch.setattr(AmazonFastProvider, "artifact", staticmethod(current_artifact))
    replacement = retry(client, job["id"])
    assert run(client, replacement) == "completed", detail(client, replacement)
    assert len(gateway.image_calls) == 21 and len(gateway.planner_calls) == 1


@pytest.mark.parametrize(
    "index,size,valid",
    [
        (0, (1254, 1254), True),
        (0, (1024, 1024), True),
        (0, (1000, 1000), False),
        (0, (0, 1024), False),
        (0, (12000, 12000), False),
        (0, (1500, 1200), False),
        (7, (1464, 600), True),
        (7, (1960, 802), True),
        (7, (1463, 600), False),
        (7, (1220, 500), False),
        (7, (2000, 600), False),
        (14, (1448, 1086), True),
        (14, (1448, 1080), True),
        (14, (1448, 1000), False),
        (14, (1024, 768), False),
    ],
)
def test_native_canvas_policy_keeps_ratio_clarity_and_desktop_minimum(
    index, size, valid
):
    assert native_canvas_valid(*size, AMAZON_ITEM_SPECS[index]) is valid
