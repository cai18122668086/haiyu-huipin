"""Durable original-route refunds and reversible entitlement cancellation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261028_0032"
down_revision = "20261028_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("credit_purchases") as batch:
        batch.add_column(
            sa.Column(
                "refunded_amount_fen",
                sa.BigInteger(),
                server_default="0",
                nullable=False,
            )
        )
        batch.drop_constraint("ck_credit_purchase_status", type_="check")
        batch.create_check_constraint(
            "ck_credit_purchase_status",
            "status IN ('pending', 'paid', 'partially_refunded', 'refunded')",
        )
    with op.batch_alter_table("credit_memberships") as batch:
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("cancellation_reason", sa.String(200)))
    op.create_table(
        "credit_refunds",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "purchase_id",
            sa.String(40),
            sa.ForeignKey("credit_purchases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("amount_fen", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(160), nullable=False),
        sa.Column("policy", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="requested"),
        sa.Column(
            "entitlement_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("provider_refund_id", sa.String(128)),
        sa.Column(
            "ledger_entry_id",
            sa.BigInteger(),
            sa.ForeignKey("credit_ledger_entries.id", ondelete="RESTRICT"),
        ),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_credit_refund_request",
        ),
        sa.CheckConstraint("amount_fen > 0", name="ck_credit_refund_positive"),
        sa.CheckConstraint(
            "status IN ('requested', 'processing', 'succeeded', 'failed')",
            name="ck_credit_refund_status",
        ),
    )
    op.create_index(
        "ix_credit_refunds_workspace_id", "credit_refunds", ["workspace_id"]
    )
    op.create_index(
        "ix_credit_refund_purchase", "credit_refunds", ["purchase_id", "created_at"]
    )
    op.create_index(
        "ix_credit_refunds_next_retry_at", "credit_refunds", ["next_retry_at"]
    )


def downgrade() -> None:
    raise RuntimeError(
        "Financial refund records must be retained; use a forward migration."
    )
