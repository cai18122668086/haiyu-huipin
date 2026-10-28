from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import services
from app.credit_billing import PRICING_KEY, image_unit_credits
from app.domain import JobType
from app.models import (
    AssetRecord,
    CreditLedgerEntryRecord,
    CreditReservationRecord,
    JobAssetRecord,
    JobItemRecord,
    JobRecord,
    WorkspaceCreditAccountRecord,
)
from tests.test_api import (
    create_local_edit_job,
    dispatch_pending,
    local_edit_payload,
    upload_asset,
)

WORKSPACE = "ws_test"


def seed_job(
    session,
    *,
    kind="detail_pack",
    count=8,
    reused=0,
    configuration=None,
    deliverables=True,
):
    job = JobRecord(
        id="job_" + uuid4().hex,
        workspace_id=WORKSPACE,
        type=kind,
        status="queued",
        stage="queued",
        expected_item_count=count,
        configuration=configuration or {"settings": {}},
    )
    session.add(job)
    session.flush()
    items = [
        JobItemRecord(
            id="item_" + uuid4().hex,
            workspace_id=WORKSPACE,
            job_id=job.id,
            item_key=f"image-{i}",
            ordinal=i,
            status="reused" if i < reused else "pending",
            stage="queued",
        )
        for i in range(count)
    ]
    session.add_all(items)
    session.flush()
    if deliverables:
        for item in items:
            asset_id = "asset_" + uuid4().hex
            session.add(
                AssetRecord(
                    id=asset_id,
                    workspace_id=WORKSPACE,
                    origin_kind="generated",
                    lifecycle_status="ready",
                    original_filename="output.png",
                    content_type="image/png",
                    byte_size=10,
                    width=96,
                    height=96,
                    sha256="a" * 64,
                    storage_provider="local",
                    storage_bucket="test",
                    object_key=asset_id + ".png",
                )
            )
            session.flush()
            session.add(
                JobAssetRecord(
                    workspace_id=WORKSPACE,
                    job_id=job.id,
                    job_item_id=item.id,
                    asset_id=asset_id,
                    direction="output",
                    role="generated_image",
                    ordinal=item.ordinal,
                    is_deliverable=True,
                )
            )
        session.flush()
    return job, items


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("local_edit", 5),
        ("product_image", 5),
        ("model_pose_transfer", 5),
        ("creative_image", 15),
        ("reference_remix", 15),
        ("detail_pack", 15),
        ("domestic_main_image", 15),
        ("amazon_visual_suite", 15),
        ("domestic_detail_review", 0),
    ],
)
def test_every_executable_job_has_a_tariff(kind, expected):
    assert image_unit_credits(kind) == expected
    assert {value.value for value in JobType} == {
        "local_edit",
        "product_image",
        "model_pose_transfer",
        "creative_image",
        "reference_remix",
        "detail_pack",
        "domestic_main_image",
        "amazon_visual_suite",
        "domestic_detail_review",
    }


@pytest.mark.parametrize(
    "preset,expected",
    [
        ("scene_main", 15),
        ("white_background", 5),
        ("model_image", 5),
        ("change_model", 5),
        ("try_on", 5),
        ("change_scene", 5),
        ("change_background", 5),
        ("change_color", 5),
    ],
)
def test_product_presets_are_classified(preset, expected):
    assert (
        image_unit_credits("product_image", {"settings": {"task_type": preset}})
        == expected
    )


def test_partial_success_releases_unused_hold_and_redelivery_is_idempotent(client):
    with client.app.state.session_factory() as session:
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        original = account.balance_credits
        job, items = seed_job(session)
        hold = services.reserve_image_credits(session, WORKSPACE, job, 0)
        assert hold.reserved_credits == job.estimated_credits == 120
        assert account.reserved_credits == 120
        assert services.reserve_image_credits(session, WORKSPACE, job, 0).id == hold.id
        for index, item in enumerate(items):
            item.status = "succeeded" if index < 3 else "failed"
        session.flush()
        services.settle_credits(session, job, 0)
        session.flush()
        services.settle_credits(session, job, 0)
        assert job.actual_credits == 45
        assert account.balance_credits == original - 45
        assert account.reserved_credits == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(CreditLedgerEntryRecord)
                .where(CreditLedgerEntryRecord.entry_type == "settle")
            )
            == 1
        )


@pytest.mark.parametrize(
    "kind", ["detail_pack", "reference_remix", "amazon_visual_suite", "product_image"]
)
def test_retry_and_local_revision_only_charge_new_items(client, kind):
    with client.app.state.session_factory() as session:
        job, items = seed_job(session, kind=kind, reused=6)
        original = session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits
        hold = services.reserve_image_credits(session, WORKSPACE, job, 0)
        unit = image_unit_credits(kind)
        assert hold.reserved_credits == 2 * unit
        for item in items:
            if item.status == "pending":
                item.status = "succeeded"
        session.flush()
        services.settle_credits(session, job, 0)
        assert job.actual_credits == 2 * unit
        assert (
            session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits
            == original - 2 * unit
        )


