"""Finalize tenant-safe constraints and production indexes.

Revision ID: 20260725_0004
Revises: 20260725_0003
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0004"
down_revision: str | None = "20260725_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "assets",
        "workspace_id",
        existing_type=sa.String(64),
        type_=sa.String(40),
        nullable=False,
    )
    op.alter_column(
        "jobs",
        "workspace_id",
        existing_type=sa.String(64),
        type_=sa.String(40),
        nullable=False,
    )
    op.alter_column(
        "assets",
        "byte_size",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        nullable=False,
    )
    op.alter_column("assets", "width", existing_type=sa.Integer(), nullable=True)
    op.alter_column("assets", "height", existing_type=sa.Integer(), nullable=True)

    op.create_foreign_key(
        "fk_assets_workspace",
        "assets",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_jobs_workspace",
        "jobs",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_job_items_workspace_job",
        "job_items",
        "jobs",
        ["workspace_id", "job_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_assets_workspace_job",
        "job_assets",
        "jobs",
        ["workspace_id", "job_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_assets_workspace_asset",
        "job_assets",
        "assets",
        ["workspace_id", "asset_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_job_assets_workspace_job_item",
        "job_assets",
        "job_items",
        ["workspace_id", "job_id", "job_item_id"],
        ["workspace_id", "job_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_plans_workspace_job",
        "job_plans",
        "jobs",
        ["workspace_id", "job_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_job_attempts_workspace_job",
        "job_attempts",
        "jobs",
        ["workspace_id", "job_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_job_assets_role_ordinal
        ON job_assets (job_id, job_item_id, direction, role, ordinal)
        NULLS NOT DISTINCT
        """
    )
    op.create_index("ix_jobs_root_job_id", "jobs", ["root_job_id"])
    op.create_index(
        "ix_jobs_workspace_created",
        "jobs",
        ["workspace_id", "created_at", "id"],
    )
    op.create_index(
        "ix_jobs_workspace_type_status_created",
        "jobs",
        ["workspace_id", "type", "status", "created_at", "id"],
    )
    op.create_index("ix_job_plans_job_id", "job_plans", ["job_id"])
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])
    op.create_index("ix_job_attempts_job_item_id", "job_attempts", ["job_item_id"])
    op.create_index("ix_usage_events_workspace_id", "usage_events", ["workspace_id"])
    op.create_index("ix_usage_events_job_id", "usage_events", ["job_id"])
    op.create_index(
        "ix_credit_ledger_entries_workspace_id",
        "credit_ledger_entries",
        ["workspace_id"],
    )
    op.create_index("ix_audit_events_workspace_id", "audit_events", ["workspace_id"])

    op.create_check_constraint(
        "ck_jobs_expected_count_nonnegative",
        "jobs",
        "expected_item_count >= 0",
    )
    op.create_check_constraint(
        "ck_jobs_actual_cost_nonnegative",
        "jobs",
        "actual_cost_micros IS NULL OR actual_cost_micros >= 0",
    )
    op.create_check_constraint(
        "ck_usage_estimated_cost",
        "usage_events",
        "estimated_cost_micros IS NULL OR estimated_cost_micros >= 0",
    )
    op.create_check_constraint(
        "ck_usage_actual_cost",
        "usage_events",
        "actual_cost_micros IS NULL OR actual_cost_micros >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_usage_actual_cost", "usage_events", type_="check")
    op.drop_constraint("ck_usage_estimated_cost", "usage_events", type_="check")
    op.drop_constraint("ck_jobs_actual_cost_nonnegative", "jobs", type_="check")
    op.drop_constraint("ck_jobs_expected_count_nonnegative", "jobs", type_="check")
    op.drop_index("ix_audit_events_workspace_id", table_name="audit_events")
    op.drop_index(
        "ix_credit_ledger_entries_workspace_id",
        table_name="credit_ledger_entries",
    )
    op.drop_index("ix_usage_events_job_id", table_name="usage_events")
    op.drop_index("ix_usage_events_workspace_id", table_name="usage_events")
    op.drop_index("ix_job_attempts_job_item_id", table_name="job_attempts")
    op.drop_index("ix_job_attempts_job_id", table_name="job_attempts")
    op.drop_index("ix_job_plans_job_id", table_name="job_plans")
    op.drop_index("ix_jobs_workspace_type_status_created", table_name="jobs")
    op.drop_index("ix_jobs_workspace_created", table_name="jobs")
    op.drop_index("ix_jobs_root_job_id", table_name="jobs")
    op.drop_index("uq_job_assets_role_ordinal", table_name="job_assets")
    for table, name in [
        ("job_attempts", "fk_job_attempts_workspace_job"),
        ("job_plans", "fk_job_plans_workspace_job"),
        ("job_assets", "fk_job_assets_workspace_job_item"),
        ("job_assets", "fk_job_assets_workspace_asset"),
        ("job_assets", "fk_job_assets_workspace_job"),
        ("job_items", "fk_job_items_workspace_job"),
        ("jobs", "fk_jobs_workspace"),
        ("assets", "fk_assets_workspace"),
    ]:
        op.drop_constraint(name, table, type_="foreignkey")
    op.alter_column(
        "jobs",
        "workspace_id",
        existing_type=sa.String(40),
        type_=sa.String(64),
        nullable=False,
    )
    op.alter_column(
        "assets",
        "workspace_id",
        existing_type=sa.String(40),
        type_=sa.String(64),
        nullable=False,
    )
