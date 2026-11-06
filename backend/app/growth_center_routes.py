from typing import Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from . import growth_center as center
from .auth import SessionDependency, WorkspaceDependency, WorkspaceWriteDependency

router = APIRouter(tags=["growth center"])


class InvitationFriend(BaseModel):
    id: str
    friend: str
    registered_at: str
    payment_status: Literal["unpaid", "paid", "refunded"]
    last_paid_at: str | None
    earned_credits: int


class InvitationCenter(BaseModel):
    invite_code: str
    invite_url: str
    invited_count: int
    paid_count: int
    unpaid_count: int
    refunded_count: int
    earned_credits: int
    reward_percent: int
    validity_days: int
    total: int
    offset: int
    limit: int
    items: list[InvitationFriend]


class NewcomerOffer(BaseModel):
    eligible: bool
    trial_viewed: bool
    should_show: bool
    server_now: str
    ready_at: str | None
    window_ends_at: str | None
    amount_fen: int
    regular_amount_fen: int
    savings_fen: int
    credits: int
    fast_images: int
    validity_days: int


class ViewedTrial(BaseModel):
    session_id: str = Field(min_length=1, max_length=40)


@router.get(
    "/api/v1/workspaces/{workspace_id}/invitations", response_model=InvitationCenter
)
def invitations(
    workspace: WorkspaceDependency,
    session: SessionDependency,
    response: Response,
    status: Literal["all", "unpaid", "paid", "refunded"] = "all",
    offset: int = Query(0, ge=0, le=1000000),
    limit: int = Query(20, ge=1, le=100),
) -> InvitationCenter:
    response.headers["Cache-Control"] = "no-store"
    user = center.personal_user(
        session, workspace.principal.user_id, workspace.workspace_id
    )
    result = center.invitation_center(session, user, offset, limit, status)
    session.commit()
    return InvitationCenter.model_validate(result)


@router.get(
    "/api/v1/workspaces/{workspace_id}/promotions/newcomer-offer",
    response_model=NewcomerOffer,
)
def newcomer_offer(
    workspace: WorkspaceDependency, session: SessionDependency, response: Response
) -> NewcomerOffer:
    response.headers["Cache-Control"] = "no-store"
    user = center.personal_user(
        session, workspace.principal.user_id, workspace.workspace_id
    )
    return NewcomerOffer.model_validate(
        center.offer_state(session, user, workspace.workspace_id)
    )


@router.post(
    "/api/v1/workspaces/{workspace_id}/promotions/newcomer-offer/viewed",
    response_model=NewcomerOffer,
)
def trial_viewed(
    payload: ViewedTrial,
    workspace: WorkspaceWriteDependency,
    session: SessionDependency,
    response: Response,
) -> NewcomerOffer:
    response.headers["Cache-Control"] = "no-store"
    user = center.personal_user(
        session, workspace.principal.user_id, workspace.workspace_id, lock=True
    )
    result = center.mark_trial_viewed(
        session, user, workspace.workspace_id, payload.session_id
    )
    session.commit()
    return NewcomerOffer.model_validate(result)


@router.post(
    "/api/v1/workspaces/{workspace_id}/promotions/newcomer-offer/impression",
    response_model=NewcomerOffer,
)
def impression(
    workspace: WorkspaceWriteDependency, session: SessionDependency, response: Response
) -> NewcomerOffer:
    response.headers["Cache-Control"] = "no-store"
    user = center.personal_user(
        session, workspace.principal.user_id, workspace.workspace_id, lock=True
    )
    result = center.claim_impression(session, user, workspace.workspace_id)
    session.commit()
    return NewcomerOffer.model_validate(result)
