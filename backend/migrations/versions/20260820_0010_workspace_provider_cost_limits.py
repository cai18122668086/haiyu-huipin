"""Raise legacy workspace provider cost defaults to $10,000,000.

Revision ID: 20260820_0010
Revises: 20260817_0009
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0010"
down_revision: str | None = "20260817_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_DAILY_LIMIT_MICROS = 10_000_000
_LEGACY_MONTHLY_LIMIT_MICROS = 100_000_000
_NEW_LIMIT_MICROS = 10_000_000_000_000


def upgrade() -> None:
    """Update only fields that still carry the original $10/$100 defaults."""
    op.execute(
        sa.text(
            """
            UPDATE workspace_limits
            SET
                daily_provider_cost_limit_micros = CASE
                    WHEN daily_provider_cost_limit_micros = :legacy_daily
                    THEN :new_limit
                    ELSE daily_provider_cost_limit_micros
                END,
                monthly_provider_cost_limit_micros = CASE
                    WHEN monthly_provider_cost_limit_micros = :legacy_monthly
                    THEN :new_limit
                    ELSE monthly_provider_cost_limit_micros
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE daily_provider_cost_limit_micros = :legacy_daily
               OR monthly_provider_cost_limit_micros = :legacy_monthly
            """
        ).bindparams(
            legacy_daily=_LEGACY_DAILY_LIMIT_MICROS,
            legacy_monthly=_LEGACY_MONTHLY_LIMIT_MICROS,
            new_limit=_NEW_LIMIT_MICROS,
        )
    )


def downgrade() -> None:
    """Restore only fields still carrying this migration's new default."""
    op.execute(
        sa.text(
            """
            UPDATE workspace_limits
            SET
                daily_provider_cost_limit_micros = CASE
                    WHEN daily_provider_cost_limit_micros = :new_limit
                    THEN :legacy_daily
                    ELSE daily_provider_cost_limit_micros
                END,
                monthly_provider_cost_limit_micros = CASE
                    WHEN monthly_provider_cost_limit_micros = :new_limit
                    THEN :legacy_monthly
                    ELSE monthly_provider_cost_limit_micros
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE daily_provider_cost_limit_micros = :new_limit
               OR monthly_provider_cost_limit_micros = :new_limit
            """
        ).bindparams(
            legacy_daily=_LEGACY_DAILY_LIMIT_MICROS,
            legacy_monthly=_LEGACY_MONTHLY_LIMIT_MICROS,
            new_limit=_NEW_LIMIT_MICROS,
        )
    )
