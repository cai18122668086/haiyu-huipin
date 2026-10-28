from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import InspirationCaseRecord, UserRecord
from tests.test_auth_external import FakeExternalAuthBackend, external_settings


def test_guests_can_read_published_catalog_but_not_private_business_data(
    tmp_path: Path,
) -> None:
    backend = FakeExternalAuthBackend()
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        with client.app.state.session_factory() as session:
            session.add(
                UserRecord(
                    id="publisher",
                    auth_provider="test",
                    auth_subject="publisher",
                    status="active",
                )
            )
            session.flush()
            for state in ["published", "draft", "archived"]:
                session.add(
                    InspirationCaseRecord(
                        id=f"case_{state}",
                        title=f"Case {state}",
                        status=state,
                        description="Public product artwork",
                        role="主图",
                        type="卖点图",
                        category="日用品",
                        platform="国内",
                        ratio="1:1",
                        intent="公开展示",
                        prompt="A public example prompt",
                        workflow_type="product_prompt",
                        reference_source="none",
                        configuration_status="ready",
                        created_by_user_id="publisher",
                        updated_by_user_id="publisher",
                    )
                )
            session.commit()
        catalog = client.get("/api/v1/inspiration/cases")
        assert catalog.status_code == 200
        assert [item["id"] for item in catalog.json()["items"]] == ["case_published"]
        assert client.get("/api/v1/inspiration/cases/case_published").status_code == 200
        for state in ["draft", "archived"]:
            assert (
                client.get(f"/api/v1/inspiration/cases/case_{state}").status_code == 404
            )
        assert client.get("/api/v1/announcements").status_code == 200
        for path in [
            "/api/v1/me",
            "/api/v1/workspaces",
            "/api/v1/runtime",
            "/api/v1/admin/inspiration/cases/access",
            "/api/v1/workspaces/private/assets",
        ]:
            assert client.get(path).status_code == 401, path
        assert (
            client.post("/api/v1/workspaces/private/jobs", json={}).status_code == 401
        )
