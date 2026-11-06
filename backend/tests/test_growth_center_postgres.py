"""Cross-tab reminder serialization and the additive production migration."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from app import growth_center as center
from app.models import NewcomerOfferRecord, PromotionCampaignRecord, UserRecord, utc_now
from app.onboarding import ensure_personal_workspace, personal_workspace_id
from tests.test_credit_payments_postgres import postgres_credits as postgres_credits


def test_concurrent_tabs_only_receive_one_newcomer_impression(postgres_credits):
    factory, _ = postgres_credits
    with factory() as session:
        user = session.get(UserRecord, "usr_test")
        user.phone_number = "+8613800099999"
        ensure_personal_workspace(session, user)
        for identifier in ["replica-welcome-v1", center.REMINDER]:
            session.add(
                PromotionCampaignRecord(
                    id=identifier, enabled=True, starts_at=utc_now() - timedelta(days=1)
                )
            )
        session.add(
            NewcomerOfferRecord(
                user_id=user.id,
                session_id="synthetic-completed",
                first_viewed_at=utc_now() - timedelta(minutes=3),
                last_shown_window=-1,
            )
        )
        session.commit()
    barrier = Barrier(6)

    def attempt(_):
        with factory() as session:
            barrier.wait(timeout=10)
            user = center.personal_user(
                session, "usr_test", personal_workspace_id("usr_test"), lock=True
            )
            result = center.claim_impression(
                session, user, personal_workspace_id(user.id)
            )
            session.commit()
            return result["should_show"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(attempt, range(6))).count(True) == 1


def test_growth_center_migration_preserves_existing_campaigns(postgres_credits):
    _, engine = postgres_credits
    path = (
        Path(__file__).parents[1] / "migrations/versions/20261106_0040_growth_center.py"
    )
    spec = importlib.util.spec_from_file_location("growth_center_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        NewcomerOfferRecord.__table__.drop(connection)
        connection.execute(text("DROP INDEX ix_credit_purchases_buyer_status"))
        for identifier in [
            "replica-welcome-v1",
            "daily-checkin-v1",
            "referral-credits-v1",
        ]:
            connection.execute(
                text(
                    "INSERT INTO promotion_campaigns (id,enabled,starts_at) "
                    "VALUES (:id,true,CURRENT_TIMESTAMP)"
                ),
                {"id": identifier},
            )
        before = connection.execute(
            text("SELECT id,enabled,starts_at FROM promotion_campaigns ORDER BY id")
        ).all()
        balance = connection.execute(
            text(
                "SELECT balance_credits FROM workspace_credit_accounts "
                "WHERE workspace_id='ws_test'"
            )
        ).scalar_one()
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert (
            connection.execute(
                text(
                    "SELECT id,enabled,starts_at FROM promotion_campaigns "
                    "WHERE id <> 'newcomer-reminder-v1' ORDER BY id"
                )
            ).all()
            == before
        )
        assert (
            connection.execute(
                text(
                    "SELECT enabled FROM promotion_campaigns "
                    "WHERE id='newcomer-reminder-v1'"
                )
            ).scalar_one()
            is False
        )
        assert (
            connection.execute(
                text(
                    "SELECT balance_credits FROM workspace_credit_accounts "
                    "WHERE workspace_id='ws_test'"
                )
            ).scalar_one()
            == balance
        )
        assert inspect(connection).has_table("newcomer_offer_views")
