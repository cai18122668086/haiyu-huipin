from __future__ import annotations

from hashlib import sha256

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .domain import MembershipRole, MembershipStatus, WorkspaceStatus
from .models import (
    UserRecord,
    WorkspaceCreditAccountRecord,
    WorkspaceLimitRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
)

PERSONAL_WORKSPACE_INITIAL_CREDITS = 0
_MAX_PROVISION_ATTEMPTS = 3


def personal_workspace_id(user_id: str) -> str:
    digest = sha256(user_id.encode("utf-8")).hexdigest()[:24]
    return f"ws_personal_{digest}"


def _personal_workspace_name(user: UserRecord) -> str:
    if user.display_name and user.display_name.strip():
        owner_label = user.display_name.strip()
    elif user.email_normalized:
        owner_label = user.email_normalized.split("@", 1)[0]
    else:
        owner_label = "我"
    return f"{owner_label}的工作区"[:120]


def ensure_personal_workspace(session: Session, user: UserRecord) -> WorkspaceRecord:
    """Create the public-signup workspace graph once, including zero credits."""

    workspace_id = personal_workspace_id(user.id)
    for attempt in range(_MAX_PROVISION_ATTEMPTS):
        try:
            with session.begin_nested():
                workspace = session.get(WorkspaceRecord, workspace_id)
                if workspace is None:
                    workspace = WorkspaceRecord(
                        id=workspace_id,
                        name=_personal_workspace_name(user),
                        status=WorkspaceStatus.ACTIVE.value,
                        created_by_user_id=user.id,
                    )
                    session.add(workspace)
                    session.flush()

                if session.get(WorkspaceMemberRecord, (workspace_id, user.id)) is None:
                    session.add(
                        WorkspaceMemberRecord(
                            workspace_id=workspace_id,
                            user_id=user.id,
                            role=MembershipRole.OWNER.value,
                            status=MembershipStatus.ACTIVE.value,
                        )
                    )
                if session.get(WorkspaceLimitRecord, workspace_id) is None:
                    session.add(WorkspaceLimitRecord(workspace_id=workspace_id))
                if session.get(WorkspaceCreditAccountRecord, workspace_id) is None:
                    session.add(
                        WorkspaceCreditAccountRecord(
                            workspace_id=workspace_id,
                            balance_credits=PERSONAL_WORKSPACE_INITIAL_CREDITS,
                            reserved_credits=0,
                            version=0,
                        )
                    )
                session.flush()
                return workspace
        except IntegrityError:
            session.expire_all()
            if attempt + 1 == _MAX_PROVISION_ATTEMPTS:
                raise

    raise RuntimeError("personal workspace provisioning attempts exhausted")
