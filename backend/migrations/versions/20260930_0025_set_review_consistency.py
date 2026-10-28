"""Allow complete generation with a separately blocked Aurora set review.

No historical rows are updated. Downgrade refuses incompatible review rows.
"""

from alembic import op

revision = "20260930_0025"
down_revision = "20260909_0024"
branch_labels = None
depends_on = None

OLD = """
            (
                status IN ('partial', 'completed', 'failed', 'cancelled')
                AND finished_at IS NOT NULL
                AND (
                    (
                        status = 'completed'
                        AND succeeded_item_count = expected_item_count
                        AND failed_item_count = 0
                    )
                    OR (
                        status = 'partial'
                        AND succeeded_item_count > 0
                        AND succeeded_item_count < expected_item_count
                    )
                    OR (status = 'failed' AND succeeded_item_count = 0)
                    OR status = 'cancelled'
                )
            )
            OR (
                status NOT IN ('partial', 'completed', 'failed', 'cancelled')
                AND finished_at IS NULL
            )
"""
NEW = """
            (
                status IN ('partial', 'completed', 'failed', 'cancelled')
                AND finished_at IS NOT NULL
                AND (
                    (
                        status = 'completed'
                        AND succeeded_item_count = expected_item_count
                        AND failed_item_count = 0
                    )
                    OR (
                        status = 'partial'
                        AND succeeded_item_count > 0
                        AND (
                            succeeded_item_count < expected_item_count
                            OR (
                                type = 'domestic_main_image'
                                AND succeeded_item_count = expected_item_count
                                AND failed_item_count = 0
                                AND error_code IN (
                                    'AURORA_SET_AUDIT_ERROR',
                                    'AURORA_SET_AUDIT_FAILED'
                                )
                                AND stage IN ('set_audit_error', 'set_audit_failed')
                            )
                        )
                    )
                    OR (status = 'failed' AND succeeded_item_count = 0)
                    OR status = 'cancelled'
                )
            )
            OR (
                status NOT IN ('partial', 'completed', 'failed', 'cancelled')
                AND finished_at IS NULL
            )
"""


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_terminal_consistency", type_="check")
        batch.create_check_constraint("ck_jobs_terminal_consistency", NEW)


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_terminal_consistency", type_="check")
        batch.create_check_constraint("ck_jobs_terminal_consistency", OLD)
