"""Add database-backed inspiration cases."""

import sqlalchemy as sa
from alembic import op

revision = "20260824_0012"
down_revision = "20260820_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inspiration_cases",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("slug", sa.String(120), nullable=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("role", sa.String(80), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("ratio", sa.String(24), nullable=False),
        sa.Column("intent", sa.String(300), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_by_user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
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
            "status IN ('draft', 'published', 'archived')",
            name="ck_inspiration_cases_status",
        ),
        sa.UniqueConstraint("slug", name="uq_inspiration_cases_slug"),
    )
    op.create_index(
        "ix_inspiration_cases_public_order",
        "inspiration_cases",
        ["status", "is_pinned", "sort_order", "published_at"],
    )
    op.create_table(
        "inspiration_case_assets",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(40),
            sa.ForeignKey("inspiration_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.String(40),
            sa.ForeignKey("assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(40)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "case_id", "role", "ordinal", name="uq_inspiration_case_asset_role_ordinal"
        ),
    )
    op.create_index(
        "ix_inspiration_case_assets_case",
        "inspiration_case_assets",
        ["case_id", "role", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inspiration_case_assets_case", table_name="inspiration_case_assets"
    )
    op.drop_table("inspiration_case_assets")
    op.drop_index("ix_inspiration_cases_public_order", table_name="inspiration_cases")
    op.drop_table("inspiration_cases")
