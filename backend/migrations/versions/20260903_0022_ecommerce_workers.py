"""Add scope-bound outbound local ecommerce worker registration."""

import sqlalchemy as sa
from alembic import op

revision = "20260903_0022"
down_revision = "20260902_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ecommerce_workers",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("workspace_id", sa.String(length=40), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("protocol_version", sa.String(length=32), nullable=False),
        sa.Column("agent_version", sa.String(length=64), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="offline"
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_token_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "session_token_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "workspace_id",
            "device_id",
            name="uq_ecommerce_workers_user_workspace_device",
        ),
        sa.UniqueConstraint(
            "session_token_hash", name="uq_ecommerce_workers_session_token_hash"
        ),
    )
    op.create_index(
        "ix_ecommerce_workers_workspace_status",
        "ecommerce_workers",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_ecommerce_workers_last_seen", "ecommerce_workers", ["last_seen_at"]
    )

    op.create_table(
        "ecommerce_worker_enrollments",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("workspace_id", sa.String(length=40), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_hash", name="uq_ecommerce_worker_enrollments_token_hash"
        ),
    )
    op.create_index(
        "ix_ecommerce_worker_enrollments_expires",
        "ecommerce_worker_enrollments",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ecommerce_worker_enrollments_expires",
        table_name="ecommerce_worker_enrollments",
    )
    op.drop_table("ecommerce_worker_enrollments")
    op.drop_index("ix_ecommerce_workers_last_seen", table_name="ecommerce_workers")
    op.drop_index(
        "ix_ecommerce_workers_workspace_status", table_name="ecommerce_workers"
    )
    op.drop_table("ecommerce_workers")
