"""Append explicit processing choices; preserve legacy users and existing jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261030_0037"
down_revision = "20261030_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processing_consents",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("notice_sha256", sa.String(64), nullable=False),
        sa.Column(
            "notice",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "choices",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_processing_consents_user_id", "processing_consents", ["user_id"]
    )


def downgrade() -> None:
    raise RuntimeError(
        "Preserve processing consent history during application rollback."
    )
