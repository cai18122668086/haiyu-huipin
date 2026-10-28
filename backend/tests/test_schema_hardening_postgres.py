from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    AmazonSuiteReviewAttemptRecord,
    CreditReservationRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobRecord,
    UserRecord,
    WorkspaceRecord,
)

POSTGRES_ENV = "POLARIS_TEST_DATABASE_URL"
BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PostgresSchema:
    admin_engine: Engine
    engine: Engine
    schema_name: str
    url: str


def _postgres_url() -> URL:
    raw_url = os.environ.get(POSTGRES_ENV)
    if not raw_url:
        pytest.skip(f"set {POSTGRES_ENV} to run real PostgreSQL schema tests")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError(f"{POSTGRES_ENV} must use PostgreSQL")
    return url


def _scoped_url(base_url: URL, schema_name: str) -> str:
    existing_options = str(base_url.query.get("options", "")).strip()
    options = f"{existing_options} -csearch_path={schema_name}".strip()
    return base_url.update_query_dict({"options": options}).render_as_string(
        hide_password=False
    )


@contextmanager
def _migration_database_url(database_url: str) -> Iterator[None]:
    key = "POLARIS_DATABASE_URL"
    previous = os.environ.get(key)
    os.environ[key] = database_url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(BACKEND_DIR / "migrations"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _migrate(database_url: str, revision: str) -> None:
    alembic_url = database_url.replace("%3D", "=")
    with _migration_database_url(alembic_url):
        command.upgrade(_alembic_config(alembic_url), revision)


def _downgrade(database_url: str, revision: str) -> None:
    alembic_url = database_url.replace("%3D", "=")
    with _migration_database_url(alembic_url):
        command.downgrade(_alembic_config(alembic_url), revision)


@contextmanager
def _new_schema(base_url: URL, prefix: str) -> Iterator[PostgresSchema]:
    schema_name = f"{prefix}_{uuid4().hex}"
    admin_engine = create_engine(base_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    database_url = _scoped_url(base_url, schema_name)
    engine = create_engine(
        database_url,
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
    )
    try:
        yield PostgresSchema(
            admin_engine=admin_engine,
            engine=engine,
            schema_name=schema_name,
            url=database_url,
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture(scope="module")
def hardened_schema() -> Iterator[PostgresSchema]:
    with _new_schema(_postgres_url(), "schema_hardening") as schema:
        _migrate(schema.url, "head")
        yield schema


@pytest.fixture
def db(hardened_schema: PostgresSchema) -> Iterator[Engine]:
    with hardened_schema.engine.connect() as connection:
        table_names = [
            row[0]
            for row in connection.execute(
                text(
                    """
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = current_schema()
                      AND tablename <> 'alembic_version'
                    ORDER BY tablename
                    """
                )
            )
        ]
    if table_names:
        quoted = ", ".join(f'"{name}"' for name in table_names)
        with hardened_schema.engine.begin() as connection:
            connection.execute(
                text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
            )
    yield hardened_schema.engine


def _seed_workspace_graph(engine: Engine) -> None:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory.begin() as session:
        for suffix in ("a", "b"):
            user_id = f"user_{suffix}"
            workspace_id = f"workspace_{suffix}"
            session.add(
                UserRecord(
                    id=user_id,
                    auth_provider="test",
                    auth_subject=f"subject_{suffix}",
                    status="active",
                )
            )
            session.flush()
            session.add(
                WorkspaceRecord(
                    id=workspace_id,
                    name=f"Workspace {suffix}",
                    status="active",
                    created_by_user_id=user_id,
                )
            )
            session.flush()
            session.add(
                JobRecord(
                    id=f"job_{suffix}",
                    workspace_id=workspace_id,
                    display_name=f"Job {suffix}",
                )
            )
            session.flush()
            session.add(
                JobItemRecord(
                    id=f"item_{suffix}",
                    workspace_id=workspace_id,
                    job_id=f"job_{suffix}",
                    item_key="primary",
                    ordinal=0,
                )
            )
            session.flush()
            session.add(
                JobAttemptRecord(
                    id=f"attempt_{suffix}",
                    workspace_id=workspace_id,
                    job_id=f"job_{suffix}",
                    job_item_id=f"item_{suffix}",
                    attempt_no=1,
                    lease_token=f"lease_{suffix}",
                    leased_by="schema-test",
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    operation="generate",
                    provider_idempotency_key=f"provider-idem-{suffix}",
                )
            )


def _assert_constraint_rejects(engine: Engine, statement: str) -> str | None:
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(text(statement))
    diagnostic = getattr(caught.value.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def test_same_workspace_job_attempt_allowed_and_cross_workspace_rejected(
    db: Engine,
) -> None:
    _seed_workspace_graph(db)
    with db.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO job_attempts (
                    id, workspace_id, job_id, job_item_id, attempt_no, status,
                    lease_token, leased_by, lease_expires_at, provider,
                    operation, provider_idempotency_key
                ) VALUES (
                    'attempt_a_2', 'workspace_a', 'job_a', 'item_a', 2,
                    'claimed', 'lease_a_2', 'schema-test',
                    CURRENT_TIMESTAMP + INTERVAL '5 minutes', 'mock',
                    'generate', 'provider-idem-a-2'
                )
                """
            )
        )
    constraint = _assert_constraint_rejects(
        db,
        """
        INSERT INTO job_attempts (
            id, workspace_id, job_id, job_item_id, attempt_no, status,
            lease_token, leased_by, lease_expires_at, provider,
            operation, provider_idempotency_key
        ) VALUES (
            'attempt_cross', 'workspace_b', 'job_b', 'item_a', 3,
            'claimed', 'lease_cross', 'schema-test',
            CURRENT_TIMESTAMP + INTERVAL '5 minutes', 'mock',
            'generate', 'provider-idem-cross'
        )
        """,
    )
    assert constraint == "fk_job_attempts_workspace_job_item"


def test_same_workspace_usage_allowed_and_cross_workspace_rejected(
    db: Engine,
) -> None:
    _seed_workspace_graph(db)
    usage_columns = """
        workspace_id, job_id, job_item_id, attempt_id, provider, model,
        operation, usage_reported, raw_usage, pricing_version, currency
    """
    constraint = _assert_constraint_rejects(
        db,
        f"""
        INSERT INTO usage_events ({usage_columns})
        VALUES (
            'workspace_b', 'job_b', 'item_b', 'attempt_a', 'mock',
            'mock-v1', 'generate', true, '{{}}', 'test-v1', 'USD'
        )
        """,
    )
    assert constraint == "fk_usage_events_workspace_job_item_attempt"
    with db.begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO usage_events ({usage_columns})
                VALUES (
                    'workspace_a', 'job_a', 'item_a', 'attempt_a', 'mock',
                    'mock-v1', 'generate', true, '{{}}', 'test-v1', 'USD'
                )
                """
            )
        )


@pytest.mark.parametrize(
    ("column_name", "constraint_name"),
    [
        ("root_job_id", "fk_jobs_workspace_root_job"),
        ("retry_of_job_id", "fk_jobs_workspace_retry_of_job"),
    ],
)
def test_same_workspace_job_lineage_allowed_and_cross_workspace_rejected(
    db: Engine,
    column_name: str,
    constraint_name: str,
) -> None:
    _seed_workspace_graph(db)
    with db.begin() as connection:
        connection.execute(
            text(f"UPDATE jobs SET {column_name} = 'job_a' WHERE id = 'job_a'")
        )
    constraint = _assert_constraint_rejects(
        db,
        f"UPDATE jobs SET {column_name} = 'job_a' WHERE id = 'job_b'",
    )
    assert constraint == constraint_name


def test_same_workspace_reservation_allowed_and_cross_workspace_rejected(
    db: Engine,
) -> None:
    _seed_workspace_graph(db)
    constraint = _assert_constraint_rejects(
        db,
        """
        INSERT INTO credit_reservations (
            id, workspace_id, job_id, reserved_credits,
            settled_credits, status
        ) VALUES (
            'reservation_cross', 'workspace_b', 'job_a', 10, 0, 'active'
        )
        """,
    )
    assert constraint == "fk_credit_reservations_workspace_job"
    with db.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO credit_reservations (
                    id, workspace_id, job_id, reserved_credits,
                    settled_credits, status
                ) VALUES (
                    'reservation_a', 'workspace_a', 'job_a', 10, 0, 'active'
                )
                """
            )
        )


def test_same_workspace_ledger_allowed_and_cross_workspace_rejected(
    db: Engine,
) -> None:
    _seed_workspace_graph(db)
    session_factory = sessionmaker(bind=db, expire_on_commit=False)
    with session_factory.begin() as session:
        session.add(
            CreditReservationRecord(
                id="reservation_a",
                workspace_id="workspace_a",
                job_id="job_a",
                reserved_credits=10,
            )
        )
    ledger_values = """
        reservation_id, job_id, entry_type, delta_balance_credits,
        delta_reserved_credits, balance_after_credits, reserved_after_credits
    """
    with db.begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO credit_ledger_entries (workspace_id, {ledger_values})
                VALUES (
                    'workspace_a', 'reservation_a', 'job_a', 'reserve',
                    0, 10, 100, 10
                )
                """
            )
        )
    constraint = _assert_constraint_rejects(
        db,
        f"""
        INSERT INTO credit_ledger_entries (workspace_id, {ledger_values})
        VALUES (
            'workspace_b', 'reservation_a', 'job_b', 'release',
            0, -10, 100, 0
        )
        """,
    )
    assert constraint == "fk_credit_ledger_workspace_reservation"


