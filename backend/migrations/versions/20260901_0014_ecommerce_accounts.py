"""Add workspace-owned ecommerce accounts and isolated profiles."""

import sqlalchemy as sa
from alembic import op

revision = "20260901_0014"
down_revision = "20260901_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ecommerce_accounts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("account_key", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(120)),
        sa.Column("profile_id", sa.String(128), nullable=False),
        sa.Column("profile_path", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "login_state", sa.String(24), nullable=False, server_default="unknown"
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
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
        sa.UniqueConstraint(
            "workspace_id",
            "platform",
            "account_key",
            name="uq_ecommerce_accounts_workspace_platform_key",
        ),
        sa.UniqueConstraint("profile_id", name="uq_ecommerce_accounts_profile"),
        sa.UniqueConstraint("profile_path", name="uq_ecommerce_accounts_profile_path"),
    )
    op.create_index(
        "ix_ecommerce_accounts_workspace_status",
        "ecommerce_accounts",
        ["workspace_id", "status"],
    )
    op.create_foreign_key(
        "fk_crawl_tasks_account_id",
        "crawl_tasks",
        "ecommerce_accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_crawl_tasks_account_id", "crawl_tasks", type_="foreignkey"
    )
    op.drop_index(
        "ix_ecommerce_accounts_workspace_status", table_name="ecommerce_accounts"
    )
    op.drop_table("ecommerce_accounts")
