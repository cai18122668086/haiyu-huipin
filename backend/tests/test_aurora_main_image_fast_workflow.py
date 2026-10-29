"""Synthetic website-route acceptance for the independent domestic fast suite.

Images are synthetic; these tests assert delivery/recovery, never visual quality
or production latency.
"""

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from app.aurora_main_image_fast import PROFILE, SKILL_ID, VERSION
from app.aurora_main_image_fast_provider import RECEIPTS_KEY
from app.aurora_main_image_planner import PAGE_ROLES
from app.models import CreditReservationRecord, JobRecord, WorkspaceCreditAccountRecord
from app.provider import ProviderOutcome
from app.schemas_v1 import DomesticMainImageConfiguration
from app.services import canonical_hash
from app.task_runner import MockTaskRunner
from app.xinghe_agent import FakeXingheGateway

from .test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset

BASE = f"/api/v1/workspaces/{WORKSPACE_ID}"


def proposal():
    return {
        "product_identity_lock": (
            "Preserve the exact primary cup, silhouette, red label and lid."
        ),
        "confirmed_facts": ["杯盖防误开"],
        "style_lock": "Warm neutral photography with consistent red type hierarchy.",
        "pages": [
            {
                "page_index": index,
                "page_role": role,
                "visual_plan": (
                    f"Distinct composition for duty {role}: "
                    "show exact product and readable copy."
                ),
                "visible_copy": ["杯盖防误开"],
                "forbidden_elements": (
                    "No invented claim, offer, brand or performance evidence."
                ),
            }
            for index, role in enumerate(PAGE_ROLES)
        ],
    }


class Gateway(FakeXingheGateway):
    provider_name = "synthetic-real-route"

    def __init__(self, raw=None, *, failure_page=None):
        super().__init__(delay_seconds=0.01)
        self.raw = proposal() if raw is None else raw
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
                "main_image_speed": "fast",
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


def test_standard_configuration_omits_speed_and_preserves_legacy_hash():
    fields = {"product_name": "杯", "category": "杯壶", "selling_points": ["防误开"]}
    legacy = DomesticMainImageConfiguration(**fields).model_dump(mode="json")
    explicit = DomesticMainImageConfiguration(
        **fields, main_image_speed="standard"
    ).model_dump(mode="json")
    assert "main_image_speed" not in legacy
    assert explicit == legacy
    assert canonical_hash(explicit) == canonical_hash(legacy)
    assert (
        DomesticMainImageConfiguration(**fields, main_image_speed="fast").model_dump()[
            "main_image_speed"
        ]
        == "fast"
    )


@pytest.mark.parametrize("auto", [True, False])
def test_fast_website_once_planned_six_images_and_signed_downloads(
    client, tmp_path, auto
):
    job_id, gateway = create(client, tmp_path, auto=auto)
    dispatch_pending(client)
    if not auto:
        assert detail(client, job_id)["status"] == "awaiting_confirmation"
        assert not gateway.image_calls
        confirm(client, job_id)
        dispatch_pending(client)
    current = assert_delivery(client, job_id)
    assert len(gateway.planner_calls) == 1
    assert len(gateway.image_calls) == 6
    assert current["cost"]["actual_credits"] == 90
    assert current["api_request_count"] == 7
    with client.app.state.session_factory() as db:
        reservation = db.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == job_id
            )
        )
        assert reservation.status == "settled" and reservation.settled_credits == 90
        assert db.get(WorkspaceCreditAccountRecord, WORKSPACE_ID).reserved_credits == 0
    assert current["plan"]["payload"]["version"] == VERSION
    assert current["plan"]["payload"]["skill_id"] == SKILL_ID
    assert current["plan"]["payload"]["validation"]["visual_review"] == "NOT_REVIEWED"
    assert all(
        call.quality == "high"
        and call.single_dispatch
        and call.preserve_provider_artifact
        for call in gateway.image_calls
    )
    assert all(call.size == "1024x1024" for call in gateway.image_calls)
    asyncio.run(client.app.state.task_runner.run(job_id))
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 6


def test_fast_duplicate_workers_share_six_active_claims(client, tmp_path):
    job_id, gateway = create(client, tmp_path, auto=False, concurrency=3000)
    dispatch_pending(client)
    confirm(client, job_id)
    first = client.app.state.task_runner.coordinator
    from app.execution import build_fake_coordinator

    second = build_fake_coordinator(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        xinghe_concurrency=3000,
        domestic_main_image_provider_enabled=True,
    )
    assert first.prepare(job_id) == "processing"
    for index in range(6):
        assert (first if index % 2 else second).claim_next_item(job_id) is not None
    assert first.claim_next_item(job_id) is None
    assert second.claim_next_item(job_id) is None


