"""Independent, idempotent official-channel credit purchase orders."""

import sqlalchemy as sa
from alembic import op

revision = "20261028_0030"
down_revision = "20261028_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credit_purchases",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("package_id", sa.String(40), nullable=False),
        sa.Column("package_name", sa.String(100), nullable=False),
        sa.Column("amount_fen", sa.BigInteger(), nullable=False),
        sa.Column("credits", sa.BigInteger(), nullable=False),
        sa.Column("merchant_pid", sa.String(32), nullable=False),
        sa.Column("signing_key_id", sa.String(64), nullable=False),
        sa.Column("checkout_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("gateway_trade_no", sa.String(64)),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column(
            "ledger_entry_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("credit_ledger_entries.id", ondelete="RESTRICT"),
            unique=True,
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
        sa.UniqueConstraint(
            "workspace_id",
            "created_by_user_id",
            "idempotency_key",
            name="uq_credit_purchase_request",
        ),
        sa.UniqueConstraint(
            "merchant_pid", "gateway_trade_no", name="uq_credit_purchase_gateway_trade"
        ),
        sa.CheckConstraint(
            "amount_fen > 0 AND credits > 0", name="ck_credit_purchase_positive"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'paid')", name="ck_credit_purchase_status"
        ),
    )
    op.create_index(
        "ix_credit_purchases_workspace_id", "credit_purchases", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_credit_purchases_workspace_id", table_name="credit_purchases")
    op.drop_table("credit_purchases")
