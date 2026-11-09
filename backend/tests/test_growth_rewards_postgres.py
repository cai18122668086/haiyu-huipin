"""Concurrent check-ins, different paid buyers and additive reward migration."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text

from app import growth_rewards as growth
from app import services
from app.auth import Principal, WorkspaceContext
from app.credit_payment_routes import (
    CreditPurchaseCreate,
    _settle_native,
    create_purchase,
)
from app.domain import MembershipRole
from app.models import (
    DailyCheckinRecord,
    PromotionCampaignRecord,
    ReferralRewardRecord,
    UserRecord,
    WorkspaceCreditAccountRecord,
    utc_now,
)
from app.onboarding import ensure_personal_workspace, personal_workspace_id
from tests.test_credit_payments import paid_fields
from tests.test_credit_payments_postgres import postgres_credits as postgres_credits


@pytest.fixture
def growth_pg(postgres_credits):
    factory, engine = postgres_credits
    with factory() as session:
        inviter = session.get(UserRecord, "usr_test")
        inviter.phone_number = "+8613800000000"
        inviter.created_at = utc_now() - timedelta(days=1)
        ensure_personal_workspace(session, inviter)
        for identifier in [growth.CHECKIN, growth.REFERRAL]:
            session.add(
                PromotionCampaignRecord(
                    id=identifier,
                    enabled=True,
                    starts_at=utc_now() - timedelta(minutes=1),
                )
            )
        code = growth.state(session, inviter, personal_workspace_id(inviter.id))[
            "invite_url"
        ].split("=")[-1]
        for i in range(6):
            buyer = UserRecord(
                id=f"usr_buyer_{i}",
                auth_provider="test",
                auth_subject=f"buyer_{i}",
                phone_number=f"+861380000000{i + 1}",
            )
            session.add(buyer)
            session.flush()
            ensure_personal_workspace(session, buyer)
            growth.bind_signup_invitation(session, buyer, code)
        session.commit()
    services.set_credit_pilot_workspaces(
        [
            "ws_test",
            personal_workspace_id("usr_test"),
            *[personal_workspace_id(f"usr_buyer_{i}") for i in range(6)],
        ]
    )
    return factory, engine


def test_concurrent_daily_requests_issue_one_credit_lot(growth_pg):
    factory, _ = growth_pg
    barrier = Barrier(6)

    def claim():
        with factory() as session:
            user = session.get(UserRecord, "usr_test")
            barrier.wait(timeout=10)
            _, created = growth.checkin(session, user)
            session.commit()
            return created

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda _: claim(), range(6))).count(True) == 1
    with factory() as session:
        assert (
            session.get(
                WorkspaceCreditAccountRecord, personal_workspace_id("usr_test")
            ).balance_credits
            == 10
        )
        assert session.scalar(select(func.count()).select_from(DailyCheckinRecord)) == 1
        assert (
            session.get(WorkspaceCreditAccountRecord, "ws_test").balance_credits == 20
        )


def test_parallel_buyer_callbacks_and_checkin_serialize_inviter_wallet(
    growth_pg, monkeypatch
):
    from app import haiyu_payments as payments

    factory, _ = growth_pg
    monkeypatch.setattr(
        payments,
        "identity",
        lambda *_: {"merchant_id": "test-seller", "app_id": "test-app"},
    )
    monkeypatch.setattr(
        payments,
        "create_checkout",
        lambda *args: "https://example.test/checkout/" + args[2],
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace()))
    )
    orders = []
    for i in range(6):
        user_id = f"usr_buyer_{i}"
        with factory() as session:
            workspace = WorkspaceContext(
                Principal(user_id), personal_workspace_id(user_id), MembershipRole.OWNER
            )
            orders.append(
                create_purchase(
                    CreditPurchaseCreate(package_id="pack_small", channel="alipay"),
                    request,
                    workspace,
                    session,
                    uuid4().hex,
                ).model_dump()
            )
    barrier = Barrier(7)

    def pay(order):
        barrier.wait(timeout=10)
        with factory() as session:
            fields = paid_fields(order, money="99.00", trade="pg_growth_" + order["id"])
            _settle_native(session, "alipay", fields)
            _settle_native(session, "alipay", fields)

    def claim():
        with factory() as session:
            user = session.get(UserRecord, "usr_test")
            barrier.wait(timeout=10)
            growth.checkin(session, user)
            session.commit()

    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(pay, order) for order in orders] + [pool.submit(claim)]
        for future in futures:
            future.result(timeout=20)
    with factory() as session:
        assert (
            session.get(
                WorkspaceCreditAccountRecord, personal_workspace_id("usr_test")
            ).balance_credits
            == 1090
        )
        assert (
            session.scalar(select(func.count()).select_from(ReferralRewardRecord)) == 6
        )
        for i in range(6):
            assert (
                session.get(
                    WorkspaceCreditAccountRecord,
                    personal_workspace_id(f"usr_buyer_{i}"),
                ).balance_credits
                == 1800
            )


def test_growth_migration_preserves_existing_campaign_and_wallet(postgres_credits):
    factory, engine = postgres_credits
    with factory() as session:
        session.add(
            PromotionCampaignRecord(
                id="replica-welcome-v1",
                enabled=True,
                starts_at=utc_now() - timedelta(days=1),
            )
        )
        session.commit()
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/20261106_0039_growth_rewards.py"
    )
    spec = importlib.util.spec_from_file_location("growth_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        for table in [
            "referral_reward_reversals",
            "referral_rewards",
            "growth_reward_adjustments",
            "invitations",
            "invitation_codes",
            "daily_checkins",
        ]:
            connection.execute(text(f"DROP TABLE {table}"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        assert (
            connection.scalar(
                text(
                    "SELECT enabled FROM promotion_campaigns "
                    "WHERE id='replica-welcome-v1'"
                )
            )
            is True
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM promotion_campaigns "
                    "WHERE id!='replica-welcome-v1' AND enabled=false"
                )
            )
            == 2
        )
        assert (
            connection.scalar(
                text(
                    "SELECT balance_credits FROM workspace_credit_accounts "
                    "WHERE workspace_id='ws_test'"
                )
            )
            == 20
        )
