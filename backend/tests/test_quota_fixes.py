"""Unit and integration tests for the quota hardening fixes.

Covers:
- release_credits  behaviour
- reserve_credits idempotency
- quota_enabled global switch
- retry-endpoint source-reservation cleanup
"""

from __future__ import annotations

from io import BytesIO
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import routes_v1, services
from app.config import Settings
from app.domain import (
    CreditReservationStatus,
    JobItemStatus,
    JobStatus,
    JobType,
)
from app.main import create_app
from app.models import (
    CreditLedgerEntryRecord,
    CreditReservationRecord,
    JobItemRecord,
    JobRecord,
    WorkspaceCreditAccountRecord,
    utc_now,
)
from app.services import (
    new_id,
    release_credits,
    reserve_credits,
    set_credit_enforcement_enabled,
    set_quota_enabled,
    settle_credits,
)

WORKSPACE_ID = "ws_test"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_account(session: Session) -> WorkspaceCreditAccountRecord:
    account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
    if account is None:
        account = WorkspaceCreditAccountRecord(
            workspace_id=WORKSPACE_ID,
            balance_credits=100,
            reserved_credits=0,
        )
        session.add(account)
        session.flush()
    else:
        # Reset to known state in case the fixture seeded a different value.
        account.balance_credits = 100
        account.reserved_credits = 0
        session.flush()
    return account


def _make_job(
    session: Session,
    *,
    estimated_credits: int = 10,
    status: JobStatus = JobStatus.QUEUED,
) -> JobRecord:
    job = JobRecord(
        id=new_id("job"),
        workspace_id=WORKSPACE_ID,
        type=JobType.LOCAL_EDIT.value,
        status=status.value,
        stage=status.value,
        estimated_credits=estimated_credits,
    )
    session.add(job)
    session.flush()
    return job


# ---------------------------------------------------------------------------
# Fix 1 — release_credits
# ---------------------------------------------------------------------------


