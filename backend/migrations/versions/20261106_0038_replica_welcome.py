"""Add disabled signup campaign and task-scoped replica entitlements."""

import sqlalchemy as sa
from alembic import op

revision = "20261106_0038"
down_revision = "20261030_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "promotion_campaigns",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "replica_welcome_grants",
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("phone_key", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "session_id",
            sa.String(40),
            sa.ForeignKey("replica_documents.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "source_job_id",
            sa.String(40),
            sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO promotion_campaigns (id, enabled, starts_at) "
            "VALUES ('replica-welcome-v1', false, CURRENT_TIMESTAMP)"
        )
    )
    op.create_index(
        "uq_newcomer_pending_user",
        "credit_purchases",
        ["created_by_user_id"],
        unique=True,
        postgresql_where=sa.text("package_id = 'newcomer_160' AND status = 'pending'"),
        sqlite_where=sa.text("package_id = 'newcomer_160' AND status = 'pending'"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Keep promotion and entitlement history during application rollback."
    )
