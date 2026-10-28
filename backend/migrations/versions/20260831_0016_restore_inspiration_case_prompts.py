"""Restore the approved case-specific prompts without changing workflow routing."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.inspiration_workflows import CANONICAL_PROMPT_BY_TITLE

revision = "20260831_0016"
down_revision = "20260831_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Fresh databases provision the catalog separately from migrations.
    if bind.execute(sa.text("SELECT 1 FROM inspiration_cases LIMIT 1")).first() is None:
        return
    for title, prompt in CANONICAL_PROMPT_BY_TITLE.items():
        result = bind.execute(
            sa.text(
                "UPDATE inspiration_cases SET prompt = :prompt "
                "WHERE title = :title"
            ),
            {"prompt": prompt, "title": title},
        )
        if result.rowcount != 1:
            raise RuntimeError(
                f"Expected exactly one inspiration case for {title!r}, "
                f"updated {result.rowcount}."
            )


def downgrade() -> None:
    # The pre-0016 values were not a valid source of truth; do not silently
    # reintroduce the generated generic prompts that caused the mismatch.
    pass
