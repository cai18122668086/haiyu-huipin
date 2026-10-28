"""Add durable leases for human-assisted ecommerce login sessions."""

import sqlalchemy as sa
from alembic import op

revision = "20260902_0021"
down_revision = "20260902_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ecommerce_login_sessions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("workspace_id", sa.String(length=40), nullable=False),
        sa.Column("started_by", sa.String(length=40), nullable=False),
        sa.Column(
            "status", sa.String(length=24), nullable=False, server_default="active"
        ),
        sa.Column("transport", sa.String(length=24), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"], ["ecommerce_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["started_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ecommerce_login_sessions_account_status",
        "ecommerce_login_sessions",
        ["account_id", "status", "expires_at"],
    )
    op.create_index(
        "ix_ecommerce_login_sessions_workspace_created",
        "ecommerce_login_sessions",
        ["workspace_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ecommerce_login_sessions_workspace_created",
        table_name="ecommerce_login_sessions",
    )
    op.drop_index(
        "ix_ecommerce_login_sessions_account_status",
        table_name="ecommerce_login_sessions",
    )
    op.drop_table("ecommerce_login_sessions")