def test_release_credits_frees_reserved_headroom(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        _seed_account(session)
        job = _make_job(session, estimated_credits=15)
        reservation = reserve_credits(session, WORKSPACE_ID, job, 15)
        assert reservation is not None
        assert reservation.status == CreditReservationStatus.ACTIVE.value

        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
        assert account.reserved_credits == 15
        assert account.balance_credits == 100  # unchanged

        release_credits(session, job)

        # reserved headroom is freed, balance untouched
        assert account.reserved_credits == 0
        assert account.balance_credits == 100

        session.refresh(reservation)
        assert reservation.status == CreditReservationStatus.RELEASED.value

        # ledger entry written
        ledger = session.scalars(
            select(CreditLedgerEntryRecord).where(
                CreditLedgerEntryRecord.reservation_id == reservation.id,
                CreditLedgerEntryRecord.entry_type == "release",
            )
        ).all()
        assert len(ledger) == 1
        assert ledger[0].delta_balance_credits == 0
        assert ledger[0].delta_reserved_credits == -15


def test_release_credits_is_idempotent(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        _seed_account(session)
        job = _make_job(session)
        reserve_credits(session, WORKSPACE_ID, job, 10)

        release_credits(session, job)
        # second call must not crash or double-free
        release_credits(session, job)

        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
        assert account.reserved_credits == 0


def test_release_credits_noops_when_no_reservation(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        _seed_account(session)
        job = _make_job(session)
        # never reserved — must not raise
        release_credits(session, job)

        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
        assert account.reserved_credits == 0


# ---------------------------------------------------------------------------
# Fix 1 / general — settle_credits safety
# ---------------------------------------------------------------------------


def test_settle_credits_charges_and_releases(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        _seed_account(session)
        job = _make_job(session, estimated_credits=20)
        reserve_credits(session, WORKSPACE_ID, job, 20)

        # actual cost is only 12 of the 20 reserved
        settle_credits(session, job, 12)

        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
        assert account.reserved_credits == 0
        assert account.balance_credits == 88  # 100 - 12

        reservation = session.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == job.id
            )
        )
        assert reservation.status == CreditReservationStatus.SETTLED.value
        assert reservation.settled_credits == 12


def test_settle_credits_is_idempotent(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        _seed_account(session)
        job = _make_job(session)
        reserve_credits(session, WORKSPACE_ID, job, 10)

        settle_credits(session, job, 10)
        # second call must not double-charge
        settle_credits(session, job, 0)

        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
        assert account.balance_credits == 90  # charged exactly once


# ---------------------------------------------------------------------------
# Fix 2 — reserve_credits idempotency
# ---------------------------------------------------------------------------


def test_reserve_credits_idempotent_same_job(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        _seed_account(session)
        job = _make_job(session)

        first = reserve_credits(session, WORKSPACE_ID, job, 10)
        assert first is not None

        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
        reserved_after_first = account.reserved_credits

        second = reserve_credits(session, WORKSPACE_ID, job, 10)
        assert second is not None
        assert second.id == first.id  # same reservation returned
        assert second.status == CreditReservationStatus.ACTIVE.value

        # reserved headroom must NOT double-count
        session.refresh(account)
        assert account.reserved_credits == reserved_after_first


def test_reserve_credits_rejects_after_settle(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        _seed_account(session)
        job = _make_job(session)
        reserve_credits(session, WORKSPACE_ID, job, 10)
        settle_credits(session, job, 10)

        from app.errors import ApiError

        with pytest.raises(ApiError) as exc_info:
            reserve_credits(session, WORKSPACE_ID, job, 5)
        assert exc_info.value.code == "CREDIT_RESERVATION_EXISTS"


# ---------------------------------------------------------------------------
# Fix 3 — quota_enabled switch
# ---------------------------------------------------------------------------
# Internal beta: unlimited workspace credits with full accounting
# ---------------------------------------------------------------------------


def test_tracking_only_credits_ignore_balance_and_keep_accounting(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        set_credit_enforcement_enabled(False)
        try:
            account = _seed_account(session)
            account.balance_credits = 1
            session.flush()
            job = _make_job(session, estimated_credits=42)

            reservation = reserve_credits(session, WORKSPACE_ID, job, 42)

            assert reservation is not None
            assert reservation.reserved_credits == 42
            assert account.balance_credits == 1
            assert account.reserved_credits == 0
            reserve_entry = session.scalar(
                select(CreditLedgerEntryRecord).where(
                    CreditLedgerEntryRecord.reservation_id == reservation.id,
                    CreditLedgerEntryRecord.entry_type == "reserve",
                )
            )
            assert reserve_entry is not None
            assert reserve_entry.delta_balance_credits == 0
            assert reserve_entry.delta_reserved_credits == 0

            settle_credits(session, job, 40)

            assert reservation.status == CreditReservationStatus.SETTLED.value
            assert reservation.settled_credits == 40
            assert job.actual_credits == 40
            assert account.balance_credits == 1
            assert account.reserved_credits == 0
            settle_entry = session.scalar(
                select(CreditLedgerEntryRecord).where(
                    CreditLedgerEntryRecord.reservation_id == reservation.id,
                    CreditLedgerEntryRecord.entry_type == "settle",
                )
            )
            assert settle_entry is not None
            assert settle_entry.delta_balance_credits == 0
            assert settle_entry.delta_reserved_credits == 0
        finally:
            set_credit_enforcement_enabled(True)


def test_tracking_only_reservation_keeps_its_mode_if_runtime_flag_changes(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        set_credit_enforcement_enabled(False)
        account = _seed_account(session)
        job = _make_job(session, estimated_credits=150)
        reservation = reserve_credits(session, WORKSPACE_ID, job, 150)
        assert reservation is not None

        set_credit_enforcement_enabled(True)
        release_credits(session, job)

        assert reservation.status == CreditReservationStatus.RELEASED.value
        assert account.balance_credits == 100
        assert account.reserved_credits == 0
        release_entry = session.scalar(
            select(CreditLedgerEntryRecord).where(
                CreditLedgerEntryRecord.reservation_id == reservation.id,
                CreditLedgerEntryRecord.entry_type == "release",
            )
        )
        assert release_entry is not None
        assert release_entry.delta_reserved_credits == 0


def test_enforced_workspace_credits_still_reject_insufficient_balance(
    client: TestClient,
) -> None:
    from app.errors import ApiError

    with client.app.state.session_factory() as session:
        set_quota_enabled(True)
        set_credit_enforcement_enabled(True)
        _seed_account(session)
        job = _make_job(session, estimated_credits=101)

        with pytest.raises(ApiError) as exc_info:
            reserve_credits(session, WORKSPACE_ID, job, 101)

        assert exc_info.value.code == "BUDGET_LIMIT_EXCEEDED"


# ---------------------------------------------------------------------------


def test_billing_and_quota_disabled_reserve_returns_none(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(False)
        set_credit_enforcement_enabled(False)
        try:
            _seed_account(session)
            job = _make_job(session)

            result = reserve_credits(session, WORKSPACE_ID, job, 10)
            assert result is None

            account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
            assert account.reserved_credits == 0  # untouched
        finally:
            set_quota_enabled(True)
            set_credit_enforcement_enabled(True)


def test_billing_and_quota_disabled_settle_and_release_are_noops(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(False)
        set_credit_enforcement_enabled(False)
        try:
            _seed_account(session)
            job = _make_job(session)

            # neither should raise
            settle_credits(session, job, 10)
            release_credits(session, job)

            account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
            assert account.reserved_credits == 0
            assert account.balance_credits == 100
        finally:
            set_quota_enabled(True)
            set_credit_enforcement_enabled(True)


def test_billing_and_quota_disabled_does_not_write_ledger(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        set_quota_enabled(False)
        set_credit_enforcement_enabled(False)
        try:
            _seed_account(session)
            job = _make_job(session)
            reserve_credits(session, WORKSPACE_ID, job, 10)
            settle_credits(session, job, 10)

            count = session.scalar(
                select(func.count()).select_from(CreditLedgerEntryRecord).where(
                    CreditLedgerEntryRecord.job_id == job.id
                )
            )
            # no ledger rows should be written when quota is disabled
            assert count == 0
        finally:
            set_quota_enabled(True)
            set_credit_enforcement_enabled(True)


def test_quota_enabled_flag_is_read_from_config(tmp_path) -> None:
    """The create_app startup path must call set_quota_enabled with the config value."""
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_mode="development",
        development_user_id="usr_test",
        development_workspace_id="ws_test",
        access_url_secret="test-access-url-secret",
        database_url=f"sqlite:///{tmp_path / 'test_quota_flag.db'}",
        auto_create_schema=True,
        storage_backend="local",
        storage_dir=tmp_path / "storage",
        auto_process_jobs=False,
        task_dispatcher="inline",
        mock_job_delay_seconds=0,
        provider_quota_enabled=False,
        workspace_credit_enforcement_enabled=False,
    )
    with TestClient(create_app(settings)):
        assert services._quota_enabled is False
        assert services._credit_enforcement_enabled is False

    # reset so other tests don't see the disabled state
    set_quota_enabled(True)
    set_credit_enforcement_enabled(True)


# ---------------------------------------------------------------------------
# Integration — retry endpoint releases source reservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("job_type", "provider_flag", "total_items", "selected_count"),
    [
        (JobType.PRODUCT_IMAGE, "product_image_provider_enabled", 4, 1),
        (
            JobType.AMAZON_VISUAL_SUITE,
            "amazon_visual_suite_provider_enabled",
            21,
            2,
        ),
    ],
)
def test_partial_retry_reserves_only_runnable_provider_requests(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    job_type: JobType,
    provider_flag: str,
    total_items: int,
    selected_count: int,
) -> None:
    quota_calls: list[dict[str, Any]] = []

    def record_quota(_session: Session, **kwargs: Any) -> None:
        quota_calls.append(kwargs)

    monkeypatch.setattr(routes_v1, "reserve_provider_job_quota", record_quota)
    client.app.state.settings.task_runner = "tengli"
    setattr(client.app.state.settings, provider_flag, True)

    with client.app.state.session_factory() as session:
        source = JobRecord(
            id=new_id("job"),
            workspace_id=WORKSPACE_ID,
            type=job_type.value,
            status=JobStatus.FAILED.value,
            stage="failed",
            configuration={"input": {}, "settings": {}},
            expected_item_count=total_items,
            failed_item_count=total_items,
            estimated_cost_min_micros=total_items * 45_000,
            estimated_cost_max_micros=total_items * 90_000,
            estimated_credits=total_items,
            currency="USD",
            finished_at=utc_now(),
        )
        session.add(source)
        session.flush()
        source_items = [
            JobItemRecord(
                id=new_id("item"),
                workspace_id=WORKSPACE_ID,
                job_id=source.id,
                item_key=routes_v1._job_item_key(job_type.value, ordinal),
                ordinal=ordinal,
                status=JobItemStatus.FAILED.value,
                stage="failed",
            )
            for ordinal in range(total_items)
        ]
        session.add_all(source_items)
        session.commit()
        source_id = source.id
        selected_ids = [item.id for item in source_items[:selected_count]]

    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source_id}/retry",
        headers=_idem(),
        json={"scope": "selected", "item_ids": selected_ids},
    )

    assert response.status_code == 201, response.text
    assert len(quota_calls) == 1
    reservation = quota_calls[0]
    # Amazon retries reserve the complete automation envelope even when only a
    # subset of image items is runnable; settlement releases unused allowance.
    expected_requests = (
        51 if job_type == JobType.AMAZON_VISUAL_SUITE else selected_count * 2
    )
    assert reservation["provider_request_count"] == expected_requests
    assert reservation["job"].expected_item_count == total_items

    retried_job_id = response.json()["id"]
    with client.app.state.session_factory() as session:
        runnable_count = session.scalar(
            select(func.count())
            .select_from(JobItemRecord)
            .where(
                JobItemRecord.job_id == retried_job_id,
                JobItemRecord.status == JobItemStatus.PENDING.value,
            )
        )
    assert runnable_count == selected_count


def _image_payload(
    color: tuple[int, int, int] = (30, 80, 130),
) -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


def _idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


def test_retry_releases_source_reservation_integration(
    client: TestClient,
) -> None:
    """End-to-end: a completed job that gets cloned must not leak reserved
    headroom from the source — release_credits is called in the clone handler."""
    set_quota_enabled(True)

    # upload two assets
    source_resp = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
        headers=_idem(),
        data={"origin_kind": "upload", "media_kind": "image"},
        files={"file": ("src.png", _image_payload(), "image/png")},
    )
    assert source_resp.status_code == 201, source_resp.text
    source = source_resp.json()

    mask_resp = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets",
        headers=_idem(),
        data={"origin_kind": "upload", "media_kind": "mask"},
        files={"file": ("mask.png", _image_payload(), "image/png")},
    )
    assert mask_resp.status_code == 201, mask_resp.text
    mask = mask_resp.json()

    # submit a local-edit job
    create_resp = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=_idem(),
        json={
            "type": "local_edit",
            "intent": "submit",
            "display_name": "Clone quota test",
            "input": {
                "source_asset_id": source["id"],
                "selection_mask_asset_id": mask["id"],
            },
            "configuration": {
                "instruction": "test",
                "quality": "balanced",
                "output_format": "png",
            },
        },
    )
    assert create_resp.status_code == 202, create_resp.text
    job = create_resp.json()

    # process to completion (mock provider succeeds quickly)
    from app.outbox import publish_pending_events
    publish_pending_events(
        client.app.state.settings,
        client.app.state.session_factory,
        client.app.state.task_runner,
        limit=2,
    )

    # clone the completed job
    clone_resp = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/clone",
        headers=_idem(),
    )
    assert clone_resp.status_code == 201, clone_resp.text
    cloned = clone_resp.json()

    # verify source job's reservation is NOT active
    with client.app.state.session_factory() as session:
        source_reservation = session.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == job["id"]
            )
        )
        if source_reservation is not None:
            assert source_reservation.status in {
                CreditReservationStatus.SETTLED.value,
                CreditReservationStatus.RELEASED.value,
            }, f"Expected settled/released, got {source_reservation.status}"

        # new job has its own reservation
        new_reservation = session.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == cloned["id"]
            )
        )
        assert new_reservation is not None

        # workspace reserved headroom must only reflect the NEW job
        account = session.get(WorkspaceCreditAccountRecord, WORKSPACE_ID)
        active_sum = (
            session.scalar(
                select(func.sum(CreditReservationRecord.reserved_credits))
                .where(
                    CreditReservationRecord.workspace_id == WORKSPACE_ID,
                    CreditReservationRecord.status
                    == CreditReservationStatus.ACTIVE.value,
                )
            )
            or 0
        )
        assert account.reserved_credits == active_sum
