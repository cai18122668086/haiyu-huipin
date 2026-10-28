"""Pixel/transport and generation-recovery contracts, using synthetic fixtures."""

import asyncio
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.provider import ProviderOutcome
from app.replica_native import (
    NativeReplicaRun,
    ReplicaInputTooLarge,
    digest,
)
from app.replica_native_provider import NativeReplicaProvider


def run_at(tmp_path, method, gateway=None):
    return NativeReplicaRun(
        method, "rep_" + uuid4().hex, "ws_test", gateway, None, root=tmp_path
    )


def image_at(tmp_path, mode="RGB"):
    path = tmp_path / "product-anchor.png"
    Image.new(mode, (512, 512), (27, 81, 244, 0) if mode == "RGBA" else "blue").save(
        path, compress_level=0
    )
    return path


@pytest.mark.parametrize("method", ["exact", "style"])
@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_lossless_transport_preserves_pixels_roles_and_original_hash(
    tmp_path, method, mode
):
    run = run_at(tmp_path, method)
    path = image_at(tmp_path, mode)
    original = path.read_bytes()
    source = run.sources([path])[0]
    assert source.content_type == "image/webp"
    assert len(source.payload) < len(original)
    assert source.sha256 == digest(source.payload)
    assert source.source_role == "product_reference"
    assert path.read_bytes() == original
    with Image.open(BytesIO(source.payload)) as transported, Image.open(path) as native:
        assert transported.size == native.size
        assert transported.convert("RGBA").tobytes() == native.convert("RGBA").tobytes()


def test_small_images_keep_their_existing_transport_bytes(tmp_path):
    run = run_at(tmp_path, "exact")
    path = tmp_path / "reference.png"
    Image.new("RGB", (32, 32), "red").save(path)
    source = run.sources([path])[0]
    assert source.payload == path.read_bytes()
    assert source.content_type == "image/png"
    assert source.source_role == "style_reference"


def test_oversized_analysis_stops_before_any_provider_call(tmp_path, monkeypatch):
    calls = []

    async def plan(request):
        calls.append(request)

    run = run_at(tmp_path, "exact", SimpleNamespace(plan=plan))
    path = image_at(tmp_path)
    monkeypatch.setattr("app.replica_native.MAX_SOURCE_BYTES", 1)
    with pytest.raises(ReplicaInputTooLarge):
        asyncio.run(run.ask("identity-segmentation", "fixture", {}, paths=[path]))
    assert calls == []


@pytest.mark.parametrize("method", ["exact", "style"])
def test_generation_uses_lossless_sources_and_cached_result_never_dispatches_again(
    tmp_path, method
):
    events = []
    output = BytesIO()
    Image.new("RGB", (64, 64), "pink").save(output, "PNG")

    async def edit(request):
        events.append("network")
        assert request.sources[0].content_type == "image/webp"
        return SimpleNamespace(
            outcome=ProviderOutcome.SUCCEEDED,
            artifact=SimpleNamespace(
                payload=output.getvalue(), locally_resampled=False
            ),
            normalized_usage={"reported": True},
            actual_cost_micros=1,
            estimated_cost_micros=1,
            http_status=200,
            error_code=None,
            raw_usage={},
        )

    run = run_at(tmp_path, method, SimpleNamespace(edit=edit))
    provider = NativeReplicaProvider(None, run.gateway, None)

    async def save(*args, **kwargs):
        events.append("saved")

    provider._save = save
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_fixture",
        job_item_id="item_fixture",
        attempt_id="attempt_fixture",
    )
    source = image_at(tmp_path)
    original = source.read_bytes()

    async def render():
        return await provider._render(
            run,
            request,
            {"token": "single-use"},
            "synthetic fixture",
            [source],
            [64, 64],
            "M01",
        )

    result = asyncio.run(render())
    assert result.read_bytes() == output.getvalue()
    assert events == ["saved", "network", "saved"]
    assert asyncio.run(render()) == result
    assert events.count("network") == 1
    assert source.read_bytes() == original


def test_oversized_generation_does_not_consume_an_invocation_token(
    tmp_path, monkeypatch
):
    calls = []

    async def edit(request):
        calls.append("network")

    run = run_at(tmp_path, "exact", SimpleNamespace(edit=edit))
    provider = NativeReplicaProvider(None, run.gateway, None)

    async def save(*args, **kwargs):
        calls.append("saved")

    provider._save = save
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_fixture",
        job_item_id="item_fixture",
        attempt_id="attempt_fixture",
    )
    source = image_at(tmp_path)
    monkeypatch.setattr("app.replica_native.MAX_SOURCE_BYTES", 1)
    with pytest.raises(ReplicaInputTooLarge):
        asyncio.run(
            provider._render(
                run, request, {"token": "unspent"}, "fixture", [source], [64, 64], "M01"
            )
        )
    assert calls == []
    assert not (run.root / "invocations.json").exists()


@pytest.mark.parametrize("mode", ["L", "P", "I;16"])
def test_non_rgb_png_depth_and_mode_keep_original_transport_bytes(tmp_path, mode):
    run = run_at(tmp_path, "exact")
    path = tmp_path / "reference.png"
    Image.new(mode, (512, 512), 1024 if mode == "I;16" else 27).save(
        path, compress_level=0, bits=8
    )
    original = path.read_bytes()
    assert len(original) > 256 * 1024
    source = run.sources([path])[0]
    assert source.content_type == "image/png"
    assert source.payload == original
    assert path.read_bytes() == original


def test_lossless_transport_preserves_color_profile_and_orientation_metadata(tmp_path):
    run = run_at(tmp_path, "exact")
    path = tmp_path / "reference.png"
    profile = b"synthetic ICC byte-preservation fixture" * 20
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (512, 512), "blue").save(
        path, compress_level=0, icc_profile=profile, exif=exif
    )
    original = path.read_bytes()
    source = run.sources([path])[0]
    assert source.content_type == "image/webp"
    with Image.open(BytesIO(source.payload)) as image:
        assert image.info["icc_profile"] == profile
        assert image.getexif()[274] == 6
    assert path.read_bytes() == original


@pytest.mark.parametrize("metadata", ["gamma", "transparency"])
def test_png_metadata_without_exact_webp_representation_keeps_original_bytes(
    tmp_path, metadata
):
    import struct

    from PIL.PngImagePlugin import PngInfo

    run = run_at(tmp_path, "exact")
    path = tmp_path / "reference.png"
    options = {"compress_level": 0}
    if metadata == "gamma":
        info = PngInfo()
        info.add(b"gAMA", struct.pack(">I", 70000))
        options["pnginfo"] = info
    else:
        options["transparency"] = (0, 0, 255)
    Image.new("RGB", (512, 512), "blue").save(path, **options)
    source = run.sources([path])[0]
    assert source.content_type == "image/png"
    assert source.payload == path.read_bytes()
