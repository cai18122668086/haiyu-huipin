"""Allow complete plan protocol identifiers without changing their identity."""

import sqlalchemy as sa
from alembic import op

revision = "20261030_0035"
down_revision = "20261029_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not op.get_context().as_sql:
        column = next(
            item
            for item in sa.inspect(op.get_bind()).get_columns("job_plans")
            if item["name"] == "schema_version"
        )
        kind = column["type"]
        if not isinstance(kind, sa.String) or kind.length not in (32, 64):
            raise RuntimeError(f"Unexpected job_plans.schema_version type: {kind}")
        if kind.length == 64:
            return
    with op.batch_alter_table("job_plans") as batch:
        batch.alter_column(
            "schema_version",
            existing_type=sa.String(32),
            type_=sa.String(64),
            existing_nullable=False,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Keep plan schema identifiers at 64 for application rollback; "
        "shrinking them can lose existing plans."
    )
