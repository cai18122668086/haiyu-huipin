"""Durable server-side payment reconciliation independent of the browser."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_0034"
down_revision = "20261028_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("credit_purchases") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(16),
            type_=sa.String(24),
            existing_nullable=False,
        )
        for name in ("reconcile_next_at", "reconcile_lease_until", "reconcile_last_at"):
            batch.add_column(sa.Column(name, sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("reconcile_lease_token", sa.String(32)))
        batch.add_column(
            sa.Column(
                "reconcile_attempts", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch.add_column(sa.Column("reconcile_last_status", sa.String(40)))
        batch.add_column(sa.Column("reconcile_last_error", sa.String(80)))
        batch.drop_constraint("ck_credit_purchase_status", type_="check")
        batch.create_check_constraint(
            "ck_credit_purchase_status",
            "status IN ('pending', 'paid', 'partially_refunded', 'refunded', 'closed')",
        )
        batch.create_index(
            "ix_credit_purchase_reconcile_due", ["status", "reconcile_next_at"]
        )
    op.execute(
        sa.text(
            "UPDATE credit_purchases SET reconcile_next_at = CURRENT_TIMESTAMP "
            "WHERE status = 'pending'"
        )
    )
    op.create_table(
        "credit_payment_reconciliation",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(80)),
    )


def downgrade() -> None:
    raise RuntimeError("Retain payment reconciliation state; use a forward migration.")
