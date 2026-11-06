"""Persist verified result viewing and daily newcomer reminder deduplication."""

import sqlalchemy as sa
from alembic import op

revision = "20261106_0040"
down_revision = "20261106_0039"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "newcomer_offer_views",
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("session_id", sa.String(40), nullable=False),
        sa.Column("first_viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_shown_window", sa.Integer(), nullable=False, server_default="-1"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_credit_purchases_buyer_status",
        "credit_purchases",
        ["created_by_user_id", "status"],
    )
    op.execute(
        sa.text(
            "INSERT INTO promotion_campaigns (id, enabled, starts_at) "
            "VALUES ('newcomer-reminder-v1', false, CURRENT_TIMESTAMP)"
        )
    )


def downgrade():
    raise RuntimeError(
        "Retain result-view history; disable the reminder campaign to roll back."
    )