def _concurrent_insert_results(
    engine: Engine,
    statement: str,
) -> list[str]:
    barrier = threading.Barrier(2)

    def insert_once(_: int) -> str:
        try:
            with engine.begin() as connection:
                barrier.wait(timeout=5)
                connection.execute(text(statement))
            return "committed"
        except IntegrityError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(insert_once, range(2)))


def test_duplicate_usage_concurrent_insert_has_one_winner(db: Engine) -> None:
    _seed_workspace_graph(db)
    results = _concurrent_insert_results(
        db,
        """
        INSERT INTO usage_events (
            workspace_id, job_id, job_item_id, attempt_id, provider, model,
            operation, usage_reported, raw_usage, pricing_version, currency
        ) VALUES (
            'workspace_a', 'job_a', 'item_a', 'attempt_a', 'mock',
            'mock-v1', 'generate', true, '{}', 'test-v1', 'USD'
        )
        """,
    )
    assert sorted(results) == ["committed", "rejected"]
    with db.connect() as connection:
        count = connection.scalar(
            text("SELECT COUNT(*) FROM usage_events WHERE attempt_id = 'attempt_a'")
        )
    assert count == 1


def test_duplicate_settlement_concurrent_insert_has_one_winner(
    db: Engine,
) -> None:
    _seed_workspace_graph(db)
    with db.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO credit_reservations (
                    id, workspace_id, job_id, reserved_credits,
                    settled_credits, status
                ) VALUES (
                    'reservation_a', 'workspace_a', 'job_a', 10, 0, 'active'
                )
                """
            )
        )
    results = _concurrent_insert_results(
        db,
        """
        INSERT INTO credit_ledger_entries (
            workspace_id, reservation_id, job_id, entry_type,
            delta_balance_credits, delta_reserved_credits,
            balance_after_credits, reserved_after_credits
        ) VALUES (
            'workspace_a', 'reservation_a', 'job_a', 'settle',
            -10, -10, 90, 0
        )
        """,
    )
    assert sorted(results) == ["committed", "rejected"]
    with db.connect() as connection:
        count = connection.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM credit_ledger_entries
                WHERE reservation_id = 'reservation_a'
                  AND entry_type = 'settle'
                """
            )
        )
    assert count == 1


