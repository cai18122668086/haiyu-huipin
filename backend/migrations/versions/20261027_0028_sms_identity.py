"""Persist the phone of independently authenticated ecommerce users."""

import sqlalchemy as sa
from alembic import op

revision = "20261027_0028"
down_revision = "20261021_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone_number", sa.String(24), nullable=True))
    op.create_index("uq_users_phone_number", "users", ["phone_number"], unique=True)
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column(
        "users",
        sa.Column("password_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "password_version")
    op.drop_column("users", "password_hash")
    op.drop_index("uq_users_phone_number", table_name="users")
    op.drop_column("users", "phone_number")
