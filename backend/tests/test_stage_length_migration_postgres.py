from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url

TABLES = ("jobs", "job_items", "crawl_tasks")
LONG_STAGE = "retry_wait_outcome_reconciliation"


@pytest.fixture
def migration_connection():
    raw_url = os.environ.get("POLARIS_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("set POLARIS_TEST_DATABASE_URL for isolated migration tests")
    url = make_url(raw_url)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or not url.database
        or not any(token in url.database.lower() for token in ("test", "ci"))
    ):
        raise pytest.UsageError("Migration tests require a local test/CI database")

    engine = sa.create_engine(url)
    schema = f"stage_migration_{uuid4().hex}"
    # The schema, synthetic records and DDL disappear in the transaction rollback.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


def _load_migration(name="20261017_0026_job_stage_lengths.py"):
    path = Path(__file__).resolve().parents[1] / "migrations/versions" / name
    spec = importlib.util.spec_from_file_location("stage_length_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("length", [32, 64])
def test_plan_schema_expansion_preserves_protocols_and_stored_plans(
    migration_connection, length
):
    from app.aurora_main_image_native_fast import SCHEMA_VERSION
    from app.models import JobPlanRecord

    connection = migration_connection
    connection.execute(
        sa.text(
            "CREATE TABLE job_plans (id text PRIMARY KEY, "
            f"schema_version varchar({length}) NOT NULL, payload jsonb NOT NULL)"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO job_plans VALUES ('before', 'aurora-main-image-plan-v2', '{}')"
        )
    )
    migration = _load_migration("20261030_0035_job_plan_schema_length.py")
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.upgrade()
    assert (
        next(
            item["type"].length
            for item in sa.inspect(connection).get_columns("job_plans")
            if item["name"] == "schema_version"
        )
        == JobPlanRecord.__table__.c.schema_version.type.length
        == 64
    )
    connection.execute(
        sa.text(
            "INSERT INTO job_plans VALUES ('after', :version, "
            "'{\"pages\": [1,2,3,4,5,6]}')"
        ),
        {"version": SCHEMA_VERSION},
    )
    with (
        Operations.context(MigrationContext.configure(connection)),
        pytest.raises(RuntimeError, match="Keep plan schema identifiers at 64"),
    ):
        migration.downgrade()
    assert (
        connection.scalar(
            sa.text("SELECT schema_version FROM job_plans WHERE id='after'")
        )
        == SCHEMA_VERSION
    )
    assert (
        connection.scalar(
            sa.text("SELECT schema_version FROM job_plans WHERE id='before'")
        )
        == "aurora-main-image-plan-v2"
    )


def _create_tables(connection, lengths):
    for table, length in zip(TABLES, lengths, strict=True):
        connection.execute(
            sa.text(
                f'CREATE TABLE "{table}" (id text PRIMARY KEY, stage varchar({length}))'
            )
        )
        value = LONG_STAGE if length == 64 else "queued"
        connection.execute(
            sa.text(f'INSERT INTO "{table}" VALUES (:id, :stage)'),
            {"id": "synthetic-before", "stage": value},
        )


def _lengths(connection):
    return [
        next(
            item["type"].length
            for item in sa.inspect(connection).get_columns(table)
            if item["name"] == "stage"
        )
        for table in TABLES
    ]


@pytest.mark.parametrize("length", [32, 64])
def test_stage_upgrade_preserves_fresh_and_already_expanded_data(
    migration_connection, length
):
    connection = migration_connection
    _create_tables(connection, [length] * 3)
    migration = _load_migration()
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.upgrade()
    assert _lengths(connection) == [64, 64, 64]
    for table in TABLES:
        before = connection.scalar(
            sa.text(f'SELECT stage FROM "{table}" WHERE id = :id'),
            {"id": "synthetic-before"},
        )
        assert before == (LONG_STAGE if length == 64 else "queued")
        connection.execute(
            sa.text(f'INSERT INTO "{table}" VALUES (:id, :stage)'),
            {"id": "synthetic-after", "stage": LONG_STAGE},
        )


def test_unexpected_schema_is_rejected_before_any_column_changes(migration_connection):
    connection = migration_connection
    _create_tables(connection, [32, 32, 128])
    with (
        Operations.context(MigrationContext.configure(connection)),
        pytest.raises(RuntimeError, match="Unexpected crawl_tasks.stage"),
    ):
        _load_migration().upgrade()
    assert _lengths(connection) == [32, 32, 128]


def test_application_rollback_does_not_shrink_or_lose_long_states(migration_connection):
    connection = migration_connection
    _create_tables(connection, [64, 64, 64])
    with (
        Operations.context(MigrationContext.configure(connection)),
        pytest.raises(RuntimeError, match="Keep stage columns at 64"),
    ):
        _load_migration().downgrade()
    assert _lengths(connection) == [64, 64, 64]
    for table in TABLES:
        assert connection.scalar(sa.text(f'SELECT stage FROM "{table}"')) == LONG_STAGE
