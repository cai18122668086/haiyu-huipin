"""Add task-linked support records without changing wallet or payment tables."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0036"
down_revision = "20261030_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_cases",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "job_id", sa.String(40), sa.ForeignKey("jobs.id", ondelete="RESTRICT")
        ),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "request_key", name="uq_support_case_request"
        ),
        sa.CheckConstraint(
            "status IN ('open','in_progress','resolved')", name="ck_support_case_status"
        ),
    )
    op.create_index("ix_support_cases_workspace_id", "support_cases", ["workspace_id"])
    op.create_index("ix_support_cases_job_id", "support_cases", ["job_id"])


def downgrade() -> None:
    raise RuntimeError("Preserve customer support history during application rollback.")
