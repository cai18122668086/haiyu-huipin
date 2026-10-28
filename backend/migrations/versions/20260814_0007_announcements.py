"""Add site-wide announcements and administrator publishing metadata.

Revision ID: 20260814_0007
Revises: 20260808_0006
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0007"
down_revision: str | None = "20260808_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("summary", sa.String(320)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.String(16),
            server_default=sa.text("'info'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("link_url", sa.String(2048)),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column(
            "is_pinned",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by_user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_announcements_status",
        ),
        sa.CheckConstraint(
            "kind IN ('info', 'success', 'warning', 'maintenance')",
            name="ck_announcements_kind",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="ck_announcements_valid_window",
        ),
    )
    op.create_index(
        "ix_announcements_public_order",
        "announcements",
        ["status", "is_pinned", "published_at"],
    )
    op.create_index(
        "ix_announcements_schedule",
        "announcements",
        ["status", "starts_at", "ends_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_announcements_schedule", table_name="announcements")
    op.drop_index("ix_announcements_public_order", table_name="announcements")
    op.drop_table("announcements")