@pytest.mark.parametrize("attempt_no", [0, -1])
def test_nonpositive_attempt_number_rejected(
    db: Engine,
    attempt_no: int,
) -> None:
    _seed_workspace_graph(db)
    constraint = _assert_constraint_rejects(
        db,
        f"""
        INSERT INTO job_attempts (
            id, workspace_id, job_id, job_item_id, attempt_no, status,
            lease_token, leased_by, lease_expires_at, provider,
            operation, provider_idempotency_key
        ) VALUES (
            'attempt_invalid', 'workspace_a', 'job_a', 'item_a', {attempt_no},
            'claimed', 'lease-invalid', 'schema-test',
            CURRENT_TIMESTAMP + INTERVAL '5 minutes', 'mock',
            'generate', 'provider-idem-invalid'
        )
        """,
    )
    assert constraint == "ck_job_attempts_attempt_no_positive"


def test_amazon_suite_review_attempt_constraints_are_enforced(db: Engine) -> None:
    _seed_workspace_graph(db)
    with db.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO amazon_suite_review_attempts (
                    id, workspace_id, job_id, review_round,
                    output_asset_set_hash, provider,
                    provider_idempotency_key, lease_token, leased_by,
                    lease_expires_at
                ) VALUES (
                    'review_valid', 'workspace_a', 'job_a', 1,
                    'output-hash-valid', 'openai',
                    'review-idem-valid', 'review-lease-valid', 'schema-test',
                    CURRENT_TIMESTAMP + INTERVAL '5 minutes'
                )
                """
            )
        )

    cross_tenant_constraint = _assert_constraint_rejects(
        db,
        """
        INSERT INTO amazon_suite_review_attempts (
            id, workspace_id, job_id, review_round,
            output_asset_set_hash, provider,
            provider_idempotency_key, lease_token, leased_by,
            lease_expires_at
        ) VALUES (
            'review_cross_tenant', 'workspace_b', 'job_a', 2,
            'output-hash-cross-tenant', 'openai',
            'review-idem-cross-tenant', 'review-lease-cross', 'schema-test',
            CURRENT_TIMESTAMP + INTERVAL '5 minutes'
        )
        """,
    )
    assert cross_tenant_constraint == "fk_amazon_suite_review_workspace_job"

    round_constraint = _assert_constraint_rejects(
        db,
        """
        INSERT INTO amazon_suite_review_attempts (
            id, workspace_id, job_id, review_round,
            output_asset_set_hash, provider,
            provider_idempotency_key, lease_token, leased_by,
            lease_expires_at
        ) VALUES (
            'review_invalid_round', 'workspace_a', 'job_a', 0,
            'output-hash-invalid-round', 'openai',
            'review-idem-invalid-round', 'review-lease-round', 'schema-test',
            CURRENT_TIMESTAMP + INTERVAL '5 minutes'
        )
        """,
    )
    assert round_constraint == "ck_amazon_suite_review_round_positive"

    for column_name, constraint_name in [
        ("actual_cost_micros", "ck_amazon_suite_review_actual_cost_nonnegative"),
        (
            "estimated_cost_micros",
            "ck_amazon_suite_review_estimated_cost_nonnegative",
        ),
    ]:
        cost_constraint = _assert_constraint_rejects(
            db,
            f"""
            INSERT INTO amazon_suite_review_attempts (
                id, workspace_id, job_id, review_round,
                output_asset_set_hash, provider,
                provider_idempotency_key, lease_token, leased_by,
                lease_expires_at, {column_name}
            ) VALUES (
                'review_negative_{column_name}', 'workspace_a', 'job_a', 2,
                'output-hash-negative-{column_name}', 'openai',
                'review-idem-negative-{column_name}',
                'review-lease-negative-{column_name}', 'schema-test',
                CURRENT_TIMESTAMP + INTERVAL '5 minutes', -1
            )
            """,
        )
        assert cost_constraint == constraint_name


def test_amazon_suite_review_round_has_one_concurrent_winner(db: Engine) -> None:
    _seed_workspace_graph(db)
    barrier = threading.Barrier(2)
    insert_statement = text(
        """
        INSERT INTO amazon_suite_review_attempts (
            id, workspace_id, job_id, review_round,
            output_asset_set_hash, provider,
            provider_idempotency_key, lease_token, leased_by,
            lease_expires_at
        ) VALUES (
            :id, 'workspace_a', 'job_a', 1,
            'shared-output-hash', 'openai', :provider_idempotency_key,
            :lease_token, 'schema-test',
            CURRENT_TIMESTAMP + INTERVAL '5 minutes'
        )
        """
    )

    def insert_once(index: int) -> str:
        try:
            with db.begin() as connection:
                barrier.wait(timeout=5)
                connection.execute(
                    insert_statement,
                    {
                        "id": f"review_concurrent_{index}",
                        "provider_idempotency_key": f"review-idem-concurrent-{index}",
                        "lease_token": f"review-lease-concurrent-{index}",
                    },
                )
            return "committed"
        except IntegrityError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(insert_once, range(2)))

    assert sorted(results) == ["committed", "rejected"]
    with db.connect() as connection:
        count = connection.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM amazon_suite_review_attempts
                WHERE job_id = 'job_a'
                  AND review_round = 1
                  AND output_asset_set_hash = 'shared-output-hash'
                """
            )
        )
    assert count == 1


