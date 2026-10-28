"""Add optional public image metadata to announcements.

Revision ID: 20260814_0008
Revises: 20260814_0007
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_0008"
down_revision: str | None = "20260814_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "announcements",
        sa.Column(
            "image_asset_id",
            sa.String(40),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("announcements", sa.Column("image_alt", sa.String(240)))


def downgrade() -> None:
    op.drop_column("announcements", "image_alt")
    op.drop_column("announcements", "image_asset_id")