def test_fast_rolling_refills_before_slow_first_page_finishes(client, tmp_path):
    class Rolling(Gateway):
        async def edit(self, request):
            if request.page_number == 1:
                await asyncio.wait_for(self.third_started.wait(), timeout=3)
            if request.page_number == 3:
                self.third_started.set()
            return await super().edit(request)

    gateway = Rolling()
    job_id, _ = create(client, tmp_path, auto=False, gateway=gateway, concurrency=2)
    dispatch_pending(client)
    confirm(client, job_id)

    async def run():
        gateway.third_started = asyncio.Event()
        return await client.app.state.task_runner.run(job_id)

    assert asyncio.run(run()) == "completed"
    assert_delivery(client, job_id)
    assert gateway.finished.index(2) < gateway.finished.index(1)
    assert [call.page_number for call in gateway.image_calls].index(3) < [
        call.page_number for call in gateway.image_calls
    ].index(1)
    assert gateway.max_active_images <= 2


def test_fast_partial_retry_only_calls_failed_page_and_keeps_frozen_plan(
    client, tmp_path
):
    job_id, gateway = create(client, tmp_path, gateway=Gateway(failure_page=3))
    dispatch_pending(client)
    initial = detail(client, job_id)
    assert initial["status"] == "partial", initial
    assert initial["succeeded_item_count"] == 5
    assert len(gateway.image_calls) == 6
    child_id = retry(client, job_id)
    dispatch_pending(client)
    current = assert_delivery(client, child_id)
    assert len(gateway.planner_calls) == 1
    assert len(gateway.image_calls) == 7
    assert initial["cost"]["actual_credits"] == 75
    assert current["cost"]["actual_credits"] == 15
    assert gateway.image_calls[-1].page_number == 3
    assert current["configuration"]["settings"]["main_image_speed"] == "fast"
    assert current["plan"]["payload"]["pages"] == initial["plan"]["payload"]["pages"]
    assert sum(item["status"] == "reused" for item in current["items"]) == 5


def test_fast_delivery_storage_retry_reuses_saved_original_without_calls(
    client, tmp_path, monkeypatch
):
    job_id, gateway = create(client, tmp_path)
    storage = client.app.state.storage
    original = storage.save_validated
    failed = False

    async def save(image):
        nonlocal failed
        if "/generated/" in image.object_key and not failed:
            failed = True
            raise OSError("synthetic final publication failure")
        return await original(image)

    monkeypatch.setattr(storage, "save_validated", save)
    dispatch_pending(client)
    initial = detail(client, job_id)
    assert initial["status"] == "partial", initial
    assert initial["succeeded_item_count"] == 5
    with client.app.state.session_factory() as db:
        receipts = db.get(JobRecord, job_id).configuration[RECEIPTS_KEY]
        assert len(receipts) == 6
        assert all(value["state"] == "returned" for value in receipts.values())
    child_id = retry(client, job_id)
    dispatch_pending(client)
    assert_delivery(client, child_id)
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 6


@pytest.mark.parametrize("invalid", ["short", "duplicate", "wrong_role", "extra"])
def test_fast_invalid_schema_cannot_submit_paid_images(client, tmp_path, invalid):
    raw = proposal()
    if invalid == "short":
        raw["pages"].pop()
    elif invalid == "duplicate":
        raw["pages"][1]["page_index"] = 0
    elif invalid == "wrong_role":
        raw["pages"][0]["page_role"] = PAGE_ROLES[1]
    else:
        raw["extra"] = "forbidden field"
    job_id, gateway = create(client, tmp_path, gateway=Gateway(raw))
    dispatch_pending(client)
    current = detail(client, job_id)
    assert current["status"] != "completed"
    assert current.get("plan") is None
    assert len(gateway.planner_calls) == 1
    assert not gateway.image_calls


def test_cancelled_fast_job_never_plans_or_generates(client, tmp_path):
    job_id, gateway = create(client, tmp_path)
    response = client.post(BASE + f"/jobs/{job_id}/cancel", headers=idem(), json={})
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    assert detail(client, job_id)["status"] == "cancelled"
    assert not gateway.planner_calls and not gateway.image_calls


