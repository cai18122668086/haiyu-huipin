from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.credit_memberships import grant
from app.main import create_app
from app.models import ProcessingConsentRecord, WorkspaceCreditAccountRecord
from app.processing_consent import NOTICE, NOTICE_HASH, POLICY_VERSION
from app.sms_auth import override_sms_apis
from tests.test_auth_external import (
    FakeExternalAuthBackend,
    external_settings,
    verified_identity,
)

CHOICE = {
    "policyVersion": POLICY_VERSION,
    "hongKongProcessing": True,
    "aiTraining": True,
}
PATH = "/api/v1/me/processing-consent"


def upload(client, workspace):
    image = BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, "PNG")
    return client.post(
        f"/api/v1/workspaces/{workspace}/assets",
        headers={"Idempotency-Key": "synthetic-upload"},
        data={"origin_kind": "upload", "media_kind": "image"},
        files={"file": ("synthetic.png", image.getvalue(), "image/png")},
    )


def test_existing_session_uploads_without_extra_choice_and_keeps_history(tmp_path):
    backend = FakeExternalAuthBackend(identity=verified_identity())
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        user_id = client.get("/api/v1/me").json()["id"]
        workspace = client.get("/api/v1/workspaces").json()[0]["id"]
        assert client.get(PATH).json()["accepted"] is False
        assert upload(client, workspace).status_code == 201
        assert (
            client.post(
                f"/api/v1/workspaces/{workspace}/jobs/job_missing/cancel",
                json={},
                headers={"Idempotency-Key": "synthetic-cancel"},
            ).status_code
            == 404
        )  # Cancellation reaches the handler without extra confirmation.
        for partial in (
            {},
            {**CHOICE, "aiTraining": False},
            {**CHOICE, "hongKongProcessing": False},
        ):
            assert client.post(PATH, json=partial).status_code in {400, 422}
        assert (
            client.post(PATH, json={**CHOICE, "aiTraining": "true"}).status_code == 422
        )
        assert (
            client.post(PATH, json={**CHOICE, "policyVersion": "old"}).status_code
            == 409
        )
        for _ in range(2):
            assert client.post(PATH, json=CHOICE).json()["accepted"] is True
        with client.app.state.session_factory() as session:
            records = session.scalars(select(ProcessingConsentRecord)).all()
            assert len(records) == 1
            assert records[0].user_id == user_id
            assert records[0].notice == NOTICE
            assert records[0].notice_sha256 == NOTICE_HASH
            assert records[0].choices == CHOICE
        created = upload(client, workspace)
        assert created.status_code == 201, created.text
        assert client.delete(PATH).json()["accepted"] is False
        assert upload(client, workspace).status_code == 201
        assert client.get(f"/api/v1/workspaces/{workspace}/assets").status_code == 200
        assert client.get(f"/api/v1/workspaces/{workspace}/jobs").status_code == 200
        # Existing originals remain downloadable after withdrawal.
        asset = created.json()["id"]
        download = client.post(
            f"/api/v1/workspaces/{workspace}/assets/{asset}/access-url",
            json={"disposition": "attachment", "variant": "original"},
        )
        assert download.status_code == 200
        assert client.get(download.json()["url"]).status_code == 200
        assert client.post(PATH, json=CHOICE).json()["accepted"] is True
        with client.app.state.session_factory() as session:
            assert (
                session.scalar(
                    select(func.count()).select_from(ProcessingConsentRecord)
                )
                == 2
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ProcessingConsentRecord)
                    .where(ProcessingConsentRecord.withdrawn_at.is_not(None))
                )
                == 1
            )


