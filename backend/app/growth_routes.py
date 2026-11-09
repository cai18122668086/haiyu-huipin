from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from . import growth_rewards
from .auth import SessionDependency, WorkspaceDependency, WorkspaceWriteDependency
from .errors import ApiError
from .models import UserRecord
from .onboarding import personal_workspace_id

router = APIRouter(tags=["account rewards"])


class RewardEvent(BaseModel):
    kind: Literal["checkin", "referral"]
    title: str
    credits: int
    created_at: str
    adjusted: bool


class RewardsState(BaseModel):
    checkin_enabled: bool
    referral_enabled: bool
    checked_in: bool
    daily_credits: int
    reward_percent: int
    validity_days: int
    checkin_validity_hours: int
    checkin_expires_at: str | None
    day: str
    next_day_at: str
    invite_url: str | None
    invited_count: int
    earned_credits: int
    pending_adjustment_credits: int
    recent: list[RewardEvent]


class CheckinResult(BaseModel):
    created: bool
    credited_credits: int
    adjusted_credits: int
    rewards: RewardsState


@router.get("/api/v1/workspaces/{workspace_id}/rewards", response_model=RewardsState)
def rewards_state(
    workspace: WorkspaceDependency, session: SessionDependency, response: Response
) -> RewardsState:
    response.headers["Cache-Control"] = "no-store"
    user = session.get(UserRecord, workspace.principal.user_id)
    if not user:
        raise ApiError(404, "ACCOUNT_NOT_FOUND", "账号不存在。")
    result = growth_rewards.state(session, user, workspace.workspace_id)
    session.commit()
    return RewardsState.model_validate(result)


@router.post(
    "/api/v1/workspaces/{workspace_id}/rewards/check-in", response_model=CheckinResult
)
def claim_checkin(
    workspace: WorkspaceWriteDependency, session: SessionDependency, response: Response
) -> CheckinResult:
    response.headers["Cache-Control"] = "no-store"
    user = session.get(UserRecord, workspace.principal.user_id)
    if not user or workspace.workspace_id != personal_workspace_id(user.id):
        raise ApiError(403, "PERSONAL_REWARDS_REQUIRED", "请在自己的个人工作区领取。")
    try:
        receipt, created = growth_rewards.checkin(session, user)
        credited = receipt.net_credits if receipt and created else 0
        result = growth_rewards.state(session, user, workspace.workspace_id)
        session.commit()
    except IntegrityError:
        session.rollback()
        user = session.get(UserRecord, workspace.principal.user_id)
        assert user is not None
        result = growth_rewards.state(session, user, workspace.workspace_id)
        if not result["checked_in"]:
            raise
        session.commit()
        created, credited = False, 0
    return CheckinResult(
        created=created,
        credited_credits=credited,
        adjusted_credits=growth_rewards.DAILY_CREDITS - credited if created else 0,
        rewards=RewardsState.model_validate(result),
    )
