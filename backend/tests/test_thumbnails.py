import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from PIL import Image

from app.models import AssetRecord
from app.thumbnails import ThumbnailBusy, ThumbnailCache, render_thumbnail


def upload_picture(client):
    picture = Image.effect_noise((1280, 960), 70).convert("RGB")
    output = BytesIO()
    picture.save(output, "JPEG", quality=92)
    original = output.getvalue()
    response = client.post(
        "/api/v1/workspaces/ws_test/assets",
        headers={"Idempotency-Key": str(uuid4())},
        data={"origin_kind": "upload", "media_kind": "image"},
        files={"file": ("original.jpg", original, "image/jpeg")},
    )
    assert response.status_code == 201
    return response.json()["id"], original


def change_query(url, **changes):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    query.update({key: [value] for key, value in changes.items()})
    return urlunsplit(parts._replace(query=urlencode(query, doseq=True)))


def test_thumbnail_is_small_private_and_keeps_original(client, monkeypatch):
    asset_id, original = upload_picture(client)
    endpoint = f"/api/v1/workspaces/ws_test/assets/{asset_id}/access-url"
    access = client.post(endpoint, json={"variant": "thumbnail"})
    assert access.status_code == 200
    data = access.json()
    response = client.get(data["url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "private, max-age=60"
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (480, 360)
    assert len(response.content) < len(original) / 4
    assert client.get(data["original_url"]).content == original
    # Original URLs minted by the previous release remain valid.
    parts = urlsplit(data["original_url"])
    query = parse_qs(parts.query)
    del query["variant"]
    legacy_url = urlunsplit(parts._replace(query=urlencode(query, doseq=True)))
    assert client.get(legacy_url).content == original

    async def should_not_read(_key):
        pytest.fail("a cached thumbnail must not reread the original")

    monkeypatch.setattr(client.app.state.storage, "read_bytes", should_not_read)
    assert client.get(data["url"]).content == response.content
    cached = client.get(
        data["url"], headers={"If-None-Match": response.headers["etag"]}
    )
    assert cached.status_code == 304
    assert cached.content == b""
    assert client.get(change_query(data["url"], variant="original")).status_code == 403
    assert (
        client.get(change_query(data["original_url"], variant="thumbnail")).status_code
        == 403
    )
    assert client.get(change_query(data["url"], expires="1")).status_code == 403
    assert (
        client.post(
            endpoint.replace("ws_test", "ws_other"), json={"variant": "thumbnail"}
        ).status_code
        == 404
    )
    # Expiry checks happen before cache lookup or an ETag response.
    with client.app.state.session_factory() as session:
        asset = session.get(AssetRecord, asset_id)
        asset.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert (
        client.get(
            data["url"], headers={"If-None-Match": response.headers["etag"]}
        ).status_code
        == 404
    )


def test_thumbnail_keeps_transparency_and_exif_orientation():
    transparent = Image.new("RGBA", (800, 400), (250, 10, 30, 0))
    output = BytesIO()
    transparent.save(output, "PNG")
    with Image.open(BytesIO(render_thumbnail(output.getvalue()))) as thumbnail:
        assert thumbnail.size == (480, 240)
        assert thumbnail.getpixel((0, 0))[3] == 0
    oriented = Image.new("RGB", (800, 400), "red")
    exif = Image.Exif()
    exif[274] = 6
    output = BytesIO()
    oriented.save(output, "JPEG", exif=exif)
    with Image.open(BytesIO(render_thumbnail(output.getvalue()))) as thumbnail:
        assert thumbnail.size == (240, 480)


def test_cache_coalesces_tabs_survives_cancellation_and_bounds_pending(monkeypatch):
    monkeypatch.setattr("app.thumbnails.render_thumbnail", lambda payload: payload)

    async def scenario():
        cache = ThumbnailCache(max_bytes=8, max_pending=1)
        release, entered = asyncio.Event(), asyncio.Event()
        calls = 0

        async def read():
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return b"preview"

        first = asyncio.create_task(cache.get("same", read))
        await entered.wait()
        second = asyncio.create_task(cache.get("same", read))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(ThumbnailBusy):
            await cache.get("another", read)
        release.set()
        assert await second == b"preview"
        assert await cache.get("same", read) == b"preview"
        assert calls == 1
        # Two entries do not fit: the older one is regenerated after eviction.
        assert await cache.get("another", read) == b"preview"
        assert await cache.get("same", read) == b"preview"
        assert calls == 3

    asyncio.run(scenario())
