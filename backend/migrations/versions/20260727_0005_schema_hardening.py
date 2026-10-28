"""Harden tenant relations, accounting uniqueness, and queue claims.

Revision ID: 20260727_0005
Revises: 20260725_0004
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0005"
down_revision: str | None = "20260725_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fail_if_exists(label: str, query: str) -> None:
    """Abort instead of silently rewriting data that violates a new invariant."""
    op.execute(
        sa.text(
            f"""
            DO $migration$
            BEGIN
                IF EXISTS ({query}) THEN
                    RAISE EXCEPTION
                        '0005 preflight failed: {label}';
                END IF;
            END
            $migration$;
            """
        )
    )


def _run_preflight() -> None:
    checks = {
        "asset compatibility columns diverge from canonical columns": """
            SELECT 1
            FROM assets
            WHERE (filename IS NOT NULL AND filename <> original_filename)
               OR (storage_key IS NOT NULL AND storage_key <> object_key)
               OR (
                    origin IS NOT NULL
                    AND (
                        (origin = 'upload' AND origin_kind <> 'upload')
                        OR (origin <> 'upload' AND origin_kind = 'upload')
                    )
               )
        """,
        "job compatibility columns contain unrepresented data": """
            SELECT 1
            FROM jobs
            WHERE queue_task_id IS NOT NULL
               OR raw_outputs::jsonb <> '[]'::jsonb
               OR delivery_outputs::jsonb <> '[]'::jsonb
               OR usage::jsonb <> '{}'::jsonb
               OR (
                    parent_job_id IS NOT NULL
                    AND root_job_id IS DISTINCT FROM parent_job_id
               )
               OR (
                    estimated_cost IS NOT NULL
                    AND (
                        estimated_cost_min_micros IS DISTINCT FROM
                            ROUND(estimated_cost * 1000000)::bigint
                        OR estimated_cost_max_micros IS DISTINCT FROM
                            ROUND(estimated_cost * 1000000)::bigint
                    )
               )
               OR (
                    actual_cost IS NOT NULL
                    AND actual_cost_micros IS DISTINCT FROM
                        ROUND(actual_cost * 1000000)::bigint
               )
               OR EXISTS (
                    SELECT 1
                    FROM json_array_elements_text(input_asset_ids)
                        AS legacy_asset(asset_id)
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM job_assets
                        WHERE job_assets.job_id = jobs.id
                          AND job_assets.workspace_id = jobs.workspace_id
                          AND job_assets.asset_id = legacy_asset.asset_id
                          AND job_assets.direction = 'input'
                    )
               )
        """,
        "cross-workspace job_attempt relation": """
            SELECT 1
            FROM job_attempts AS attempt
            LEFT JOIN job_items AS item
              ON item.id = attempt.job_item_id
             AND item.job_id = attempt.job_id
             AND item.workspace_id = attempt.workspace_id
            WHERE item.id IS NULL
        """,
        "cross-workspace usage_event relation": """
            SELECT 1
            FROM usage_events AS usage
            LEFT JOIN job_attempts AS attempt
              ON attempt.id = usage.attempt_id
             AND attempt.job_item_id = usage.job_item_id
             AND attempt.job_id = usage.job_id
             AND attempt.workspace_id = usage.workspace_id
            WHERE attempt.id IS NULL
        """,
        "cross-workspace root_job relation": """
            SELECT 1
            FROM jobs AS child
            LEFT JOIN jobs AS root
              ON root.id = child.root_job_id
             AND root.workspace_id = child.workspace_id
            WHERE child.root_job_id IS NOT NULL
              AND root.id IS NULL
        """,
        "cross-workspace retry_of_job relation": """
            SELECT 1
            FROM jobs AS child
            LEFT JOIN jobs AS retried
              ON retried.id = child.retry_of_job_id
             AND retried.workspace_id = child.workspace_id
            WHERE child.retry_of_job_id IS NOT NULL
              AND retried.id IS NULL
        """,
        "cross-workspace credit reservation relation": """
            SELECT 1
            FROM credit_reservations AS reservation
            LEFT JOIN jobs
              ON jobs.id = reservation.job_id
             AND jobs.workspace_id = reservation.workspace_id
            WHERE jobs.id IS NULL
        """,
        "cross-workspace credit ledger relation": """
            SELECT 1
            FROM credit_ledger_entries AS ledger
            LEFT JOIN credit_reservations AS reservation
              ON reservation.id = ledger.reservation_id
             AND reservation.workspace_id = ledger.workspace_id
            LEFT JOIN jobs
              ON jobs.id = ledger.job_id
             AND jobs.workspace_id = ledger.workspace_id
            WHERE (ledger.reservation_id IS NOT NULL AND reservation.id IS NULL)
               OR (ledger.job_id IS NOT NULL AND jobs.id IS NULL)
        """,
        "duplicate usage for one attempt": """
            SELECT 1
            FROM usage_events
            GROUP BY attempt_id
            HAVING COUNT(*) > 1
        """,
        "duplicate settlement for one reservation": """
            SELECT 1
            FROM credit_ledger_entries
            WHERE entry_type = 'settle'
              AND reservation_id IS NOT NULL
            GROUP BY reservation_id
            HAVING COUNT(*) > 1
        """,
        "nonpositive attempt number": """
            SELECT 1 FROM job_attempts WHERE attempt_no <= 0
        """,
        "negative API request count": """
            SELECT 1 FROM jobs WHERE api_request_count < 0
        """,
        "inconsistent terminal job state": """
            SELECT 1
            FROM jobs
            WHERE NOT (
                (
                    status IN ('partial', 'completed', 'failed', 'cancelled')
                    AND finished_at IS NOT NULL
                    AND (
                        (
                            status = 'completed'
                            AND succeeded_item_count = expected_item_count
                            AND failed_item_count = 0
                        )
                        OR (
                            status = 'partial'
                            AND succeeded_item_count > 0
                            AND succeeded_item_count < expected_item_count
                        )
                        OR (status = 'failed' AND succeeded_item_count = 0)
                        OR status = 'cancelled'
                    )
                )
                OR (
                    status NOT IN ('partial', 'completed', 'failed', 'cancelled')
                    AND finished_at IS NULL
                )
            )
        """,
    }
    for label, query in checks.items():
        _fail_if_exists(label, query)


def _drop_compatibility_schema() -> None:
    for index_name in [
        "ix_assets_created_at",
        "ix_assets_origin",
        "ix_assets_workspace_id",
    ]:
        op.drop_index(index_name, table_name="assets")
    op.drop_constraint("assets_storage_key_key", "assets", type_="unique")
    for column_name in ["filename", "storage_key", "origin"]:
        op.drop_column("assets", column_name)

    for index_name in [
        "ix_jobs_created_at",
        "ix_jobs_parent_job_id",
        "ix_jobs_queue_task_id",
        "ix_jobs_status",
        "ix_jobs_type",
        "ix_jobs_workspace_id",
    ]:
        op.drop_index(index_name, table_name="jobs")
    for column_name in [
        "input_asset_ids",
        "raw_outputs",
        "delivery_outputs",
        "usage",
        "estimated_cost",
        "actual_cost",
        "queue_task_id",
        "parent_job_id",
    ]:
        op.drop_column("jobs", column_name)


def upgrade() -> None:
    _run_preflight()

    # End the one-release compatibility window. Preflight above guarantees that
    # no authoritative information exists only in these legacy columns.
    _drop_compatibility_schema()

    for table, constraint_name in [
        ("job_items", "job_items_job_id_fkey"),
        ("job_assets", "job_assets_job_id_fkey"),
        ("job_assets", "job_assets_asset_id_fkey"),
        ("job_plans", "job_plans_job_id_fkey"),
        ("job_attempts", "job_attempts_job_id_fkey"),
    ]:
        op.drop_constraint(constraint_name, table, type_="foreignkey")

    op.alter_column(
        "jobs",
        "configuration",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(),
        postgresql_using="configuration::jsonb",
        nullable=False,
    )
    op.alter_column(
        "assets",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    for column_name in ["created_at", "updated_at"]:
        op.alter_column(
            "jobs",
            column_name,
            existing_type=sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        )

    op.create_unique_constraint(
        "uq_job_attempts_workspace_job_item_id",
        "job_attempts",
        ["workspace_id", "job_id", "job_item_id", "id"],
    )
    op.create_unique_constraint(
        "uq_usage_events_attempt_id",
        "usage_events",
        ["attempt_id"],
    )
    op.create_unique_constraint(
        "uq_credit_reservations_workspace_id",
        "credit_reservations",
        ["workspace_id", "id"],
    )

    op.create_foreign_key(
        "fk_job_attempts_workspace_job_item",
        "job_attempts",
        "job_items",
        ["workspace_id", "job_id", "job_item_id"],
        ["workspace_id", "job_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_usage_events_workspace_job_item_attempt",
        "usage_events",
        "job_attempts",
        ["workspace_id", "job_id", "job_item_id", "attempt_id"],
        ["workspace_id", "job_id", "job_item_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_jobs_workspace_root_job",
        "jobs",
        "jobs",
        ["workspace_id", "root_job_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_jobs_workspace_retry_of_job",
        "jobs",
        "jobs",
        ["workspace_id", "retry_of_job_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_credit_reservations_workspace_job",
        "credit_reservations",
        "jobs",
        ["workspace_id", "job_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_credit_ledger_workspace_reservation",
        "credit_ledger_entries",
        "credit_reservations",
        ["workspace_id", "reservation_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_credit_ledger_workspace_job",
        "credit_ledger_entries",
        "jobs",
        ["workspace_id", "job_id"],
        ["workspace_id", "id"],
    )

    op.create_check_constraint(
        "ck_job_attempts_attempt_no_positive",
        "job_attempts",
        "attempt_no > 0",
    )
    op.create_check_constraint(
        "ck_jobs_api_request_count_nonnegative",
        "jobs",
        "api_request_count >= 0",
    )
    op.create_check_constraint(
        "ck_jobs_terminal_consistency",
        "jobs",
        """
        (
            status IN ('partial', 'completed', 'failed', 'cancelled')
            AND finished_at IS NOT NULL
            AND (
                (
                    status = 'completed'
                    AND succeeded_item_count = expected_item_count
                    AND failed_item_count = 0
                )
                OR (
                    status = 'partial'
                    AND succeeded_item_count > 0
                    AND succeeded_item_count < expected_item_count
                )
                OR (status = 'failed' AND succeeded_item_count = 0)
                OR status = 'cancelled'
            )
        )
        OR (
            status NOT IN ('partial', 'completed', 'failed', 'cancelled')
            AND finished_at IS NULL
        )
        """,
    )

    op.create_index(
        "uq_credit_ledger_settlement_reservation",
        "credit_ledger_entries",
        ["reservation_id"],
        unique=True,
        postgresql_where=sa.text(
            "entry_type = 'settle' AND reservation_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_outbox_events_claim",
        "outbox_events",
        ["available_at", "created_at"],
        postgresql_where=sa.text("status IN ('pending', 'failed')"),
    )
    op.create_index(
        "ix_outbox_events_reclaim",
        "outbox_events",
        ["locked_at", "created_at"],
        postgresql_where=sa.text(
            "status = 'publishing' AND locked_at IS NOT NULL"
        ),
    )
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")


def _restore_compatibility_schema() -> None:
    op.add_column("assets", sa.Column("filename", sa.String(255), nullable=True))
    op.add_column("assets", sa.Column("storage_key", sa.String(512), nullable=True))
    op.add_column("assets", sa.Column("origin", sa.String(32), nullable=True))
    op.execute(
        """
        UPDATE assets
        SET filename = original_filename,
            storage_key = object_key,
            origin = origin_kind
        """
    )
    op.create_unique_constraint(
        "assets_storage_key_key", "assets", ["storage_key"]
    )
    op.create_index("ix_assets_created_at", "assets", ["created_at"])
    op.create_index("ix_assets_origin", "assets", ["origin"])
    op.create_index("ix_assets_workspace_id", "assets", ["workspace_id"])

    op.add_column(
        "jobs",
        sa.Column(
            "input_asset_ids",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "raw_outputs",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "delivery_outputs",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "usage",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )
    op.add_column("jobs", sa.Column("estimated_cost", sa.Float(), nullable=True))
    op.add_column("jobs", sa.Column("actual_cost", sa.Float(), nullable=True))
    op.add_column("jobs", sa.Column("queue_task_id", sa.String(255), nullable=True))
    op.add_column("jobs", sa.Column("parent_job_id", sa.String(40), nullable=True))
    op.execute(
        """
        UPDATE jobs
        SET input_asset_ids = COALESCE(
                (
                    SELECT json_agg(job_assets.asset_id ORDER BY job_assets.ordinal)
                    FROM job_assets
                    WHERE job_assets.job_id = jobs.id
                      AND job_assets.workspace_id = jobs.workspace_id
                      AND job_assets.direction = 'input'
                      AND job_assets.job_item_id IS NULL
                ),
                '[]'::json
            ),
            estimated_cost = CASE
                WHEN estimated_cost_min_micros = estimated_cost_max_micros
                THEN estimated_cost_min_micros / 1000000.0
            END,
            actual_cost = actual_cost_micros / 1000000.0,
            parent_job_id = root_job_id
        """
    )
    op.create_foreign_key(
        "jobs_parent_job_id_fkey",
        "jobs",
        "jobs",
        ["parent_job_id"],
        ["id"],
    )
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index("ix_jobs_parent_job_id", "jobs", ["parent_job_id"])
    op.create_index("ix_jobs_queue_task_id", "jobs", ["queue_task_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_type", "jobs", ["type"])
    op.create_index("ix_jobs_workspace_id", "jobs", ["workspace_id"])


def downgrade() -> None:
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.drop_index("ix_outbox_events_reclaim", table_name="outbox_events")
    op.drop_index("ix_outbox_events_claim", table_name="outbox_events")
    op.drop_index(
        "uq_credit_ledger_settlement_reservation",
        table_name="credit_ledger_entries",
    )

    op.drop_constraint(
        "ck_jobs_terminal_consistency", "jobs", type_="check"
    )
    op.drop_constraint(
        "ck_jobs_api_request_count_nonnegative", "jobs", type_="check"
    )
    op.drop_constraint(
        "ck_job_attempts_attempt_no_positive",
        "job_attempts",
        type_="check",
    )

    for table, constraint_name in [
        ("credit_ledger_entries", "fk_credit_ledger_workspace_job"),
        ("credit_ledger_entries", "fk_credit_ledger_workspace_reservation"),
        ("credit_reservations", "fk_credit_reservations_workspace_job"),
        ("jobs", "fk_jobs_workspace_retry_of_job"),
        ("jobs", "fk_jobs_workspace_root_job"),
        ("usage_events", "fk_usage_events_workspace_job_item_attempt"),
        ("job_attempts", "fk_job_attempts_workspace_job_item"),
    ]:
        op.drop_constraint(constraint_name, table, type_="foreignkey")

    op.drop_constraint(
        "uq_credit_reservations_workspace_id",
        "credit_reservations",
        type_="unique",
    )
    op.drop_constraint(
        "uq_usage_events_attempt_id", "usage_events", type_="unique"
    )
    op.drop_constraint(
        "uq_job_attempts_workspace_job_item_id",
        "job_attempts",
        type_="unique",
    )

    for table, constraint_name, referred_table, local_columns, ondelete in [
        ("job_items", "job_items_job_id_fkey", "jobs", ["job_id"], "CASCADE"),
        ("job_assets", "job_assets_job_id_fkey", "jobs", ["job_id"], "CASCADE"),
        (
            "job_assets",
            "job_assets_asset_id_fkey",
            "assets",
            ["asset_id"],
            "RESTRICT",
        ),
        ("job_plans", "job_plans_job_id_fkey", "jobs", ["job_id"], "CASCADE"),
        (
            "job_attempts",
            "job_attempts_job_id_fkey",
            "jobs",
            ["job_id"],
            "CASCADE",
        ),
    ]:
        op.create_foreign_key(
            constraint_name,
            table,
            referred_table,
            local_columns,
            ["id"],
            ondelete=ondelete,
        )

    op.alter_column(
        "jobs",
        "configuration",
        existing_type=postgresql.JSONB(),
        type_=sa.JSON(),
        postgresql_using="configuration::json",
        nullable=False,
    )
    op.alter_column(
        "assets",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=None,
        nullable=False,
    )
    for column_name in ["created_at", "updated_at"]:
        op.alter_column(
            "jobs",
            column_name,
            existing_type=sa.DateTime(timezone=True),
            server_default=None,
            nullable=False,
        )

    _restore_compatibility_schema()
