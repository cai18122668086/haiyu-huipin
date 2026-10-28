"""Add worker-bound remote login job metadata."""

import sqlalchemy as sa
from alembic import op

revision = "20260904_0023"
down_revision = "20260903_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ecommerce_accounts",
        sa.Column("worker_id", sa.String(length=40), nullable=True),
    )
    op.create_index(
        "ix_ecommerce_accounts_worker_id",
        "ecommerce_accounts",
        ["worker_id"],
    )
    op.add_column(
        "ecommerce_login_sessions",
        sa.Column("worker_id", sa.String(length=40), nullable=True),
    )
    op.create_index(
        "ix_ecommerce_login_sessions_worker_id",
        "ecommerce_login_sessions",
        ["worker_id"],
    )
    op.add_column(
        "ecommerce_login_sessions",
        sa.Column(
            "state", sa.String(length=32), nullable=False, server_default="queued"
        ),
    )
    op.add_column(
        "ecommerce_login_sessions",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ecommerce_login_sessions",
        sa.Column("last_frame_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ecommerce_login_sessions_worker_state",
        "ecommerce_login_sessions",
        ["worker_id", "state", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ecommerce_login_sessions_worker_state",
        table_name="ecommerce_login_sessions",
    )
    op.drop_index(
        "ix_ecommerce_login_sessions_worker_id",
        table_name="ecommerce_login_sessions",
    )
    op.drop_column("ecommerce_login_sessions", "last_frame_at")
    op.drop_column("ecommerce_login_sessions", "claimed_at")
    op.drop_column("ecommerce_login_sessions", "state")
    op.drop_column("ecommerce_login_sessions", "worker_id")
    op.drop_index("ix_ecommerce_accounts_worker_id", table_name="ecommerce_accounts")
    op.drop_column("ecommerce_accounts", "worker_id")
