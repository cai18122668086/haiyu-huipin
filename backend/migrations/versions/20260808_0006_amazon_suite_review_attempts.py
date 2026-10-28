"""Add durable Amazon whole-suite review attempts.

Revision ID: 20260808_0006
Revises: 20260727_0005
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260808_0006"
down_revision: str | None = "20260727_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "amazon_suite_review_attempts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column("job_id", sa.String(40), nullable=False),
        sa.Column("review_round", sa.Integer(), nullable=False),
        sa.Column("output_asset_set_hash", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_idempotency_key", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.String(24),
            server_default=sa.text("'claimed'"),
            nullable=False,
        ),
        sa.Column("lease_token", sa.String(64), nullable=False),
        sa.Column("leased_by", sa.String(128), nullable=False),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column(
            "raw_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "normalized_usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("actual_cost_micros", sa.BigInteger()),
        sa.Column("estimated_cost_micros", sa.BigInteger()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("provider_call_started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "review_round > 0",
            name="ck_amazon_suite_review_round_positive",
        ),
        sa.CheckConstraint(
            "actual_cost_micros IS NULL OR actual_cost_micros >= 0",
            name="ck_amazon_suite_review_actual_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_cost_micros IS NULL OR estimated_cost_micros >= 0",
            name="ck_amazon_suite_review_estimated_cost_nonnegative",
        ),
        sa.UniqueConstraint(
            "job_id",
            "review_round",
            "output_asset_set_hash",
            name="uq_amazon_suite_review_job_round_output",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_idempotency_key",
            name="uq_amazon_suite_review_provider_idem",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["jobs.workspace_id", "jobs.id"],
            name="fk_amazon_suite_review_workspace_job",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_amazon_suite_review_job_status_lease",
        "amazon_suite_review_attempts",
        ["workspace_id", "job_id", "status", "lease_expires_at"],
    )
    op.create_index(
        "ix_amazon_suite_review_provider_request",
        "amazon_suite_review_attempts",
        ["provider", "provider_request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_amazon_suite_review_provider_request",
        table_name="amazon_suite_review_attempts",
    )
    op.drop_index(
        "ix_amazon_suite_review_job_status_lease",
        table_name="amazon_suite_review_attempts",
    )
    op.drop_table("amazon_suite_review_attempts")
