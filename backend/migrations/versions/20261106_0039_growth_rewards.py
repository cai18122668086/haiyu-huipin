"""Add daily check-ins and single-level referral credit accounting, disabled first."""

import sqlalchemy as sa
from alembic import op

revision = "20261106_0039"
down_revision = "20261106_0038"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def fk(name, target, *, primary=False, nullable=False, unique=False):
    return sa.Column(
        name,
        sa.String(40),
        sa.ForeignKey(target, ondelete="RESTRICT"),
        primary_key=primary,
        nullable=nullable,
        unique=unique,
    )


def upgrade():
    op.create_table(
        "daily_checkins",
        fk("user_id", "users.id", primary=True),
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("phone_key", sa.String(64), nullable=False),
        fk("grant_id", "credit_grants.id", unique=True),
        sa.Column("net_credits", sa.BigInteger(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("phone_key", "day", name="uq_checkin_phone_day"),
    )
    op.create_table(
        "invitation_codes",
        fk("user_id", "users.id", primary=True),
        sa.Column("code", sa.String(16), unique=True, nullable=False),
        *timestamps(),
    )
    op.create_table(
        "invitations",
        fk("invitee_user_id", "users.id", primary=True),
        fk("inviter_user_id", "users.id"),
        sa.Column("phone_key", sa.String(64), unique=True, nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        *timestamps(),
    )
    op.create_index(
        "ix_invitations_inviter_user_id", "invitations", ["inviter_user_id"]
    )
    op.create_table(
        "growth_reward_adjustments",
        fk("user_id", "users.id", primary=True),
        sa.Column("pending_credits", sa.BigInteger(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "pending_credits >= 0", name="ck_growth_adjustment_nonnegative"
        ),
    )
    op.create_table(
        "referral_rewards",
        fk("source_grant_id", "credit_grants.id", primary=True),
        fk("purchase_id", "credit_purchases.id"),
        fk("inviter_user_id", "users.id"),
        fk("invitee_user_id", "users.id"),
        fk("bonus_grant_id", "credit_grants.id", nullable=True, unique=True),
        *[
            sa.Column(name, sa.BigInteger(), nullable=False)
            for name in [
                "amount",
                "revoked",
                "consumed",
                "debt_created",
                "source_revoked",
            ]
        ],
        *timestamps(),
        sa.CheckConstraint(
            "amount >= 0 AND revoked >= 0 AND revoked <= amount "
            "AND consumed >= 0 AND debt_created >= 0 AND source_revoked >= 0",
            name="ck_referral_reward_amounts",
        ),
    )
    op.create_index(
        "ix_referral_rewards_purchase_id", "referral_rewards", ["purchase_id"]
    )
    op.create_index(
        "ix_referral_rewards_inviter_user_id", "referral_rewards", ["inviter_user_id"]
    )
    op.create_table(
        "referral_reward_reversals",
        fk("refund_id", "credit_refunds.id", primary=True),
        fk("source_grant_id", "referral_rewards.source_grant_id", primary=True),
        sa.Column("source_credits", sa.BigInteger(), nullable=False),
        *timestamps(),
    )
    op.execute(
        sa.text(
            "INSERT INTO promotion_campaigns (id, enabled, starts_at) "
            "VALUES ('daily-checkin-v1', false, CURRENT_TIMESTAMP), "
            "('referral-credits-v1', false, CURRENT_TIMESTAMP)"
        )
    )


def downgrade():
    raise RuntimeError(
        "Retain reward attribution, credit and adjustment history on rollback."
    )