def test_cancellation_releases_all_unused_credits(client):
    with client.app.state.session_factory() as session:
        job, _ = seed_job(session, count=6)
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        original = account.balance_credits
        hold = services.reserve_image_credits(session, WORKSPACE, job)
        assert hold.reserved_credits == 90
        services.release_credits(session, job)
        services.release_credits(session, job)
        assert account.balance_credits == original
        assert account.reserved_credits == 0


def test_supplier_quota_switch_cannot_bypass_customer_credits(client):
    services.set_quota_enabled(False)
    try:
        with client.app.state.session_factory() as session:
            job, _ = seed_job(session, count=6)
            assert (
                services.reserve_image_credits(session, WORKSPACE, job).reserved_credits
                == 90
            )
    finally:
        services.set_quota_enabled(True)


def test_client_tariff_cannot_override_server_tariff(client):
    with client.app.state.session_factory() as session:
        job, _ = seed_job(
            session,
            configuration={
                "settings": {},
                PRICING_KEY: {"credits_per_image": 0, "version": "forged"},
            },
        )
        reservation = services.reserve_image_credits(session, WORKSPACE, job)
        assert reservation.reserved_credits == 120
        assert reservation.pricing_snapshot["credits_per_image"] == 15
        assert job.configuration[PRICING_KEY]["version"] == "forged"


def test_credit_endpoints_are_workspace_scoped(client):
    response = client.get(f"/api/v1/workspaces/{WORKSPACE}/credits")
    assert response.status_code == 200
    assert response.json()["enforcement_enabled"] is True
    assert len(response.json()["pricing"]) == 16
    assert client.get("/api/v1/workspaces/not-mine/credits").status_code in {403, 404}
    assert client.get("/api/v1/workspaces/not-mine/credits/ledger").status_code in {
        403,
        404,
    }


def test_insufficient_balance_creates_no_job_or_reservation(client):
    source, mask = (
        upload_asset(client),
        upload_asset(client, filename="mask.png", media_kind="mask"),
    )
    with client.app.state.session_factory() as session:
        session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits = 4
        session.commit()
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/jobs",
        headers={"Idempotency-Key": str(uuid4())},
        json=local_edit_payload(source["id"], mask["id"]),
    )
    assert response.status_code == 402, response.text
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JobRecord)) == 0
        assert (
            session.scalar(select(func.count()).select_from(CreditReservationRecord))
            == 0
        )


def test_quote_and_actual_reservation_match_without_creating_quote_job(client):
    source, mask = (
        upload_asset(client),
        upload_asset(client, filename="mask.png", media_kind="mask"),
    )
    body = local_edit_payload(source["id"], mask["id"])
    quote = client.post(f"/api/v1/workspaces/{WORKSPACE}/credits/quote", json=body)
    assert quote.status_code == 200, quote.text
    assert quote.json()["estimated_credits"] == 5
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JobRecord)) == 0
    created = create_local_edit_job(client, source["id"], mask["id"])
    assert created["cost"]["estimated_credits"] == 5
    assert dispatch_pending(client) == 1
    complete = client.get(f"/api/v1/workspaces/{WORKSPACE}/jobs/{created['id']}").json()
    assert complete["status"] == "completed"
    assert complete["cost"]["actual_credits"] == 0  # mock returns no image
    ledger = client.get(f"/api/v1/workspaces/{WORKSPACE}/credits/ledger").json()[
        "items"
    ]
    assert [entry["entry_type"] for entry in ledger] == ["settle", "reserve"]
    assert ledger[0]["delta_balance_credits"] == 0
    assert ledger[0]["released_credits"] == 5


def test_concurrent_submissions_cannot_overdraw_same_wallet(client):
    source, mask = (
        upload_asset(client),
        upload_asset(client, filename="mask.png", media_kind="mask"),
    )
    with client.app.state.session_factory() as session:
        session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits = 5
        session.commit()
    body = local_edit_payload(source["id"], mask["id"])

    def submit(_):
        return client.post(
            f"/api/v1/workspaces/{WORKSPACE}/jobs",
            headers={"Idempotency-Key": str(uuid4())},
            json=body,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert sorted(response.status_code for response in responses) == [202, 402], [
        response.text for response in responses
    ]
    with client.app.state.session_factory() as session:
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        assert account.balance_credits == account.reserved_credits == 5


def test_reserving_does_not_change_hash_bound_generation_configuration(client):
    with client.app.state.session_factory() as session:
        configuration = {
            "settings": {"market_region": "domestic"},
            "input": {"product": "fixed"},
        }
        job, _ = seed_job(session, configuration=configuration)
        services.reserve_image_credits(session, WORKSPACE, job)
        assert job.configuration == configuration
        assert "customer_credit_pricing" not in job.configuration


def test_concurrent_settlement_cannot_charge_twice(client):
    factory = client.app.state.session_factory
    with factory() as session:
        job, items = seed_job(session, count=6)
        original = session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits
        services.reserve_image_credits(session, WORKSPACE, job)
        for item in items:
            item.status = "succeeded"
        session.commit()
        job_id = job.id

    def settle(_):
        with factory() as session:
            services.settle_credits(session, session.get(JobRecord, job_id), 0)
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(settle, range(2)))
    with factory() as session:
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        assert account.balance_credits == original - 90
        assert account.reserved_credits == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(CreditLedgerEntryRecord)
                .where(
                    CreditLedgerEntryRecord.job_id == job_id,
                    CreditLedgerEntryRecord.entry_type == "settle",
                )
            )
            == 1
        )


