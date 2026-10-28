"""Frozen purchase terms, calendar memberships and source-bound credit lots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261028_0031"
down_revision = "20261028_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    with op.batch_alter_table("workspace_credit_accounts") as batch:
        batch.add_column(
            sa.Column(
                "lots_initialized",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
    with op.batch_alter_table("credit_reservations") as batch:
        batch.add_column(sa.Column("credit_allocations", json_type, nullable=True))
    with op.batch_alter_table("credit_ledger_entries") as batch:
        batch.add_column(sa.Column("description", sa.String(200), nullable=True))
    with op.batch_alter_table("credit_purchases") as batch:
        batch.add_column(sa.Column("product_snapshot", json_type, nullable=True))

    def stamp():
        return [
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
        ]

    op.create_table(
        "credit_memberships",
        sa.Column("id", sa.String(40), primary_key=True),
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
            unique=True,
        ),
        sa.Column("plan_id", sa.String(20), nullable=False),
        sa.Column("plan_name", sa.String(100), nullable=False),
        sa.Column("monthly_credits", sa.BigInteger(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        *stamp(),
        sa.CheckConstraint(
            "months IN (1, 12) AND monthly_credits > 0 AND ends_at > starts_at",
            name="ck_credit_membership_terms",
        ),
    )
    op.create_index(
        "ix_credit_memberships_workspace_id", "credit_memberships", ["workspace_id"]
    )
    op.create_table(
        "credit_grants",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_key", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("remaining", sa.BigInteger(), nullable=False),
        sa.Column("held", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "ledger_entry_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("credit_ledger_entries.id", ondelete="RESTRICT"),
            nullable=True,
            unique=True,
        ),
        *stamp(),
        sa.UniqueConstraint(
            "workspace_id", "source_key", name="uq_credit_grant_source"
        ),
        sa.CheckConstraint(
            "amount >= 0 AND remaining >= 0 AND held >= 0 "
            "AND held <= remaining AND remaining <= amount",
            name="ck_credit_grant_balance",
        ),
    )
    op.create_index("ix_credit_grants_workspace_id", "credit_grants", ["workspace_id"])


def downgrade() -> None:
    # Downgrading deletes financial provenance and must never run in production.
    raise RuntimeError(
        "Financial membership migration is forward-only; "
        "disable new orders to roll back safely."
    )
