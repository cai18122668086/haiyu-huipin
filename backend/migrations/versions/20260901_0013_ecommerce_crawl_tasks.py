"""Add the first persisted domestic ecommerce crawl task loop."""

import sqlalchemy as sa
from alembic import op

revision = "20260901_0013"
down_revision = "20260824_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_tasks",
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
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("stage", sa.String(32)),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message_safe", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("account_id", sa.String(64)),
        sa.Column("profile_id", sa.String(128)),
        sa.Column(
            "request_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("result_payload", sa.JSON()),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
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
            "workspace_id", "idempotency_key", name="uq_crawl_tasks_idempotency"
        ),
    )
    op.create_index(
        "ix_crawl_tasks_workspace_created",
        "crawl_tasks",
        ["workspace_id", "created_at", "id"],
    )
    op.create_index(
        "ix_crawl_tasks_status_queued", "crawl_tasks", ["status", "queued_at"]
    )


    op.create_table(
        "crawl_profile_leases",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("profile_id", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column(
            "task_id",
            sa.String(40),
            sa.ForeignKey("crawl_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint("profile_id", name="uq_crawl_profile_leases_profile"),
        sa.UniqueConstraint("task_id", name="uq_crawl_profile_leases_task"),
    )
    op.create_index(
        "ix_crawl_profile_leases_expires", "crawl_profile_leases", ["expires_at"]
    )


    op.create_table(
        "crawl_attempts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(40),
            sa.ForeignKey("crawl_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message_safe", sa.Text()),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_crawl_attempt_task_no"),
    )
    op.create_index(
        "ix_crawl_attempts_task_created", "crawl_attempts", ["task_id", "created_at"]
    )

    op.create_table(
        "crawl_events",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(40),
            sa.ForeignKey("crawl_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(32)),
        sa.Column("to_status", sa.String(32)),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("details", sa.JSON()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_crawl_events_task_created", "crawl_events", ["task_id", "created_at", "id"]
    )

    op.create_table(
        "product_snapshots",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(40),
            sa.ForeignKey("crawl_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schema_version",
            sa.String(24),
            nullable=False,
            server_default="product_snapshot.v1",
        ),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("product_id", sa.String(128)),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "adapter_version",
            sa.String(64),
            nullable=False,
            server_default="ecommerce-adapter.v1",
        ),
        sa.Column(
            "parser_version",
            sa.String(64),
            nullable=False,
            server_default="ecommerce-normalizer.v1",
        ),
        sa.Column("title", sa.String(500)),
        sa.Column("price", sa.String(128)),
        sa.Column("shop_name", sa.String(300)),
        sa.Column("sales", sa.String(128)),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "warnings", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
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
        sa.UniqueConstraint("task_id", name="uq_product_snapshots_task"),
    )
    op.create_index(
        "ix_product_snapshots_workspace_created",
        "product_snapshots",
        ["workspace_id", "created_at"],
    )

    op.create_table(
        "product_assets",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.String(40),
            sa.ForeignKey("product_snapshots.id", ondelete="CASCADE"),
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
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("source", sa.String(120)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "role",
            "ordinal",
            name="uq_product_assets_snapshot_role_ordinal",
        ),
    )
    op.create_index(
        "ix_product_assets_snapshot",
        "product_assets",
        ["snapshot_id", "role", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_assets_snapshot", table_name="product_assets")
    op.drop_table("product_assets")
    op.drop_index(
        "ix_product_snapshots_workspace_created", table_name="product_snapshots"
    )
    op.drop_table("product_snapshots")
    op.drop_index("ix_crawl_events_task_created", table_name="crawl_events")
    op.drop_table("crawl_events")
    op.drop_index("ix_crawl_attempts_task_created", table_name="crawl_attempts")
    op.drop_table("crawl_attempts")
    op.drop_index("ix_crawl_profile_leases_expires", table_name="crawl_profile_leases")
    op.drop_table("crawl_profile_leases")
    op.drop_index("ix_crawl_tasks_status_queued", table_name="crawl_tasks")
    op.drop_index("ix_crawl_tasks_workspace_created", table_name="crawl_tasks")
    op.drop_table("crawl_tasks")
