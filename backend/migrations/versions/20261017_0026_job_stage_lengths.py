"""Record the production stage expansion without narrowing existing data.

Production applied this DDL before b8cd247 updated the models. Both a fresh
0025 database (32) and that existing production schema (64) can advance here.
Application rollback keeps the compatible, wider columns in place.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261017_0026"
down_revision = "20260930_0025"
branch_labels = None
depends_on = None

TABLES = ("jobs", "job_items", "crawl_tasks")


def upgrade() -> None:
    if op.get_context().as_sql:
        for table in TABLES:
            op.alter_column(
                table, "stage", existing_type=sa.String(32), type_=sa.String(64)
            )
        return

    inspector = sa.inspect(op.get_bind())
    lengths = {}
    # Check every table before changing one: never narrow an unexpected schema.
    for table in TABLES:
        column = next(
            item for item in inspector.get_columns(table) if item["name"] == "stage"
        )
        kind = column["type"]
        if not isinstance(kind, sa.String) or kind.length not in (32, 64):
            raise RuntimeError(f"Unexpected {table}.stage type: {kind}")
        lengths[table] = kind.length

    for table, length in lengths.items():
        if length == 32:
            op.alter_column(
                table, "stage", existing_type=sa.String(32), type_=sa.String(64)
            )


def downgrade() -> None:
    raise RuntimeError(
        "Keep stage columns at 64 for application rollback; shrinking them can "
        "truncate reconciliation states. Database downgrade requires review."
    )
