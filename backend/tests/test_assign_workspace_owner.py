from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, build_database
from app.domain import MembershipRole, MembershipStatus, UserStatus, WorkspaceStatus
from app.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "assign_workspace_owner.py"
    spec = importlib.util.spec_from_file_location("assign_workspace_owner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load assign_workspace_owner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


owner_script = _load_script()
AssignmentError = owner_script.AssignmentError
assign_workspace_owner = owner_script.assign_workspace_owner
build_parser = owner_script.build_parser


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine, factory = build_database(f"sqlite:///{tmp_path / 'owner-assignment.db'}")
    Base.metadata.create_all(engine)
    try:
        yield factory
    finally:
        engine.dispose()


def _seed_identity_and_workspace(
    factory: sessionmaker[Session],
    *,
    provider: str = "supertokens",
    user_status: str = UserStatus.ACTIVE.value,
) -> None:
    with factory() as session:
        session.add_all(
            [
                UserRecord(
                    id="usr_legacy",
                    auth_provider="migration",
                    auth_subject="legacy",
                    email_normalized=None,
                    display_name="Legacy Owner",
                    status=UserStatus.ACTIVE.value,
                ),
                UserRecord(
                    id="usr_admin",
                    auth_provider=provider,
                    auth_subject="st-primary-user-id",
                    email_normalized="admin@example.com",
                    display_name="Administrator",
                    status=user_status,
                ),
                WorkspaceRecord(
                    id="ws_history",
                    name="Historical Workspace",
                    status=WorkspaceStatus.ACTIVE.value,
                    created_by_user_id="usr_legacy",
                ),
            ]
        )
        session.commit()


def test_creates_owner_membership_using_normalized_email(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_identity_and_workspace(session_factory)

    with session_factory() as session:
        result = assign_workspace_owner(
            session,
            email="  ADMIN@Example.COM ",
            workspace_id="ws_history",
        )

    assert result.action == "created"
    assert result.email_normalized == "admin@example.com"
    assert result.user_id == "usr_admin"
    assert result.previous_role is None
    assert result.previous_status is None

    with session_factory() as session:
        membership = session.get(
            WorkspaceMemberRecord,
            {"workspace_id": "ws_history", "user_id": "usr_admin"},
        )
        workspace = session.get(WorkspaceRecord, "ws_history")
        assert membership is not None
        assert membership.role == MembershipRole.OWNER.value
        assert membership.status == MembershipStatus.ACTIVE.value
        assert workspace is not None
        assert workspace.created_by_user_id == "usr_legacy"


def test_repeated_assignment_is_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_identity_and_workspace(session_factory)

    with session_factory() as session:
        first = assign_workspace_owner(
            session,
            email="admin@example.com",
            workspace_id="ws_history",
        )
    with session_factory() as session:
        second = assign_workspace_owner(
            session,
            email="admin@example.com",
            workspace_id="ws_history",
        )
        membership_count = session.scalar(
            select(func.count()).select_from(WorkspaceMemberRecord)
        )

    assert first.action == "created"
    assert second.action == "unchanged"
    assert second.previous_role == MembershipRole.OWNER.value
    assert second.previous_status == MembershipStatus.ACTIVE.value
    assert membership_count == 1


def test_restores_existing_membership_as_active_owner(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_identity_and_workspace(session_factory)
    with session_factory() as session:
        session.add(
            WorkspaceMemberRecord(
                workspace_id="ws_history",
                user_id="usr_admin",
                role=MembershipRole.MEMBER.value,
                status=MembershipStatus.SUSPENDED.value,
            )
        )
        session.commit()

    with session_factory() as session:
        result = assign_workspace_owner(
            session,
            email="admin@example.com",
            workspace_id="ws_history",
        )

    assert result.action == "restored"
    assert result.previous_role == MembershipRole.MEMBER.value
    assert result.previous_status == MembershipStatus.SUSPENDED.value
    with session_factory() as session:
        membership = session.get(
            WorkspaceMemberRecord,
            {"workspace_id": "ws_history", "user_id": "usr_admin"},
        )
        assert membership is not None
        assert membership.role == MembershipRole.OWNER.value
        assert membership.status == MembershipStatus.ACTIVE.value


def test_refuses_identity_not_mapped_by_supertokens(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_identity_and_workspace(session_factory, provider="development")

    with session_factory() as session, pytest.raises(
        AssignmentError,
        match=r"no SuperTokens JIT user found.*provider\(s\): development",
    ):
        assign_workspace_owner(
            session,
            email="admin@example.com",
            workspace_id="ws_history",
        )


def test_refuses_inactive_user(session_factory: sessionmaker[Session]) -> None:
    _seed_identity_and_workspace(
        session_factory,
        user_status=UserStatus.SUSPENDED.value,
    )

    with session_factory() as session, pytest.raises(
        AssignmentError,
        match=r"is not active.*suspended",
    ):
        assign_workspace_owner(
            session,
            email="admin@example.com",
            workspace_id="ws_history",
        )


def test_refuses_unknown_workspace(session_factory: sessionmaker[Session]) -> None:
    _seed_identity_and_workspace(session_factory)

    with session_factory() as session, pytest.raises(
        AssignmentError,
        match="workspace 'ws_missing' was not found",
    ):
        assign_workspace_owner(
            session,
            email="admin@example.com",
            workspace_id="ws_missing",
        )


def test_cli_requires_explicit_workspace_id() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--email", "admin@example.com"])

    assert exc_info.value.code == 2
