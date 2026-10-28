"""Create assets and jobs tables.

Revision ID: 20260725_0001
Revises:
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_assets_created_at", "assets", ["created_at"])
    op.create_index("ix_assets_origin", "assets", ["origin"])
    op.create_index("ix_assets_workspace_id", "assets", ["workspace_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("input_asset_ids", sa.JSON(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("raw_outputs", sa.JSON(), nullable=False),
        sa.Column("delivery_outputs", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("api_request_count", sa.Integer(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("actual_cost", sa.Float(), nullable=True),
        sa.Column("queue_task_id", sa.String(length=255), nullable=True),
        sa.Column("retry_of_job_id", sa.String(length=40), nullable=True),
        sa.Column("parent_job_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["retry_of_job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index("ix_jobs_parent_job_id", "jobs", ["parent_job_id"])
    op.create_index("ix_jobs_queue_task_id", "jobs", ["queue_task_id"])
    op.create_index("ix_jobs_retry_of_job_id", "jobs", ["retry_of_job_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_type", "jobs", ["type"])
    op.create_index("ix_jobs_workspace_id", "jobs", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_workspace_id", table_name="jobs")
    op.drop_index("ix_jobs_type", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_retry_of_job_id", table_name="jobs")
    op.drop_index("ix_jobs_queue_task_id", table_name="jobs")
    op.drop_index("ix_jobs_parent_job_id", table_name="jobs")
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_assets_workspace_id", table_name="assets")
    op.drop_index("ix_assets_origin", table_name="assets")
    op.drop_index("ix_assets_created_at", table_name="assets")
    op.drop_table("assets")
