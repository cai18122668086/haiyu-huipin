"""Add explicit inspiration workflow routing configuration."""

import sqlalchemy as sa
from alembic import op

revision = "20260828_0013"
down_revision = "20260824_0012"
branch_labels = None
depends_on = None

PRODUCT_PROMPT_CASE_TITLES = (
    "生成AI模特",
    "衣服上身（无中生有）",
    "卖点图（多姿势展示）",
    "衣服上身（模特无中生有）",
    "改造型",
)


def upgrade() -> None:
    # SQLite cannot ALTER TABLE ... ADD CONSTRAINT directly.  Batch mode
    # keeps this migration usable for the local-preview SQLite database while
    # remaining a normal ALTER on PostgreSQL.
    with op.batch_alter_table("inspiration_cases", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "workflow_type",
                sa.String(24),
                nullable=False,
                server_default="legacy_unclassified",
            )
        )
        batch_op.add_column(
            sa.Column(
                "reference_source",
                sa.String(20),
                nullable=False,
                server_default="none",
            )
        )
        batch_op.add_column(
            sa.Column(
                "configuration_status",
                sa.String(16),
                nullable=False,
                server_default="incomplete",
            )
        )
        batch_op.add_column(
            sa.Column(
                "button_label",
                sa.String(80),
                nullable=False,
                server_default="一键做同款",
            )
        )
        batch_op.create_check_constraint(
            "ck_inspiration_cases_workflow_type",
            "workflow_type IN ('reference_remix', 'product_prompt', "
            "'local_edit', 'legacy_unclassified')",
        )
        batch_op.create_check_constraint(
            "ck_inspiration_cases_reference_source",
            "reference_source IN ('admin_reference', 'result_image', 'none')",
        )
        batch_op.create_check_constraint(
            "ck_inspiration_cases_configuration_status",
            "configuration_status IN ('draft', 'ready', 'incomplete', 'archived')",
        )
    # These five cases are explicitly confirmed for the product-prompt path.
    # All other historical cases remain fail-closed and require admin
    # configuration instead of being silently treated as reference remixes.
    op.execute(
        sa.text(
            "UPDATE inspiration_cases SET workflow_type = 'product_prompt', "
            "reference_source = 'none', "
            "configuration_status = CASE WHEN trim(prompt) <> '' THEN 'ready' "
            "ELSE 'incomplete' END "
            "WHERE title IN ("
            + ", ".join(f"'{title}'" for title in PRODUCT_PROMPT_CASE_TITLES)
            + ")"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("inspiration_cases", recreate="auto") as batch_op:
        batch_op.drop_constraint(
            "ck_inspiration_cases_configuration_status", type_="check"
        )
        batch_op.drop_constraint("ck_inspiration_cases_reference_source", type_="check")
        batch_op.drop_constraint("ck_inspiration_cases_workflow_type", type_="check")
        batch_op.drop_column("button_label")
        batch_op.drop_column("configuration_status")
        batch_op.drop_column("reference_source")
        batch_op.drop_column("workflow_type")
