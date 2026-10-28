"""Add durable administrator roles and permissions."""

import sqlalchemy as sa
from alembic import op

revision = "20260902_0020"
down_revision = "20260902_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False, server_default="admin"),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column("permissions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_by_user_id", sa.String(length=40), nullable=True),
        sa.Column("updated_by_user_id", sa.String(length=40), nullable=True),
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
        sa.CheckConstraint(
            "role IN ('super_admin', 'admin')", name="ck_admin_users_role"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name="ck_admin_users_status"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_admin_users_status", "admin_users", ["status"])


def downgrade() -> None:
    op.drop_index("ix_admin_users_status", table_name="admin_users")
    op.drop_table("admin_users")
