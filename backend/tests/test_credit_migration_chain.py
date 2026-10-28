from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def test_membership_migrations_follow_existing_phone_identity_and_preserve_data():
    root = Path(__file__).parents[1]
    config = Config()
    config.set_main_option("script_location", str(root / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["20261028_0033"]
    assert scripts.get_revision("20261028_0029").down_revision == "20261027_0028"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in [
            "CREATE TABLE users (id TEXT PRIMARY KEY, phone_number TEXT)",
            "INSERT INTO users VALUES ('existing-user', 'synthetic-phone')",
            "CREATE TABLE workspaces (id TEXT PRIMARY KEY)",
            "INSERT INTO workspaces VALUES ('existing-workspace')",
            "CREATE TABLE workspace_credit_accounts (workspace_id TEXT PRIMARY KEY, "
            "balance_credits BIGINT, reserved_credits BIGINT)",
            "INSERT INTO workspace_credit_accounts "
            "VALUES ('existing-workspace', 157, 10)",
            "CREATE TABLE jobs (id TEXT PRIMARY KEY)",
            "CREATE TABLE credit_reservations (id TEXT PRIMARY KEY, "
            "reserved_credits BIGINT)",
            "INSERT INTO credit_reservations VALUES ('existing-reservation', 10)",
            "CREATE TABLE credit_ledger_entries (id INTEGER PRIMARY KEY)",
        ]:
            connection.execute(text(statement))
        for name in [
            "20261028_0029_credit_pricing_snapshot.py",
            "20261028_0030_credit_purchases.py",
            "20261028_0031_credit_memberships.py",
            "20261028_0032_credit_refunds.py",
            "20261028_0033_admin_entitlements.py",
        ]:
            spec = spec_from_file_location(
                "credit_migration", root / "migrations/versions" / name
            )
            migration = module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        assert (
            connection.execute(text("SELECT phone_number FROM users")).scalar()
            == "synthetic-phone"
        )
        assert (
            connection.execute(
                text("SELECT balance_credits FROM workspace_credit_accounts")
            ).scalar()
            == 157
        )
        assert (
            connection.execute(
                text("SELECT reserved_credits FROM credit_reservations")
            ).scalar()
            == 10
        )
        assert (
            connection.execute(
                text("SELECT pricing_snapshot FROM credit_reservations")
            ).scalar()
            is None
        )
        assert {"credit_purchases", "credit_memberships", "credit_grants"} <= set(
            inspect(connection).get_table_names()
        )
    engine.dispose()
