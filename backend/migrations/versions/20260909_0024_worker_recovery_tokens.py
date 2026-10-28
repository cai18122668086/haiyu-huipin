"""Add durable recovery credentials for automatic Worker re-registration."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_0024"
down_revision = "20260904_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ecommerce_workers",
        sa.Column("recovery_token_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ecommerce_workers",
        sa.Column(
            "recovery_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_ecommerce_workers_recovery_token_hash",
        "ecommerce_workers",
        ["recovery_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_ecommerce_workers_recovery_token_hash",
        "ecommerce_workers",
    )
    op.drop_column("ecommerce_workers", "recovery_token_expires_at")
    op.drop_column("ecommerce_workers", "recovery_token_hash")