def test_amazon_suite_review_provider_idempotency_is_unique(db: Engine) -> None:
    _seed_workspace_graph(db)
    with db.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO amazon_suite_review_attempts (
                    id, workspace_id, job_id, review_round,
                    output_asset_set_hash, provider,
                    provider_idempotency_key, lease_token, leased_by,
                    lease_expires_at
                ) VALUES (
                    'review_idem_a', 'workspace_a', 'job_a', 1,
                    'output-hash-idem-a', 'openai', 'shared-review-idem',
                    'review-lease-idem-a', 'schema-test',
                    CURRENT_TIMESTAMP + INTERVAL '5 minutes'
                )
                """
            )
        )

    constraint = _assert_constraint_rejects(
        db,
        """
        INSERT INTO amazon_suite_review_attempts (
            id, workspace_id, job_id, review_round,
            output_asset_set_hash, provider,
            provider_idempotency_key, lease_token, leased_by,
            lease_expires_at
        ) VALUES (
            'review_idem_b', 'workspace_b', 'job_b', 1,
            'output-hash-idem-b', 'openai', 'shared-review-idem',
            'review-lease-idem-b', 'schema-test',
            CURRENT_TIMESTAMP + INTERVAL '5 minutes'
        )
        """,
    )
    assert constraint == "uq_amazon_suite_review_provider_idem"


def test_negative_api_request_count_rejected(db: Engine) -> None:
    _seed_workspace_graph(db)
    constraint = _assert_constraint_rejects(
        db,
        "UPDATE jobs SET api_request_count = -1 WHERE id = 'job_a'",
    )
    assert constraint == "ck_jobs_api_request_count_nonnegative"


@pytest.mark.parametrize(
    "mutation",
    [
        "status = 'completed', finished_at = NULL",
        (
            "status = 'completed', finished_at = CURRENT_TIMESTAMP, "
            "succeeded_item_count = 0"
        ),
        "status = 'partial', finished_at = CURRENT_TIMESTAMP",
        (
            "status = 'failed', finished_at = CURRENT_TIMESTAMP, "
            "succeeded_item_count = 1"
        ),
        "status = 'processing', finished_at = CURRENT_TIMESTAMP",
    ],
)
def test_inconsistent_terminal_job_state_rejected(
    db: Engine,
    mutation: str,
) -> None:
    _seed_workspace_graph(db)
    constraint = _assert_constraint_rejects(
        db,
        f"UPDATE jobs SET {mutation} WHERE id = 'job_a'",
    )
    assert constraint == "ck_jobs_terminal_consistency"


def test_valid_terminal_job_states_allowed(db: Engine) -> None:
    _seed_workspace_graph(db)
    with db.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE jobs
                SET status = 'completed',
                    succeeded_item_count = expected_item_count,
                    failed_item_count = 0,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = 'job_a'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE jobs
                SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP
                WHERE id = 'job_b'
                """
            )
        )