def test_existing_session_creates_jobs_without_extra_confirmation(tmp_path):
    backend = FakeExternalAuthBackend(identity=verified_identity())
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        workspace = client.get("/api/v1/workspaces").json()[0]["id"]
        source = upload(client, workspace)
        assert source.status_code == 201, source.text
        payload = {
            "type": "product_image",
            "intent": "submit",
            "input": {"product_asset_ids": [source.json()["id"]]},
            "configuration": {"task_type": "white_background", "image_count": 1},
        }
        insufficient = client.post(
            f"/api/v1/workspaces/{workspace}/jobs",
            headers={"Idempotency-Key": "synthetic-job-no-credits"},
            json=payload,
        )
        assert insufficient.status_code == 402, insufficient.text
        assert insufficient.json()["error"]["code"] == "BUDGET_LIMIT_EXCEEDED"
        with client.app.state.session_factory() as session:
            account = session.get(WorkspaceCreditAccountRecord, workspace)
            grant(session, account, "synthetic-test", 20, None, "Synthetic test")
            session.commit()
        created = client.post(
            f"/api/v1/workspaces/{workspace}/jobs",
            headers={"Idempotency-Key": "synthetic-job-without-extra-consent"},
            json=payload,
        )
        assert created.status_code == 202, created.text
        assert client.get(PATH).json()["accepted"] is False
        with client.app.state.session_factory() as session:
            assert session.scalars(select(ProcessingConsentRecord)).all() == []


def test_consent_is_scoped_to_authenticated_identity(tmp_path):
    backend = FakeExternalAuthBackend()
    with TestClient(create_app(external_settings(tmp_path), backend)) as client:
        assert client.post(PATH, json=CHOICE).status_code == 401
        backend.identity = verified_identity()
        assert client.post(PATH, json=CHOICE).status_code == 200
        backend.identity = verified_identity(
            primary_user_id="another-subject", email="other@example.com"
        )
        assert client.get(PATH).json()["accepted"] is False


@pytest.mark.parametrize(
    "choice",
    [None, {**CHOICE, "aiTraining": False}, {**CHOICE, "policyVersion": "old"}],
)
def test_registration_requires_only_agreements_before_sms_or_otp(choice):
    import asyncio

    called = []

    async def consume(api_options):
        called.append(True)
        return SimpleNamespace(status="RESTART_FLOW_ERROR")

    async def body():
        return {
            "acceptedAgreements": True,
            "flow": "register",
            "processingConsent": choice,
            "password": "SyntheticPassword123",
        }

    api = override_sms_apis(
        SimpleNamespace(
            create_code_post=consume,
            resend_code_post=consume,
            consume_code_post=consume,
        )
    )
    result = asyncio.run(
        api.consume_code_post(SimpleNamespace(request=SimpleNamespace(json=body)))
    )
    assert result.status == "RESTART_FLOW_ERROR"
    options = SimpleNamespace(request=SimpleNamespace(json=body))
    assert (
        asyncio.run(api.create_code_post(options)).status
        == "RESTART_FLOW_ERROR"
    )
    assert (
        asyncio.run(api.resend_code_post(options)).status
        == "RESTART_FLOW_ERROR"
    )
    assert len(called) == 3


def test_sms_login_and_password_reset_do_not_require_extra_confirmation():
    import asyncio

    called = []

    async def consume(api_options):
        called.append(True)
        return SimpleNamespace(status="RESTART_FLOW_ERROR")

    payload = {"acceptedAgreements": True, "flow": "login", "processingConsent": CHOICE}

    async def body():
        return payload

    api = override_sms_apis(
        SimpleNamespace(
            create_code_post=consume,
            resend_code_post=consume,
            consume_code_post=consume,
        )
    )
    options = SimpleNamespace(request=SimpleNamespace(json=body))
    assert asyncio.run(api.consume_code_post(options)).status == "RESTART_FLOW_ERROR"
    payload.clear()
    payload.update(
        acceptedAgreements=True, flow="reset", password="SyntheticPassword123"
    )
    assert asyncio.run(api.consume_code_post(options)).status == "RESTART_FLOW_ERROR"
    assert len(called) == 2
