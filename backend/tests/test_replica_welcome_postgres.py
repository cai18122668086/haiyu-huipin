"""Real PostgreSQL locking and additive migration checks with synthetic accounts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text

from app import services
from app.auth import Principal, WorkspaceContext
from app.credit_payment_routes import CreditPurchaseCreate, create_purchase
from app.domain import MembershipRole
from app.errors import ApiError
from app.models import (
    CreditPurchaseRecord,
    PromotionCampaignRecord,
    ReplicaWelcomeGrantRecord,
    UserRecord,
    utc_now,
)
from app.onboarding import ensure_personal_workspace, personal_workspace_id
from app.replica_welcome import CAMPAIGN, NEWCOMER_PACK, bind_session, ensure_grant
from tests.test_credit_payments_postgres import postgres_credits as postgres_credits
from tests.test_replica_welcome import document


@pytest.fixture
def welcome_pg(postgres_credits):
    factory, engine = postgres_credits
    with factory() as session:
        user = session.get(UserRecord, "usr_test")
        user.phone_number = "+8613800088888"
        user.created_at = utc_now()
        session.add(
            PromotionCampaignRecord(
                id=CAMPAIGN, enabled=True, starts_at=utc_now() - timedelta(minutes=1)
            )
        )
        ensure_personal_workspace(session, user)
        session.commit()
    services.set_credit_pilot_workspaces(["ws_test", personal_workspace_id("usr_test")])
    return factory, engine


def test_parallel_signup_and_trial_binding_grant_only_once(welcome_pg):
    factory, _ = welcome_pg
    barrier = Barrier(6)

    def signup():
        with factory() as session:
            user = session.get(UserRecord, "usr_test")
            barrier.wait(timeout=10)
            assert ensure_grant(session, user)
            session.commit()

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: signup(), range(6)))
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(ReplicaWelcomeGrantRecord))
            == 1
        )

    barrier = Barrier(6)

    def claim():
        with factory() as session:
            row = document(session)
            barrier.wait(timeout=10)
            try:
                bind_session(session, row)
                session.commit()
                return True
            except ApiError as error:
                assert error.code == "WELCOME_TRIAL_ALREADY_BOUND"
                session.rollback()
                return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda _: claim(), range(6))).count(True) == 1


def test_different_checkout_keys_reuse_single_newcomer_order(welcome_pg, monkeypatch):
    from app import haiyu_payments as payments

    factory, _ = welcome_pg
    with factory() as session:
        ensure_grant(session, session.get(UserRecord, "usr_test"))
        session.commit()
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
    workspace = WorkspaceContext(
        Principal("usr_test"), personal_workspace_id("usr_test"), MembershipRole.OWNER
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace()))
    )
    barrier = Barrier(6)

    def buy():
        with factory() as session:
            barrier.wait(timeout=10)
            return create_purchase(
                CreditPurchaseCreate(package_id=NEWCOMER_PACK, channel="alipay"),
                request,
                workspace,
                session,
                uuid4().hex,
            ).id

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(lambda _: buy(), range(6)))
    assert len(set(ids)) == 1
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(CreditPurchaseRecord)) == 1
        )


def test_additive_migration_preserves_balance_and_defaults_disabled(welcome_pg):
    import importlib.util
    from pathlib import Path

    _, engine = welcome_pg
    path = (
        Path(__file__).parents[1]
        / "migrations/versions/20261106_0038_replica_welcome.py"
    )
    spec = importlib.util.spec_from_file_location("welcome_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE replica_welcome_grants"))
        connection.execute(text("DROP TABLE promotion_campaigns"))
        connection.execute(text("DROP INDEX uq_newcomer_pending_user"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        assert (
            connection.scalar(
                text(
                    "SELECT balance_credits FROM workspace_credit_accounts "
                    "WHERE workspace_id='ws_test'"
                )
            )
            == 20
        )
        assert (
            connection.scalar(text("SELECT enabled FROM promotion_campaigns")) is False
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM replica_welcome_grants")) == 0
        )
