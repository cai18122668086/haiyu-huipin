"""Add 30-day user content retention metadata.

Revision ID: 20260817_0009
Revises: 20260814_0008
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0009"
down_revision: str | None = "20260814_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column(
            "retention_class",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'user_30d'"),
        ),
    )
    op.add_column("assets", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "assets",
        sa.Column(
            "purge_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "assets", sa.Column("purge_next_retry_at", sa.DateTime(timezone=True))
    )
    op.add_column("assets", sa.Column("purge_last_error", sa.String(120)))
    op.add_column("assets", sa.Column("purged_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("history_expires_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("history_deleted_at", sa.DateTime(timezone=True)))

    op.execute(
        """
        UPDATE assets
        SET expires_at = created_at + INTERVAL '30 days'
        WHERE expires_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE assets
        SET retention_class = 'system_persistent', expires_at = NULL
        WHERE id IN (
            SELECT image_asset_id
            FROM announcements
            WHERE image_asset_id IS NOT NULL
        )
        """
    )
    op.execute(
        """
        UPDATE jobs
        SET history_expires_at = CASE
            WHEN status IN ('partial', 'completed', 'failed', 'cancelled')
                THEN COALESCE(finished_at, created_at) + INTERVAL '30 days'
            WHEN status = 'draft'
                THEN created_at + INTERVAL '30 days'
            ELSE NULL
        END
        """
    )

    op.create_index(
        "ix_assets_retention_due",
        "assets",
        ["retention_class", "expires_at"],
    )
    op.create_index(
        "ix_jobs_history_due",
        "jobs",
        ["history_expires_at", "history_deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_history_due", table_name="jobs")
    op.drop_index("ix_assets_retention_due", table_name="assets")
    op.drop_column("jobs", "history_deleted_at")
    op.drop_column("jobs", "history_expires_at")
    op.drop_column("assets", "purged_at")
    op.drop_column("assets", "purge_last_error")
    op.drop_column("assets", "purge_next_retry_at")
    op.drop_column("assets", "purge_attempts")
    op.drop_column("assets", "expires_at")
    op.drop_column("assets", "retention_class")
