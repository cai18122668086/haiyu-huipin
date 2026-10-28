"""Add the crawl result snapshot reference used by task recovery."""

import sqlalchemy as sa
from alembic import op

revision = "20260902_0019"
down_revision = "20260902_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crawl_tasks",
        sa.Column("result_snapshot_id", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("crawl_tasks", "result_snapshot_id")