def test_standard_api_idempotency_matches_omitted_default_and_fast_is_distinct(client):
    asset = upload_asset(client)
    body = {
        "type": "domestic_main_image",
        "intent": "submit",
        "input": {"product_asset_ids": [asset["id"]]},
        "configuration": {
            "product_name": "杯",
            "category": "杯壶",
            "selling_points": ["防误开"],
        },
    }
    headers = idem()
    original = client.post(BASE + "/jobs", headers=headers, json=body)
    assert original.status_code == 202, original.text
    assert "main_image_speed" not in original.json()["configuration"]["settings"]
    body["configuration"]["main_image_speed"] = "standard"
    replay = client.post(BASE + "/jobs", headers=headers, json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == original.json()["id"]
    body["configuration"]["main_image_speed"] = "fast"
    different = client.post(BASE + "/jobs", headers=headers, json=body)
    assert different.status_code == 409, different.text


def test_completed_fast_clone_uses_frozen_plan_but_generates_six_new_images(
    client, tmp_path
):
    job_id, gateway = create(client, tmp_path)
    dispatch_pending(client)
    initial = assert_delivery(client, job_id)
    with client.app.state.session_factory() as db:
        parent_receipts = deepcopy(
            db.get(JobRecord, job_id).configuration[RECEIPTS_KEY]
        )
    cloned = client.post(BASE + f"/jobs/{job_id}/clone", headers=idem())
    assert cloned.status_code == 201, cloned.text
    clone_id = cloned.json()["id"]
    with client.app.state.session_factory() as db:
        assert RECEIPTS_KEY not in db.get(JobRecord, clone_id).configuration
    dispatch_pending(client)
    current = assert_delivery(client, clone_id)
    assert len(gateway.planner_calls) == 1
    assert len(gateway.image_calls) == 12
    assert all(call.job_id == clone_id for call in gateway.image_calls[6:])
    assert current["plan"]["payload"]["pages"] == initial["plan"]["payload"]["pages"]
    assert current["api_request_count"] == 6
    assert current["cost"]["actual_credits"] == 90
    with client.app.state.session_factory() as db:
        child_receipts = db.get(JobRecord, clone_id).configuration[RECEIPTS_KEY]
        assert len(child_receipts) == 6
        assert all(receipt["job_id"] == clone_id for receipt in child_receipts.values())
        assert db.get(JobRecord, job_id).configuration[RECEIPTS_KEY] == parent_receipts


@pytest.mark.parametrize("plan_state", ["missing", "unconfirmed"])
def test_fast_failed_plan_cannot_retry_into_paid_generation(
    client, tmp_path, plan_state
):
    raw = proposal()
    if plan_state == "missing":
        raw["pages"].pop()
    job_id, gateway = create(
        client, tmp_path, gateway=Gateway(raw), auto=plan_state == "missing"
    )
    dispatch_pending(client)
    if plan_state == "unconfirmed":
        assert detail(client, job_id)["plan"]["confirmed_at"] is None
        cancelled = client.post(
            BASE + f"/jobs/{job_id}/cancel", headers=idem(), json={}
        )
        assert cancelled.status_code == 202, cancelled.text
        dispatch_pending(client)
        assert detail(client, job_id)["status"] == "cancelled"
    else:
        assert detail(client, job_id)["status"] == "failed"
    response = client.post(
        BASE + f"/jobs/{job_id}/retry",
        headers=idem(),
        json={"scope": "failed", "item_ids": []},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "FAST_MAIN_IMAGE_PLAN_UNAVAILABLE"
    assert len(gateway.planner_calls) == 1 and not gateway.image_calls


def test_fast_unknown_submission_cannot_retry_or_clone_paid_call(client, tmp_path):
    class UnknownGateway(Gateway):
        unknown_calls = 0

        async def edit(self, request):
            if request.page_number == 3:
                self.unknown_calls += 1
                raise TimeoutError("synthetic unknown paid submission")
            return await super().edit(request)

    gateway = UnknownGateway()
    job_id, _ = create(client, tmp_path, gateway=gateway)
    dispatch_pending(client)
    current = detail(client, job_id)
    assert current["status"] == "partial", current
    assert gateway.unknown_calls == 1 and len(gateway.image_calls) == 5
    for suffix, body in [
        ("retry", {"scope": "failed", "item_ids": []}),
        ("clone", None),
    ]:
        response = client.post(
            BASE + f"/jobs/{job_id}/{suffix}", headers=idem(), json=body
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROVIDER_OUTCOME_UNKNOWN"
    assert gateway.unknown_calls == 1 and len(gateway.image_calls) == 5
    assert len(gateway.planner_calls) == 1
