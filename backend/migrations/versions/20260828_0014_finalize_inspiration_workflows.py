"""Finalize the approved workflow routing for all published inspiration cases."""

import sqlalchemy as sa
from alembic import op

from app.inspiration_workflows import (
    LOCAL_EDIT_CASE_TITLES,
    PRODUCT_PROMPT_CASE_TITLES,
    REFERENCE_REMIX_CASE_TITLES,
)

revision = "20260828_0014"
down_revision = "20260828_0013"
branch_labels = None
depends_on = None


def _set_workflow(
    bind: sa.Connection,
    titles: tuple[str, ...],
    workflow: str,
    source: str,
    status: str,
) -> None:
    for title in titles:
        bind.execute(
            sa.text(
                "UPDATE inspiration_cases "
                "SET workflow_type = :workflow_type, "
                "reference_source = :reference_source, "
                "configuration_status = CASE "
                "WHEN :configuration_status = 'incomplete' THEN 'incomplete' "
                "WHEN trim(prompt) <> '' THEN 'ready' ELSE 'incomplete' END "
                "WHERE title = :title"
            ),
            {
                "workflow_type": workflow,
                "reference_source": source,
                "configuration_status": status,
                "title": title,
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    _set_workflow(bind, PRODUCT_PROMPT_CASE_TITLES, "product_prompt", "none", "ready")
    _set_workflow(
        bind,
        REFERENCE_REMIX_CASE_TITLES,
        "reference_remix",
        "admin_reference",
        "incomplete",
    )
    _set_workflow(bind, LOCAL_EDIT_CASE_TITLES, "local_edit", "none", "ready")


def downgrade() -> None:
    bind = op.get_bind()
    # Revert only the rows this migration owns. The prior migration deliberately
    # leaves all non-product cases fail-closed as legacy_unclassified.
    _set_workflow(
        bind,
        REFERENCE_REMIX_CASE_TITLES + LOCAL_EDIT_CASE_TITLES,
        "legacy_unclassified",
        "none",
        "incomplete",
    )
    _set_workflow(
        bind,
        ("场景图（玩偶）",),
        "legacy_unclassified",
        "none",
        "incomplete",
    )
