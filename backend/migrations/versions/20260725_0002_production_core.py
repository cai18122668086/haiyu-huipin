"""Add the Phase 1.1 production domain model.

Revision ID: 20260725_0002
Revises: 20260725_0001
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260725_0002"
down_revision: str | None = "20260725_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("auth_provider", sa.String(32), nullable=False),
        sa.Column("auth_subject", sa.String(255), nullable=False),
        sa.Column("email_normalized", sa.String(320)),
        sa.Column("display_name", sa.String(120)),
        sa.Column("status", sa.String(16), nullable=False),
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
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("auth_provider", "auth_subject", name="uq_users_auth"),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_workspaces_status", "workspaces", ["status"])
    op.create_table(
        "workspace_members",
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
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
    )
    op.create_table(
        "workspace_limits",
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("storage_quota_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_active_jobs", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_jobs", sa.Integer(), nullable=False),
        sa.Column("daily_provider_cost_limit_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "monthly_provider_cost_limit_micros", sa.BigInteger(), nullable=False
        ),
        sa.Column("max_upload_bytes", sa.BigInteger(), nullable=False),
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
    )

    for name, column_type in [
        ("media_kind", sa.String(16)),
        ("origin_kind", sa.String(16)),
        ("lifecycle_status", sa.String(16)),
        ("original_filename", sa.String(255)),
        ("sha256", sa.String(64)),
        ("storage_provider", sa.String(16)),
        ("storage_bucket", sa.String(128)),
        ("object_key", sa.String(512)),
        ("object_version", sa.String(255)),
        ("etag", sa.String(255)),
    ]:
        op.add_column("assets", sa.Column(name, column_type, nullable=True))
    op.add_column(
        "assets",
        sa.Column(
            "metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.add_column(
        "assets", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )

    for name, column_type in [
        ("stage", sa.String(32)),
        ("display_name", sa.String(160)),
        ("configuration_schema", sa.String(32)),
        ("release_version", sa.String(64)),
        ("root_job_id", sa.String(40)),
        ("retry_scope", sa.String(16)),
        ("currency", sa.String(3)),
        ("usage_completeness", sa.String(16)),
        ("cancel_reason", sa.Text()),
    ]:
        op.add_column("jobs", sa.Column(name, column_type, nullable=True))
    for name in [
        "state_version",
        "expected_item_count",
        "succeeded_item_count",
        "failed_item_count",
    ]:
        op.add_column("jobs", sa.Column(name, sa.Integer(), nullable=True))
    for name in [
        "estimated_cost_min_micros",
        "estimated_cost_max_micros",
        "actual_cost_micros",
        "estimated_credits",
        "actual_credits",
    ]:
        op.add_column("jobs", sa.Column(name, sa.BigInteger(), nullable=True))
    op.add_column("jobs", sa.Column("error_details", JSONB, nullable=True))
    for name in ["cancel_requested_at", "queued_at"]:
        op.add_column(
            "jobs", sa.Column(name, sa.DateTime(timezone=True), nullable=True)
        )
    op.create_foreign_key(
        "fk_jobs_root_job_id", "jobs", "jobs", ["root_job_id"], ["id"]
    )

    op.create_table(
        "job_items",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column(
            "job_id",
            sa.String(40),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(32)),
        sa.Column("quality", sa.String(16)),
        sa.Column(
            "configuration",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("source_job_item_id", sa.String(40), sa.ForeignKey("job_items.id")),
        sa.Column("result_source", sa.String(16)),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("error_details", JSONB),
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
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("job_id", "item_key", name="uq_job_items_key"),
        sa.UniqueConstraint("job_id", "ordinal", name="uq_job_items_ordinal"),
        sa.UniqueConstraint(
            "workspace_id", "job_id", "id", name="uq_job_items_workspace_job_id"
        ),
    )
    op.create_index("ix_job_items_job_id", "job_items", ["job_id"])

    op.create_table(
        "job_assets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column(
            "job_id",
            sa.String(40),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_item_id",
            sa.String(40),
            sa.ForeignKey("job_items.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "asset_id",
            sa.String(40),
            sa.ForeignKey("assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("is_deliverable", sa.Boolean(), nullable=False),
        sa.Column(
            "validation",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_job_assets_ordinal"),
    )
    op.create_index("ix_job_assets_job_id", "job_assets", ["job_id"])
    op.create_index("ix_job_assets_asset_id", "job_assets", ["asset_id"])

    op.create_table(
        "job_plans",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column(
            "job_id",
            sa.String(40),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("validation_status", sa.String(16), nullable=False),
        sa.Column(
            "validation_errors",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("validator_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("job_id", "revision", name="uq_job_plans_revision"),
    )

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40), nullable=False),
        sa.Column(
            "job_id",
            sa.String(40),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_item_id",
            sa.String(40),
            sa.ForeignKey("job_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=False),
        sa.Column("leased_by", sa.String(128), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column("provider_idempotency_key", sa.String(255), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("error_details", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("job_item_id", "attempt_no", name="uq_job_attempt_no"),
        sa.UniqueConstraint(
            "provider", "provider_idempotency_key", name="uq_attempt_provider_idem"
        ),
        sa.UniqueConstraint("provider_request_id"),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(40),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_item_id",
            sa.String(40),
            sa.ForeignKey("job_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            sa.String(40),
            sa.ForeignKey("job_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("cached_tokens", sa.BigInteger()),
        sa.Column("input_image_count", sa.Integer()),
        sa.Column("output_image_count", sa.Integer()),
        sa.Column("quality", sa.String(16)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("usage_reported", sa.Boolean(), nullable=False),
        sa.Column(
            "raw_usage",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("pricing_version", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("estimated_cost_micros", sa.BigInteger()),
        sa.Column("actual_cost_micros", sa.BigInteger()),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )

    op.create_table(
        "idempotency_requests",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("route_template", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("resource_type", sa.String(32)),
        sa.Column("resource_id", sa.String(40)),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", JSONB),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "method",
            "route_template",
            "idempotency_key",
            name="uq_idempotency_scope",
        ),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(128), nullable=False, unique=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(128)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])

    op.create_table(
        "workspace_credit_accounts",
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("balance_credits", sa.BigInteger(), nullable=False),
        sa.Column("reserved_credits", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
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
            "balance_credits >= 0", name="ck_credit_balance_nonnegative"
        ),
        sa.CheckConstraint(
            "reserved_credits >= 0", name="ck_credit_reserved_nonnegative"
        ),
        sa.CheckConstraint(
            "reserved_credits <= balance_credits", name="ck_credit_reserved_bounded"
        ),
    )
    op.create_table(
        "credit_reservations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.String(40),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("reserved_credits", sa.BigInteger(), nullable=False),
        sa.Column("settled_credits", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
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
    )
    op.create_table(
        "credit_ledger_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reservation_id",
            sa.String(40),
            sa.ForeignKey("credit_reservations.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "job_id", sa.String(40), sa.ForeignKey("jobs.id", ondelete="SET NULL")
        ),
        sa.Column("entry_type", sa.String(24), nullable=False),
        sa.Column("delta_balance_credits", sa.BigInteger(), nullable=False),
        sa.Column("delta_reserved_credits", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_credits", sa.BigInteger(), nullable=False),
        sa.Column("reserved_after_credits", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "actor_user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32)),
        sa.Column("resource_id", sa.String(40)),
        sa.Column("request_id", sa.String(64)),
        sa.Column(
            "details", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("credit_ledger_entries")
    op.drop_table("credit_reservations")
    op.drop_table("workspace_credit_accounts")
    op.drop_index("ix_outbox_events_aggregate_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("idempotency_requests")
    op.drop_table("usage_events")
    op.drop_table("job_attempts")
    op.drop_table("job_plans")
    op.drop_index("ix_job_assets_asset_id", table_name="job_assets")
    op.drop_index("ix_job_assets_job_id", table_name="job_assets")
    op.drop_table("job_assets")
    op.drop_index("ix_job_items_job_id", table_name="job_items")
    op.drop_table("job_items")
    op.drop_constraint("fk_jobs_root_job_id", "jobs", type_="foreignkey")
    for column in [
        "queued_at",
        "cancel_requested_at",
        "error_details",
        "actual_credits",
        "estimated_credits",
        "actual_cost_micros",
        "estimated_cost_max_micros",
        "estimated_cost_min_micros",
        "failed_item_count",
        "succeeded_item_count",
        "expected_item_count",
        "state_version",
        "cancel_reason",
        "usage_completeness",
        "currency",
        "retry_scope",
        "root_job_id",
        "release_version",
        "configuration_schema",
        "display_name",
        "stage",
    ]:
        op.drop_column("jobs", column)
    for column in [
        "deleted_at",
        "metadata",
        "etag",
        "object_version",
        "object_key",
        "storage_bucket",
        "storage_provider",
        "sha256",
        "original_filename",
        "lifecycle_status",
        "origin_kind",
        "media_kind",
    ]:
        op.drop_column("assets", column)
    op.drop_table("workspace_limits")
    op.drop_table("workspace_members")
    op.drop_index("ix_workspaces_status", table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_table("users")
