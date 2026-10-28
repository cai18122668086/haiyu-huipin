"""Persist workspace product imports and reference-remix plans."""

import sqlalchemy as sa
from alembic import op

revision = "20261021_0027"
down_revision = "20261017_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "replica_documents",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "kind", "fingerprint", name="uq_replica_document_intent"
        ),
    )
    op.create_index(
        "ix_replica_workspace_created",
        "replica_documents",
        ["workspace_id", "created_at"],
    )


def downgrade():
    raise RuntimeError(
        "Preserve replica plans and imports during application rollback."
    )
