"""Synthetic raster lineage tests; no supplier calls or visual-quality claims."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
)
from app.domestic_detail.generation import (
    DetailGenerationError,
    begin_invocation,
    bind_workspace,
    finish_invocation,
    verify_accepted_canvas,
)
from app.domestic_detail.pixels import (
    DetailPixelError,
    load_native_pair_pixels,
    pixel_review_context,
)
from tests.test_native_detail_failure_transition import (
    audit,
    record_fixture,
)  # noqa: F401
from tests.test_native_detail_failure_transition import pair_audit as pair_audit


def inspect(root, selection="LATEST_RECORDED"):
    return AuroraDetailHarnessRunner().run(
        "inspect-pair-artifacts",
        root,
        {"session": root / "session.json", "pair-id": "P01", "selection": selection},
    )


@pytest.mark.parametrize(
    "kind,size,expected",
    [
        ("RGB", (30, 80), "EXACT"),
        ("RGB", (60, 160), "CENTER_CROP_RESIZE"),
        ("RGB", (32, 80), "BOUNDED_CENTER_CROP_RESIZE"),
        ("RGB", (80, 80), "HYBRID_CROP_WITH_BLURRED_EDGE_EXTENSION"),
        ("RGBA", (30, 80), "ORIENTATION_COLOR_FRAME_NORMALIZE"),
        ("EXIF", (80, 30), "ORIENTATION_COLOR_FRAME_NORMALIZE"),
        ("JPEG", (30, 80), "EXACT"),
    ],
)
def test_native_normalization_and_fixed_crops_are_exact_bytes(
    pair_audit, kind, size, expected
):
    base, current, _ = pair_audit
    root = base / "normalization"
    root.mkdir()
    current["session_dir"] = str(root)
    source = root / ("source.jpg" if kind in {"EXIF", "JPEG"} else "source.png")
    image = Image.new("RGBA" if kind == "RGBA" else "RGB", size, "#214365")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, size[0] // 2, size[1] // 2), fill="#ffa041")
    if kind == "EXIF":
        exif = Image.Exif()
        exif[274] = 6
        image.save(source, exif=exif)
    else:
        image.save(source)
    original = source.read_bytes()
    current["plan"]["output_strategy"].update(canvas_width_px=30, module_height_px=40)
    for module in current["modules"]:
        module["plan"]["output"].update(width_px=30, height_px=40)
        module.update(attempts=[], cycle_attempt_count=0)
    current["generation_requests"] = []
    record_fixture(root, current, source, "normalization-token")
    before = (root / "session.json").read_bytes()
    lineage = inspect(root)
    assert source.read_bytes() == original
    assert Path(lineage["source"]["path"]).read_bytes() == original
    assert lineage["normalization"]["mode"] == expected
    assert (root / "session.json").read_bytes() == before
    pixels = load_native_pair_pixels(lineage, [root], group_key="fixture")
    assert pixels.canvas.payload == Path(lineage["canvas"]["path"]).read_bytes()
    assert (pixels.canvas.width, pixels.canvas.height) == (30, 80)
    with Image.open(Path(lineage["canvas"]["path"])) as canvas:
        for offset, (artifact, row) in enumerate(
            zip(pixels.screens, lineage["slices"], strict=True)
        ):
            assert artifact.payload == Path(row["path"]).read_bytes()
            with Image.open(Path(row["path"])) as sliced:
                assert (
                    sliced.tobytes()
                    == canvas.crop((0, offset * 40, 30, (offset + 1) * 40)).tobytes()
                )
    context = json.dumps(pixel_review_context(lineage))
    assert "source_path" not in context and str(root) not in context


@pytest.mark.parametrize(
    "changed",
    [
        "source",
        "canvas",
        "canvas_forged_hash",
        "slice",
        "slice_forged_hash",
        "coordinates",
        "order",
        "signed_request",
    ],
)
def test_native_inspection_rejects_changed_pixels_even_with_same_dimensions(
    pair_audit, changed
):
    root, current, _ = pair_audit
    if changed in {"canvas", "canvas_forged_hash"}:
        source = root / "wrong-size.png"
        Image.new("RGB", (6, 16), "#123456").save(source)
        current = record_fixture(root, current, source, "second-normalized-token")
    lineage = inspect(root)
    if changed == "source":
        Path(lineage["source"]["path"]).write_bytes(b"changed original")
    elif changed in {"canvas", "canvas_forged_hash", "slice", "slice_forged_hash"}:
        target = Path(
            lineage["canvas"]["path"]
            if changed in {"canvas", "canvas_forged_hash"}
            else lineage["slices"][0]["path"]
        )
        with Image.open(target) as image:
            modified = image.copy()
        modified.putpixel((0, 0), (255, 1, 2))
        modified.save(target, format="PNG")
        if changed == "canvas_forged_hash":
            for module in current["modules"]:
                module["attempts"][-1]["normalized_image_sha256"] = hashlib.sha256(
                    target.read_bytes()
                ).hexdigest()
        if changed == "slice_forged_hash":
            row = current["modules"][0]["attempts"][-1]["pair_slice"]
            row["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            row["pixel_sha256"] = hashlib.sha256(
                b"RGB:3x4" + bytes([0]) + modified.tobytes()
            ).hexdigest()
    elif changed == "coordinates":
        current["modules"][0]["attempts"][-1]["pair_slice"]["crop_box"][1] = 1
    elif changed == "order":
        current["plan"]["output_strategy"]["continuous_pair_rendering"]["pair_ids"][0][
            "module_ids"
        ].reverse()
    else:
        request = Path(current["generation_requests"][0]["request"])
        value = json.loads(request.read_text())
        value["prompt"] = "changed signed request"
        request.write_text(json.dumps(value))
    if changed in {"slice_forged_hash", "canvas_forged_hash", "coordinates", "order"}:
        (root / "session.json").write_text(json.dumps(current), encoding="utf-8")
    before = (root / "session.json").read_bytes()
    with pytest.raises(AuroraDetailHarnessError):
        inspect(root)
    assert (root / "session.json").read_bytes() == before


def test_loader_rejects_wrong_job_workspace_and_changed_transport_proof(
    pair_audit, tmp_path
):
    root, _, _ = pair_audit
    lineage = inspect(root)
    with pytest.raises(DetailPixelError, match="escaped"):
        load_native_pair_pixels(lineage, [tmp_path / "different-job"], group_key="x")
    changed = deepcopy(lineage)
    changed["slices"].reverse()
    with pytest.raises(DetailPixelError, match="proof changed"):
        load_native_pair_pixels(changed, [root], group_key="x")


def test_accepted_recovery_requires_full_native_lineage_and_original_paid_bytes(
    pair_audit,
):
    root, current, judgment = pair_audit
    judgment["status"] = "PASS"
    judgment["material_failures"] = []
    audit((root, current, judgment))
    lineage = inspect(root, "ACCEPTED")
    binding = {"sha256": "b" * 64}
    bind_workspace(root, binding, create=True)
    native = {
        "token": lineage["token"],
        "pair_id": "P01",
        "signed_request_sha256": lineage["signed_request_sha256"],
    }
    receipt = begin_invocation(
        root,
        binding,
        native,
        provider="synthetic",
        model="synthetic",
        pricing_version="test",
        idempotency_key="key",
        prompt="synthetic",
        size="3x8",
        quality="high",
        sources=[],
    )
    payload = Path(lineage["source"]["path"]).read_bytes()
    finish_invocation(
        receipt,
        outcome="succeeded",
        provider_request_id="synthetic",
        artifact_sha256=lineage["source"]["sha256"],
        artifact_payload=payload,
    )
    assert (
        verify_accepted_canvas(
            [root], binding, pair_id="P01", artifact_sha256=lineage["canvas"]["sha256"]
        )
        == lineage
    )
    receipt.with_suffix(".artifact").write_bytes(
        b"same receipt, changed actual original"
    )
    with pytest.raises(DetailGenerationError, match="original paid response"):
        verify_accepted_canvas(
            [root], binding, pair_id="P01", artifact_sha256=lineage["canvas"]["sha256"]
        )
