"""Backfill Phase 1 data and validate production constraints.

Revision ID: 20260725_0003
Revises: 20260725_0002
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0003"
down_revision: str | None = "20260725_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO users (
            id, auth_provider, auth_subject, display_name, status
        ) VALUES (
            'usr_legacy', 'migration', 'legacy', 'Legacy Migration Owner', 'active'
        ) ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO workspaces (id, name, status, created_by_user_id)
        SELECT workspace_id, workspace_id, 'active', 'usr_legacy'
        FROM (
            SELECT workspace_id FROM assets
            UNION
            SELECT workspace_id FROM jobs
        ) source
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO workspace_members (workspace_id, user_id, role, status)
        SELECT id, 'usr_legacy', 'owner', 'active'
        FROM workspaces
        ON CONFLICT (workspace_id, user_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO workspace_limits (
            workspace_id, storage_quota_bytes, max_active_jobs,
            max_concurrent_jobs, daily_provider_cost_limit_micros,
            monthly_provider_cost_limit_micros, max_upload_bytes
        )
        SELECT id, 10737418240, 20, 4, 10000000, 100000000, 20971520
        FROM workspaces
        ON CONFLICT (workspace_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO workspace_credit_accounts (
            workspace_id, balance_credits, reserved_credits, version
        )
        SELECT id, 10000, 0, 0 FROM workspaces
        ON CONFLICT (workspace_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE assets SET
            media_kind = COALESCE(media_kind, 'image'),
            origin_kind = COALESCE(
                origin_kind,
                CASE WHEN origin = 'upload' THEN 'upload' ELSE 'generated' END
            ),
            lifecycle_status = COALESCE(lifecycle_status, 'ready'),
            original_filename = COALESCE(original_filename, filename),
            sha256 = COALESCE(sha256, repeat('0', 64)),
            storage_provider = COALESCE(storage_provider, 'legacy'),
            storage_bucket = COALESCE(storage_bucket, 'legacy'),
            object_key = COALESCE(object_key, storage_key),
            object_version = COALESCE(object_version, '')
        """
    )
    op.execute(
        """
        UPDATE jobs SET
            stage = COALESCE(stage, status),
            configuration_schema = COALESCE(configuration_schema, 'legacy-v1'),
            release_version = COALESCE(release_version, 'phase1'),
            root_job_id = COALESCE(root_job_id, parent_job_id),
            state_version = COALESCE(state_version, 0),
            expected_item_count = COALESCE(expected_item_count, 1),
            succeeded_item_count = COALESCE(
                succeeded_item_count,
                CASE WHEN status = 'completed' THEN 1 ELSE 0 END
            ),
            failed_item_count = COALESCE(
                failed_item_count,
                CASE WHEN status = 'failed' THEN 1 ELSE 0 END
            ),
            estimated_cost_min_micros = COALESCE(
                estimated_cost_min_micros,
                CASE WHEN estimated_cost IS NULL THEN NULL
                     ELSE ROUND(estimated_cost * 1000000)::bigint END
            ),
            estimated_cost_max_micros = COALESCE(
                estimated_cost_max_micros,
                CASE WHEN estimated_cost IS NULL THEN NULL
                     ELSE ROUND(estimated_cost * 1000000)::bigint END
            ),
            actual_cost_micros = COALESCE(
                actual_cost_micros,
                CASE WHEN actual_cost IS NULL THEN NULL
                     ELSE ROUND(actual_cost * 1000000)::bigint END
            ),
            currency = COALESCE(currency, 'USD'),
            usage_completeness = COALESCE(
                usage_completeness,
                CASE WHEN usage ->> 'reported' = 'true' THEN 'complete' ELSE 'none' END
            ),
            queued_at = COALESCE(
                queued_at,
                CASE WHEN status <> 'draft' THEN created_at ELSE NULL END
            )
        """
    )
    op.execute(
        """
        INSERT INTO job_items (
            id, workspace_id, job_id, item_key, ordinal, status,
            configuration, attempt_count, state_version
        )
        SELECT
            'item_' || md5(id), workspace_id, id, 'result', 0,
            CASE status
                WHEN 'completed' THEN 'succeeded'
                WHEN 'failed' THEN 'failed'
                WHEN 'cancelled' THEN 'cancelled'
                ELSE 'pending'
            END,
            '{}'::jsonb, 0, 0
        FROM jobs
        ON CONFLICT (job_id, item_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO job_assets (
            workspace_id, job_id, asset_id, direction, role, ordinal,
            is_deliverable, validation
        )
        SELECT
            jobs.workspace_id,
            jobs.id,
            input_asset.asset_id,
            'input',
            CASE jobs.type
                WHEN 'local_edit' THEN 'source'
                ELSE 'product_reference'
            END,
            input_asset.ordinality - 1,
            false,
            '{}'::jsonb
        FROM jobs
        CROSS JOIN LATERAL json_array_elements_text(jobs.input_asset_ids)
            WITH ORDINALITY AS input_asset(asset_id, ordinality)
        JOIN assets
          ON assets.id = input_asset.asset_id
         AND assets.workspace_id = jobs.workspace_id
        """
    )

    for column in [
        "media_kind",
        "origin_kind",
        "lifecycle_status",
        "original_filename",
        "sha256",
        "storage_provider",
        "storage_bucket",
        "object_key",
        "object_version",
    ]:
        op.alter_column("assets", column, nullable=False)
    for column in [
        "configuration_schema",
        "release_version",
        "state_version",
        "expected_item_count",
        "succeeded_item_count",
        "failed_item_count",
        "usage_completeness",
    ]:
        op.alter_column("jobs", column, nullable=False)

    # The old Phase 1 columns remain for one compatibility window, but v1 no
    # longer writes them. Defaults keep upgrades safe until the approved cleanup.
    for column in ["filename", "storage_key", "origin"]:
        op.alter_column("assets", column, nullable=True)
    op.alter_column(
        "jobs", "input_asset_ids", server_default=sa.text("'[]'::json"), nullable=False
    )
    op.alter_column(
        "jobs", "raw_outputs", server_default=sa.text("'[]'::json"), nullable=False
    )
    op.alter_column(
        "jobs",
        "delivery_outputs",
        server_default=sa.text("'[]'::json"),
        nullable=False,
    )
    op.alter_column(
        "jobs", "usage", server_default=sa.text("'{}'::json"), nullable=False
    )

    op.create_unique_constraint(
        "uq_assets_workspace_id", "assets", ["workspace_id", "id"]
    )
    op.create_unique_constraint("uq_jobs_workspace_id", "jobs", ["workspace_id", "id"])
    op.create_unique_constraint(
        "uq_assets_storage_object",
        "assets",
        ["storage_provider", "storage_bucket", "object_key", "object_version"],
    )
    op.create_check_constraint(
        "ck_assets_byte_size_positive", "assets", "byte_size > 0"
    )
    op.create_check_constraint(
        "ck_assets_width_positive", "assets", "width IS NULL OR width > 0"
    )
    op.create_check_constraint(
        "ck_assets_height_positive", "assets", "height IS NULL OR height > 0"
    )
    op.create_check_constraint(
        "ck_jobs_progress", "jobs", "progress >= 0 AND progress <= 100"
    )
    op.create_check_constraint(
        "ck_jobs_result_counts_nonnegative",
        "jobs",
        "succeeded_item_count >= 0 AND failed_item_count >= 0",
    )
    op.create_check_constraint(
        "ck_jobs_result_counts_bounded",
        "jobs",
        "succeeded_item_count + failed_item_count <= expected_item_count",
    )
    op.create_index(
        "ix_assets_workspace_created",
        "assets",
        ["workspace_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_jobs_workspace_status_created",
        "jobs",
        ["workspace_id", "status", sa.text("created_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_workspace_status_created", table_name="jobs")
    op.drop_index("ix_assets_workspace_created", table_name="assets")
    op.drop_constraint("ck_jobs_result_counts_bounded", "jobs", type_="check")
    op.drop_constraint("ck_jobs_result_counts_nonnegative", "jobs", type_="check")
    op.drop_constraint("ck_jobs_progress", "jobs", type_="check")
    op.drop_constraint("ck_assets_height_positive", "assets", type_="check")
    op.drop_constraint("ck_assets_width_positive", "assets", type_="check")
    op.drop_constraint("ck_assets_byte_size_positive", "assets", type_="check")
    op.drop_constraint("uq_assets_storage_object", "assets", type_="unique")
    op.drop_constraint("uq_jobs_workspace_id", "jobs", type_="unique")
    op.drop_constraint("uq_assets_workspace_id", "assets", type_="unique")
