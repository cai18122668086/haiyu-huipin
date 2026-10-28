"""Assign an existing SuperTokens user as owner of one explicit workspace.

This is an operator-only data repair tool. It intentionally requires a workspace ID
and only changes the matching row in ``workspace_members``. Historical ownership
fields on workspaces, assets, jobs, or other records are never rewritten.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import build_database
from app.domain import MembershipRole, MembershipStatus, UserStatus, WorkspaceStatus
from app.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord

SUPERTOKENS_AUTH_PROVIDER = "supertokens"


class AssignmentError(RuntimeError):
    """Raised when the requested owner assignment is unsafe or impossible."""


@dataclass(frozen=True)
class AssignmentResult:
    action: Literal["created", "restored", "unchanged"]
    email_normalized: str
    user_id: str
    workspace_id: str
    workspace_name: str
    previous_role: str | None
    previous_status: str | None


def normalize_email(email: str) -> str:
    """Normalize an operator-provided email the same way as JIT identity lookup."""

    normalized = email.strip().casefold()
    if not normalized or "@" not in normalized or len(normalized) > 320:
        raise AssignmentError("email must be a valid non-empty address")
    return normalized


def _find_supertokens_user(session: Session, email_normalized: str) -> UserRecord:
    users = list(
        session.scalars(
            select(UserRecord).where(
                UserRecord.auth_provider == SUPERTOKENS_AUTH_PROVIDER,
                UserRecord.email_normalized == email_normalized,
            )
        )
    )
    if len(users) > 1:
        user_ids = ", ".join(sorted(user.id for user in users))
        raise AssignmentError(
            "multiple SuperTokens users share normalized email "
            f"{email_normalized!r} (user IDs: {user_ids}); resolve the duplicate "
            "before assigning workspace ownership"
        )
    if not users:
        providers = sorted(
            set(
                session.scalars(
                    select(UserRecord.auth_provider).where(
                        UserRecord.email_normalized == email_normalized
                    )
                )
            )
        )
        if providers:
            provider_list = ", ".join(providers)
            raise AssignmentError(
                f"no SuperTokens JIT user found for {email_normalized!r}; "
                f"the email only exists under provider(s): {provider_list}"
            )
        raise AssignmentError(
            f"no SuperTokens JIT user found for {email_normalized!r}; "
            "the administrator must complete sign-in first"
        )

    user = users[0]
    if user.status != UserStatus.ACTIVE.value:
        raise AssignmentError(
            f"SuperTokens user {user.id!r} is not active (status={user.status!r})"
        )
    return user


def assign_workspace_owner(
    session: Session,
    *,
    email: str,
    workspace_id: str,
) -> AssignmentResult:
    """Idempotently create or restore one active owner membership."""

    normalized_email = normalize_email(email)
    normalized_workspace_id = workspace_id.strip()
    if not normalized_workspace_id:
        raise AssignmentError("workspace ID must not be empty")

    user = _find_supertokens_user(session, normalized_email)
    workspace = session.get(WorkspaceRecord, normalized_workspace_id)
    if workspace is None:
        raise AssignmentError(f"workspace {normalized_workspace_id!r} was not found")
    if (
        workspace.status == WorkspaceStatus.DELETED.value
        or workspace.deleted_at is not None
    ):
        raise AssignmentError(
            f"workspace {normalized_workspace_id!r} is deleted; "
            "ownership was not changed"
        )

    membership = session.get(
        WorkspaceMemberRecord,
        {
            "workspace_id": normalized_workspace_id,
            "user_id": user.id,
        },
    )
    if membership is None:
        previous_role = None
        previous_status = None
        action: Literal["created", "restored", "unchanged"] = "created"
        membership = WorkspaceMemberRecord(
            workspace_id=normalized_workspace_id,
            user_id=user.id,
            role=MembershipRole.OWNER.value,
            status=MembershipStatus.ACTIVE.value,
        )
        session.add(membership)
    else:
        previous_role = membership.role
        previous_status = membership.status
        if (
            membership.role == MembershipRole.OWNER.value
            and membership.status == MembershipStatus.ACTIVE.value
        ):
            action = "unchanged"
        else:
            membership.role = MembershipRole.OWNER.value
            membership.status = MembershipStatus.ACTIVE.value
            action = "restored"

    try:
        session.commit()
    except Exception:
        session.rollback()
        raise

    return AssignmentResult(
        action=action,
        email_normalized=normalized_email,
        user_id=user.id,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        previous_role=previous_role,
        previous_status=previous_status,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Assign one existing SuperTokens JIT user as the active owner of one "
            "explicit workspace. This command never grants access to every workspace."
        )
    )
    parser.add_argument(
        "--email",
        required=True,
        help="administrator email already mapped by SuperTokens JIT sign-in",
    )
    parser.add_argument(
        "--workspace-id",
        required=True,
        help="exact workspace ID to grant; intentionally required for safety",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    engine, session_factory = build_database(settings.database_url)
    try:
        with session_factory() as session:
            result = assign_workspace_owner(
                session,
                email=args.email,
                workspace_id=args.workspace_id,
            )
    except AssignmentError as exc:
        print(f"owner assignment refused: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(
            f"owner assignment failed due to a database error: {exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        engine.dispose()

    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