def test_no_deliverable_image_releases_entire_reservation(client):
    with client.app.state.session_factory() as session:
        job, items = seed_job(session, count=6, deliverables=False)
        original = session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits
        services.reserve_image_credits(session, WORKSPACE, job)
        for item in items:
            item.status = "succeeded"
        session.flush()
        services.settle_credits(session, job, 90)
        assert job.actual_credits == 0
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        assert account.balance_credits == original
        assert account.reserved_credits == 0


def test_auxiliary_outputs_are_not_charged_and_settled_bill_is_immutable(client):
    from app.credit_billing import successful_image_credits

    with client.app.state.session_factory() as session:
        job, items = seed_job(session, count=2)
        services.reserve_image_credits(session, WORKSPACE, job)
        for item in items:
            item.status = "succeeded"
        links = list(
            session.scalars(
                select(JobAssetRecord)
                .where(JobAssetRecord.job_id == job.id)
                .order_by(JobAssetRecord.ordinal)
            )
        )
        links[1].is_deliverable = False
        session.add(
            JobAssetRecord(
                workspace_id=WORKSPACE,
                job_id=job.id,
                job_item_id=items[0].id,
                asset_id=links[0].asset_id,
                direction="output",
                role="preview",
                ordinal=0,
                is_deliverable=True,
            )
        )
        session.flush()
        services.settle_credits(session, job, 0)
        assert job.actual_credits == 15
        links[0].is_deliverable = False
        session.flush()
        assert successful_image_credits(session, job) == 15


def test_persisted_image_is_charged_once_through_actual_execution(client):
    from dataclasses import replace
    from hashlib import sha256
    from io import BytesIO

    from PIL import Image

    from app.provider import FakeProvider, ProviderArtifact

    payload = BytesIO()
    Image.new("RGB", (96, 96), (100, 90, 140)).save(payload, "PNG")
    png = payload.getvalue()

    class ArtifactProvider(FakeProvider):
        name = "synthetic-credit-test"

        async def execute(self, request):
            return replace(
                await super().execute(request),
                artifact=ProviderArtifact(
                    payload=png,
                    filename="synthetic-test.png",
                    content_type="image/png",
                    width=96,
                    height=96,
                    sha256=sha256(png).hexdigest(),
                ),
            )

    client.app.state.task_runner.coordinator.provider = ArtifactProvider()
    source, mask = (
        upload_asset(client),
        upload_asset(client, filename="mask.png", media_kind="mask"),
    )
    before = client.get(f"/api/v1/workspaces/{WORKSPACE}/credits").json()
    created = create_local_edit_job(client, source["id"], mask["id"])
    assert dispatch_pending(client) == 1
    completed = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/jobs/{created['id']}"
    ).json()
    assert completed["status"] == "completed", completed
    assert any(
        link["direction"] == "output" and link["is_deliverable"]
        for link in completed["assets"]
    )
    assert completed["cost"]["actual_credits"] == 5
    after = client.get(f"/api/v1/workspaces/{WORKSPACE}/credits").json()
    assert after["balance_credits"] == before["balance_credits"] - 5
    assert after["reserved_credits"] == 0


def test_different_jobs_settle_and_release_same_wallet_concurrently(client):
    factory = client.app.state.session_factory
    with factory() as session:
        original = session.get(WorkspaceCreditAccountRecord, WORKSPACE).balance_credits
        jobs = []
        for _ in range(3):
            job, items = seed_job(session, count=1)
            services.reserve_image_credits(session, WORKSPACE, job)
            items[0].status = "succeeded"
            jobs.append(job.id)
        session.commit()

    def complete(index):
        with factory() as session:
            job = session.get(JobRecord, jobs[index])
            if index == 2:
                services.release_credits(session, job)
            else:
                services.settle_credits(session, job, 0)
            session.commit()

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(complete, range(3)))
    with factory() as session:
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE)
        assert account.balance_credits == original - 30
        assert account.reserved_credits == 0
