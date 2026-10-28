"""Add durable Aurora retry scheduling fields."""

import sqlalchemy as sa
from alembic import op

revision = "20260820_0011"
down_revision = "20260820_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_attempt_provider_idem", "job_attempts", type_="unique")
    op.create_index(
        "ix_attempt_provider_idem",
        "job_attempts",
        ["provider", "provider_idempotency_key"],
    )
    op.add_column(
        "job_items",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_job_items_retry_schedule", "job_items", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_job_items_retry_schedule", table_name="job_items")
    op.drop_column("job_items", "next_attempt_at")
    op.drop_index("ix_attempt_provider_idem", table_name="job_attempts")
    op.create_unique_constraint(
        "uq_attempt_provider_idem",
        "job_attempts",
        ["provider", "provider_idempotency_key"],
    )
