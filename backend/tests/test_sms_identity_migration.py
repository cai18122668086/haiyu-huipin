from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_sms_migration_preserves_existing_users_and_adds_credentials() -> None:
    source = (
        Path(__file__).parents[1] / "migrations/versions/20261027_0028_sms_identity.py"
    )
    spec = spec_from_file_location("sms_identity_migration", source)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE users (id TEXT PRIMARY KEY, email_normalized TEXT)")
        )
        connection.execute(
            text("INSERT INTO users VALUES ('legacy-user', 'legacy@example.test')")
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        row = connection.execute(
            text(
                "SELECT id, email_normalized, phone_number, password_hash, "
                "password_version FROM users"
            )
        ).one()
        assert tuple(row) == ("legacy-user", "legacy@example.test", None, None, 0)
        indexes = inspect(connection).get_indexes("users")
        assert any(
            index["unique"] and index["column_names"] == ["phone_number"]
            for index in indexes
        )
    engine.dispose()