def test_empty_database_upgrade_downgrade_and_reupgrade() -> None:
    with _new_schema(_postgres_url(), "migration_empty") as schema:
        _migrate(schema.url, "head")
        _downgrade(schema.url, "20260725_0004")
        _migrate(schema.url, "head")
        with schema.engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        assert revision == "20260808_0006"


def test_0006_upgrade_downgrade_and_reupgrade() -> None:
    with _new_schema(_postgres_url(), "migration_0006") as schema:
        _migrate(schema.url, "20260727_0005")
        assert (
            "amazon_suite_review_attempts"
            not in inspect(schema.engine).get_table_names()
        )

        _migrate(schema.url, "head")
        assert (
            "amazon_suite_review_attempts" in inspect(schema.engine).get_table_names()
        )

        _downgrade(schema.url, "20260727_0005")
        assert (
            "amazon_suite_review_attempts"
            not in inspect(schema.engine).get_table_names()
        )

        _migrate(schema.url, "head")
        with schema.engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        assert revision == "20260808_0006"


def test_populated_0004_database_upgrades_and_round_trips() -> None:
    with _new_schema(_postgres_url(), "migration_0004") as schema:
        _migrate(schema.url, "20260725_0004")
        _seed_workspace_graph(schema.engine)
        _migrate(schema.url, "head")
        _downgrade(schema.url, "20260725_0004")
        _migrate(schema.url, "head")
        with schema.engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            job_count = connection.scalar(text("SELECT COUNT(*) FROM jobs"))
            attempt_count = connection.scalar(text("SELECT COUNT(*) FROM job_attempts"))
        assert revision == "20260808_0006"
        assert job_count == 2
        assert attempt_count == 2


