"""Restore historical inspiration card metadata without touching prompts or assets."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.inspiration_workflows import CANONICAL_METADATA_BY_TITLE

revision = "20260831_0017"
down_revision = "20260831_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Fresh databases provision the catalog separately from migrations.
    if bind.execute(sa.text("SELECT 1 FROM inspiration_cases LIMIT 1")).first() is None:
        return
    for title, metadata in CANONICAL_METADATA_BY_TITLE.items():
        result = bind.execute(
            sa.text(
                "UPDATE inspiration_cases SET "
                "description = :description, role = :role, type = :type, "
                "category = :category, platform = :platform, ratio = :ratio, "
                "intent = :intent WHERE title = :title"
            ),
            {"title": title, **metadata},
        )
        if result.rowcount != 1:
            raise RuntimeError(
                f"Expected exactly one inspiration case for {title!r}, "
                f"updated {result.rowcount}."
            )


def downgrade() -> None:
    # The generic metadata values that preceded this repair were not a valid
    # historical source of truth; do not silently restore them.
    pass
