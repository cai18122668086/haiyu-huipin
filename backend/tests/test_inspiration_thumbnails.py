from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image

from app.models import AssetRecord, InspirationCaseAssetRecord, InspirationCaseRecord
from app.thumbnails import ThumbnailBusy


def published_picture(client):
    output = BytesIO()
    Image.effect_noise((1280, 960), 70).convert("RGB").save(output, "JPEG", quality=92)
    original = output.getvalue()
    response = client.post(
        "/api/v1/workspaces/ws_test/assets",
        headers={"Idempotency-Key": str(uuid4())},
        data={"origin_kind": "upload", "media_kind": "image"},
        files={"file": ("source.jpg", original, "image/jpeg")},
    )
    assert response.status_code == 201
    asset_id = response.json()["id"]
    with client.app.state.session_factory() as session:
        case = InspirationCaseRecord(
            id="ins_thumbnail",
            title="Preview",
            description="Preview test",
            role="product",
            type="scene",
            category="test",
            platform="国内",
            ratio="4:3",
            prompt="Product photo",
            intent="Product photo",
            workflow_type="product_prompt",
            reference_source="none",
            configuration_status="ready",
            button_label="Generate",
            status="published",
            sort_order=0,
            is_pinned=False,
            published_at=datetime.now(UTC),
            created_by_user_id="usr_test",
            updated_by_user_id="usr_test",
        )
        session.add(case)
        session.flush()
        session.add(
            InspirationCaseAssetRecord(
                id="ins_asset_thumbnail",
                case_id=case.id,
                asset_id=asset_id,
                role="result",
                ordinal=0,
            )
        )
        session.commit()
    return (
        f"/api/v1/inspiration/cases/ins_thumbnail/assets/{asset_id}",
        original,
        asset_id,
    )


def test_public_preview_is_small_cached_and_original_remains_unchanged(
    client, monkeypatch
):
    url, original, _ = published_picture(client)
    preview = client.get(url, params={"variant": "thumbnail"})
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/webp"
    with Image.open(BytesIO(preview.content)) as image:
        assert image.size == (480, 360)
    assert len(preview.content) < len(original) / 4
    assert client.get(url).content == original

    async def no_read(_key):
        pytest.fail("warm preview must not reread the original")

    monkeypatch.setattr(client.app.state.storage, "read_bytes", no_read)
    assert client.get(url, params={"variant": "thumbnail"}).content == preview.content
    assert (
        client.get(
            url,
            params={"variant": "thumbnail"},
            headers={"If-None-Match": preview.headers["etag"]},
        ).status_code
        == 304
    )
    assert client.get(url, params={"variant": "unexpected"}).status_code == 422


def test_same_origin_catalog_and_signed_assets_can_be_copied_through_proxy(client):
    url, original, asset_id = published_picture(client)
    client.app.state.settings.asset_access_url_mode = "same_origin"
    case = client.get("/api/v1/inspiration/cases").json()["items"][0]
    assert case["gallery"][0]["url"] == url
    assert client.get(case["gallery"][0]["url"]).content == original
    access = client.post(
        f"/api/v1/workspaces/ws_test/assets/{asset_id}/access-url",
        json={"disposition": "inline", "variant": "original"},
    )
    assert access.status_code == 200
    assert access.json()["url"].startswith("/api/v1/")
    assert client.get(access.json()["url"]).content == original


@pytest.mark.parametrize("unavailable", ["unpublished", "deleted"])
def test_preview_checks_publication_and_asset_access_before_conditional_cache(
    client, unavailable
):
    url, _, asset_id = published_picture(client)
    preview = client.get(url, params={"variant": "thumbnail"})
    with client.app.state.session_factory() as session:
        if unavailable == "unpublished":
            session.get(InspirationCaseRecord, "ins_thumbnail").status = "draft"
        else:
            session.get(AssetRecord, asset_id).deleted_at = datetime.now(UTC)
        session.commit()
    assert (
        client.get(
            url,
            params={"variant": "thumbnail"},
            headers={"If-None-Match": preview.headers["etag"]},
        ).status_code
        == 404
    )


def test_busy_preview_does_not_poison_public_cache_or_change_original(
    client, monkeypatch
):
    url, original, _ = published_picture(client)

    async def busy(*_args):
        raise ThumbnailBusy

    monkeypatch.setattr(client.app.state.thumbnails, "get", busy)
    response = client.get(url, params={"variant": "thumbnail"})
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "3"
    assert client.get(url).content == original