def test_0004_upgrade_fails_auditably_on_constraint_violations() -> None:
    with _new_schema(_postgres_url(), "migration_dirty") as schema:
        _migrate(schema.url, "20260725_0004")
        _seed_workspace_graph(schema.engine)
        with schema.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO job_attempts (
                        id, workspace_id, job_id, job_item_id, attempt_no,
                        status, lease_token, leased_by, lease_expires_at,
                        provider, operation, provider_idempotency_key
                    ) VALUES (
                        'attempt_dirty', 'workspace_a', 'job_a', 'item_a', 0,
                        'claimed', 'lease-dirty', 'schema-test',
                        CURRENT_TIMESTAMP + INTERVAL '5 minutes', 'mock',
                        'generate', 'provider-idem-dirty'
                    )
                    """
                )
            )
        with pytest.raises(
            DBAPIError,
            match="0005 preflight failed: nonpositive attempt number",
        ):
            _migrate(schema.url, "head")
        with schema.engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            dirty_row_count = connection.scalar(
                text("SELECT COUNT(*) FROM job_attempts WHERE attempt_no = 0")
            )
        assert revision == "20260725_0004"
        assert dirty_row_count == 1


def _metadata_differences(connection: Connection) -> list[object]:
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "target_metadata": Base.metadata,
        },
    )
    return compare_metadata(context, Base.metadata)


def test_alembic_and_metadata_created_schemas_match_current_orm(
    hardened_schema: PostgresSchema,
) -> None:
    assert AmazonSuiteReviewAttemptRecord.__table__.name == (
        "amazon_suite_review_attempts"
    )
    with hardened_schema.engine.connect() as connection:
        migrated_differences = _metadata_differences(connection)
    assert migrated_differences == []

    with _new_schema(_postgres_url(), "metadata_create") as metadata_schema:
        Base.metadata.create_all(metadata_schema.engine)
        with metadata_schema.engine.connect() as connection:
            metadata_differences = _metadata_differences(connection)
    assert metadata_differences == []


def test_required_tenant_constraints_and_indexes_exist(
    hardened_schema: PostgresSchema,
) -> None:
    inspector = inspect(hardened_schema.engine)
    foreign_keys = {
        fk["name"]
        for table_name in [
            "jobs",
            "job_attempts",
            "amazon_suite_review_attempts",
            "usage_events",
            "credit_reservations",
            "credit_ledger_entries",
        ]
        for fk in inspector.get_foreign_keys(table_name)
    }
    assert {
        "fk_job_attempts_workspace_job_item",
        "fk_amazon_suite_review_workspace_job",
        "fk_usage_events_workspace_job_item_attempt",
        "fk_jobs_workspace_root_job",
        "fk_jobs_workspace_retry_of_job",
        "fk_credit_reservations_workspace_job",
        "fk_credit_ledger_workspace_reservation",
        "fk_credit_ledger_workspace_job",
    } <= foreign_keys
    outbox_indexes = {index["name"] for index in inspector.get_indexes("outbox_events")}
    assert {
        "ix_outbox_events_claim",
        "ix_outbox_events_reclaim",
    } <= outbox_indexes

    review_indexes = {
        index["name"] for index in inspector.get_indexes("amazon_suite_review_attempts")
    }
    assert {
        "ix_amazon_suite_review_job_status_lease",
        "ix_amazon_suite_review_provider_request",
    } <= review_indexes
    review_unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "amazon_suite_review_attempts"
        )
    }
    assert {
        "uq_amazon_suite_review_job_round_output",
        "uq_amazon_suite_review_provider_idem",
    } <= review_unique_constraints


def _seed_outbox_for_plan(engine: Engine) -> None:
    _seed_workspace_graph(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO outbox_events (
                    id, workspace_id, aggregate_type, aggregate_id, event_type,
                    dedupe_key, payload, status, attempt_count, available_at,
                    locked_at, created_at
                )
                SELECT
                    'outbox_' || value,
                    'workspace_a',
                    'job',
                    'job_a',
                    'job.ready',
                    'dedupe_' || value,
                    '{}'::jsonb,
                    CASE
                        WHEN value % 5 = 0 THEN 'publishing'
                        WHEN value % 2 = 0 THEN 'failed'
                        ELSE 'pending'
                    END,
                    0,
                    CURRENT_TIMESTAMP - INTERVAL '1 minute',
                    CASE
                        WHEN value % 5 = 0
                        THEN CURRENT_TIMESTAMP - INTERVAL '1 hour'
                    END,
                    CURRENT_TIMESTAMP - (value || ' milliseconds')::interval
                FROM generate_series(1, 10000) AS value
                """
            )
        )
        connection.execute(text("ANALYZE outbox_events"))


