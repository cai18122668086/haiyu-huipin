from datetime import UTC, datetime, timedelta

import pytest

from app.domain import MembershipRole, MembershipStatus, UserStatus
from app.models import UserRecord, WorkspaceMemberRecord

ANNOUNCEMENT_ADMIN_EMAIL = "admin@example.com"


@pytest.fixture(autouse=True)
def configure_announcement_admin(client):
    client.app.state.settings.announcement_admin_emails = [ANNOUNCEMENT_ADMIN_EMAIL]
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        user = session.get(UserRecord, "usr_test")
        assert user is not None
        user.email_normalized = ANNOUNCEMENT_ADMIN_EMAIL
        session.commit()
    yield


def announcement_payload(**overrides):
    payload = {
        "title": "维护通知",
        "summary": "今晚会有短暂维护",
        "content": "服务将在今晚进行维护。",
        "kind": "maintenance",
        "is_pinned": False,
    }
    payload.update(overrides)
    return payload


def create_and_publish(client, payload):
    created = client.post("/api/v1/admin/announcements", json=payload)
    assert created.status_code == 201, created.text
    announcement_id = created.json()["id"]
    published = client.post(f"/api/v1/admin/announcements/{announcement_id}/publish")
    assert published.status_code == 200, published.text
    return published.json()


def test_draft_is_hidden_until_published(client):
    created = client.post("/api/v1/admin/announcements", json=announcement_payload())
    assert created.status_code == 201
    assert created.json()["status"] == "draft"
    assert client.get("/api/v1/announcements/current").json() is None

    published = client.post(
        f"/api/v1/admin/announcements/{created.json()['id']}/publish"
    )
    assert published.status_code == 200
    assert (
        client.get("/api/v1/announcements/current").json()["id"] == created.json()["id"]
    )


def test_public_endpoint_filters_schedule_and_prioritizes_pinned(client):
    now = datetime.now(UTC)
    create_and_publish(
        client,
        announcement_payload(
            title="稍后开始",
            starts_at=(now + timedelta(hours=1)).isoformat(),
        ),
    )
    create_and_publish(client, announcement_payload(title="普通公告"))
    pinned = create_and_publish(
        client, announcement_payload(title="置顶公告", is_pinned=True)
    )
    expired = create_and_publish(
        client,
        announcement_payload(
            title="已过期",
            ends_at=(now - timedelta(minutes=1)).isoformat(),
        ),
    )

    response = client.get("/api/v1/announcements")
    assert response.status_code == 200
    titles = [item["title"] for item in response.json()["items"]]
    assert titles[0] == "置顶公告"
    assert "稍后开始" not in titles
    assert "已过期" not in titles
    assert pinned["id"] in [item["id"] for item in response.json()["items"]]
    assert expired["id"] not in [item["id"] for item in response.json()["items"]]


def test_admin_can_edit_publish_and_archive(client):
    assert client.get("/api/v1/admin/announcements/access").json() == {
        "can_manage": True
    }
    created = client.post("/api/v1/admin/announcements", json=announcement_payload())
    announcement_id = created.json()["id"]
    updated = client.patch(
        f"/api/v1/admin/announcements/{announcement_id}",
        json={"title": "更新后的公告", "is_pinned": True},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "更新后的公告"
    assert updated.json()["is_pinned"] is True

    assert (
        client.post(
            f"/api/v1/admin/announcements/{announcement_id}/publish"
        ).status_code
        == 200
    )
    archived = client.post(f"/api/v1/admin/announcements/{announcement_id}/archive")
    assert archived.status_code == 200
    assert client.get("/api/v1/announcements/current").json() is None


def test_non_admin_cannot_manage_announcements(client):
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        session.add(
            UserRecord(
                id="usr_member",
                auth_provider="development",
                auth_subject="usr_member",
                email_normalized=ANNOUNCEMENT_ADMIN_EMAIL,
                display_name="Member",
                status=UserStatus.ACTIVE.value,
            )
        )
        session.add(
            WorkspaceMemberRecord(
                workspace_id="ws_test",
                user_id="usr_member",
                role=MembershipRole.MEMBER.value,
                status=MembershipStatus.ACTIVE.value,
            )
        )
        session.commit()

    headers = {"X-User-Id": "usr_member"}
    assert client.get("/api/v1/admin/announcements/access", headers=headers).json() == {
        "can_manage": False
    }
    response = client.post(
        "/api/v1/admin/announcements",
        headers=headers,
        json=announcement_payload(),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ANNOUNCEMENT_ADMIN_REQUIRED"


def test_personal_workspace_owner_outside_allowlist_cannot_manage(client):
    session_factory = client.app.state.session_factory
    with session_factory() as session:
        session.add(
            UserRecord(
                id="usr_unlisted_owner",
                auth_provider="development",
                auth_subject="usr_unlisted_owner",
                email_normalized="other@example.com",
                display_name="Unlisted owner",
                status=UserStatus.ACTIVE.value,
            )
        )
        session.add(
            WorkspaceMemberRecord(
                workspace_id="ws_test",
                user_id="usr_unlisted_owner",
                role=MembershipRole.OWNER.value,
                status=MembershipStatus.ACTIVE.value,
            )
        )
        session.commit()

    headers = {"X-User-Id": "usr_unlisted_owner"}
    assert client.get(
        "/api/v1/admin/announcements/access",
        headers=headers,
    ).json() == {"can_manage": False}
    response = client.post(
        "/api/v1/admin/announcements",
        headers=headers,
        json=announcement_payload(),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ANNOUNCEMENT_ADMIN_REQUIRED"

    created = client.post(
        "/api/v1/admin/announcements",
        json=announcement_payload(),
    ).json()
    protected_requests = (
        client.get("/api/v1/admin/announcements", headers=headers),
        client.patch(
            f"/api/v1/admin/announcements/{created['id']}",
            headers=headers,
            json={"title": "Forbidden update"},
        ),
        client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish",
            headers=headers,
        ),
        client.post(
            f"/api/v1/admin/announcements/{created['id']}/archive",
            headers=headers,
        ),
    )
    for protected_response in protected_requests:
        assert protected_response.status_code == 403
        assert (
            protected_response.json()["error"]["code"]
            == "ANNOUNCEMENT_ADMIN_REQUIRED"
        )


def test_invalid_content_and_time_window_are_rejected(client):
    empty = client.post(
        "/api/v1/admin/announcements",
        json=announcement_payload(title="   ", content="正文"),
    )
    assert empty.status_code == 422

    now = datetime.now(UTC)
    invalid_window = client.post(
        "/api/v1/admin/announcements",
        json=announcement_payload(
            starts_at=now.isoformat(),
            ends_at=(now - timedelta(minutes=1)).isoformat(),
        ),
    )
    assert invalid_window.status_code == 422
