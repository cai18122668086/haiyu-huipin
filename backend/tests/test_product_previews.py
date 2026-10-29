"""Product previews stay same-origin, workspace-owned, and image-only."""

from io import BytesIO

import pytest
from PIL import Image

from app.everyinfra import normalize_product


def product_document(client, monkeypatch):
    def fetch(*args, **kwargs):
        return {
            "title": "羽绒服",
            "imageUrls": ["https://img.alicdn.com/main.jpg"],
            "descImageUrls": ["https://img.alicdn.com/detail.jpg"],
            "skuImageUrls": ["https://img.alicdn.com/sku.jpg"],
        }

    monkeypatch.setattr("app.replica.fetch_product", fetch)
    response = client.post(
        "/api/v1/workspaces/ws_test/replica/products",
        json={"url": "https://detail.tmall.com/item.htm?id=123456789"},
    )
    return response.json()["id"]


@pytest.mark.parametrize(
    "group,filename",
    [("imageUrls", "main"), ("descImageUrls", "detail"), ("skuImageUrls", "sku")],
)
def test_preview_serves_the_selected_returned_image_with_private_cache(
    client, monkeypatch, group, filename
):
    document_id = product_document(client, monkeypatch)
    output = BytesIO()
    Image.new("RGB", (80, 90), "purple").save(output, "PNG")
    calls = []

    def download(url, max_bytes):
        calls.append(url)
        return output.getvalue()

    monkeypatch.setattr("app.replica._download", download)
    response = client.get(
        f"/api/v1/workspaces/ws_test/replica/products/{document_id}/images/{group}/0"
    )
    assert response.status_code == 200
    assert response.content == output.getvalue()
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert "Cookie" in {part.strip() for part in response.headers["vary"].split(",")}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert calls == [f"https://img.alicdn.com/{filename}.jpg"]
    calls.clear()
    assert (
        client.get(
            f"/api/v1/workspaces/ws_other/replica/products/{document_id}/images/{group}/0"
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/workspaces/ws_test/replica/products/{document_id}/images/{group}/1"
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/workspaces/ws_test/replica/products/{document_id}/images/{group}/-1"
        ).status_code
        == 404
    )
    assert not calls


@pytest.mark.parametrize(
    "payload",
    [b"<html>not an image</html>", b'<svg xmlns="http://www.w3.org/2000/svg"/>'],
)
def test_preview_never_serves_active_content_as_an_image(client, monkeypatch, payload):
    document_id = product_document(client, monkeypatch)
    monkeypatch.setattr("app.replica._download", lambda *args: payload)
    response = client.get(
        f"/api/v1/workspaces/ws_test/replica/products/{document_id}/images/imageUrls/0"
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PRODUCT_IMAGE_INVALID"


def test_preview_enforces_the_existing_image_pixel_limit(client, monkeypatch):
    document_id = product_document(client, monkeypatch)
    output = BytesIO()
    Image.new("RGB", (80, 90), "purple").save(output, "PNG")
    client.app.state.settings.max_image_pixels = 100
    monkeypatch.setattr("app.replica._download", lambda *args: output.getvalue())
    response = client.get(
        f"/api/v1/workspaces/ws_test/replica/products/{document_id}/images/imageUrls/0"
    )
    assert response.status_code == 502


def test_native_title_is_preferred_over_the_upstream_translation():
    result = normalize_product(
        {
            "id": "123456789",
            "title": "Translated jacket",
            "titleOriginal": "轻量鹅绒服",
            "images": ["https://img.alicdn.com/main.jpg"],
        },
        "tmall",
        "123456789",
        "https://detail.tmall.com/item.htm?id=123456789",
    )
    assert result["title"] == "轻量鹅绒服"