def test_outbox_claim_explain_uses_claim_indexes(db: Engine) -> None:
    _seed_outbox_for_plan(db)
    with db.connect() as connection:
        connection.execute(text("SET enable_seqscan = off"))
        rows = connection.execute(
            text(
                """
                EXPLAIN
                SELECT id
                FROM outbox_events
                WHERE (
                    (
                        status IN ('pending', 'failed')
                        AND available_at <= CURRENT_TIMESTAMP
                    )
                    OR (
                        status = 'publishing'
                        AND locked_at IS NOT NULL
                        AND locked_at < CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                    )
                )
                ORDER BY created_at
                LIMIT 100
                FOR UPDATE SKIP LOCKED
                """
            )
        )
        plan = "\n".join(row[0] for row in rows)
    print(f"OUTBOX_CLAIM_EXPLAIN\n{plan}")
    assert "ix_outbox_events_claim" in plan, plan
    assert "ix_outbox_events_reclaim" in plan, plan


def test_outbox_concurrent_claims_skip_locked_rows(db: Engine) -> None:
    _seed_outbox_for_plan(db)
    claim = text(
        """
        SELECT id
        FROM outbox_events
        WHERE (
            (
                status IN ('pending', 'failed')
                AND available_at <= CURRENT_TIMESTAMP
            )
            OR (
                status = 'publishing'
                AND locked_at IS NOT NULL
                AND locked_at < CURRENT_TIMESTAMP - INTERVAL '5 minutes'
            )
        )
        ORDER BY created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
        """
    )
    first_connection = db.connect()
    second_connection = db.connect()
    first_transaction = first_connection.begin()
    second_transaction = second_connection.begin()
    try:
        first_id = first_connection.scalar(claim)
        second_id = second_connection.scalar(claim)
        assert first_id is not None
        assert second_id is not None
        assert first_id != second_id
    finally:
        second_transaction.rollback()
        first_transaction.rollback()
        second_connection.close()
        first_connection.close()
