"""Keep customer tariffs separate from hash-bound generation inputs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261028_0029"
down_revision = "20261027_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Null for historical reservations: never reprice existing work.
    op.add_column(
        "credit_reservations",
        sa.Column(
            "pricing_snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("credit_reservations", "pricing_snapshot")
