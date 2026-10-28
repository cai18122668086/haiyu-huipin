"""Add fencing tokens to ecommerce profile leases."""

import sqlalchemy as sa
from alembic import op

revision = "20260901_0015"
down_revision = "20260901_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crawl_profile_leases",
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("crawl_profile_leases", "fencing_token")
