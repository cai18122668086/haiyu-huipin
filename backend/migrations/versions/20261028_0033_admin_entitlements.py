"""Manual memberships and permanent support-action receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261028_0033"
down_revision = "20261028_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("credit_memberships") as batch:
        batch.alter_column("purchase_id", existing_type=sa.String(40), nullable=True)
    op.create_table(
        "credit_admin_actions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "response_body",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
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
        sa.UniqueConstraint(
            "workspace_id",
            "actor_user_id",
            "idempotency_key",
            name="uq_credit_admin_action_request",
        ),
    )
    if "idempotency_requests" in sa.inspect(op.get_bind()).get_table_names():
        op.execute(
            sa.text("""
            INSERT INTO credit_admin_actions
                (id, workspace_id, actor_user_id, idempotency_key, request_hash,
                 response_body, created_at, updated_at)
            SELECT id, workspace_id, actor_user_id, idempotency_key, request_hash,
                   response_body, created_at, updated_at
            FROM idempotency_requests
            WHERE route_template = 'admin/billing/adjustments'
              AND method = 'POST' AND response_body IS NOT NULL
        """)
        )


def downgrade() -> None:
    raise RuntimeError("Retain support action receipts; use a forward migration.")
