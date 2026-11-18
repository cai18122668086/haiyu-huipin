import hashlib
from io import BytesIO

import pytest
from PIL import Image

from app import source_pixels


def test_composition_uses_source_pixels_and_keeps_native_background_geometry():
    background = source_pixels.artifact(
        Image.new("RGB", (120, 80), "blue"), "native.png"
    )
    product = Image.new("RGBA", (20, 30), (200, 30, 50, 255))
    for y in range(8, 23):
        product.putpixel((10, y), (255, 255, 255, 255))
    layer = source_pixels.artifact(product, "physical-original.png")
    result, proof = source_pixels.compose(background, (layer,), [[0, 0, 1, 1]])
    assert (result.width, result.height) == (120, 80)
    assert proof["background_sha256"] == background.sha256
    assert proof["composite_sha256"] == hashlib.sha256(result.payload).hexdigest()
    box = proof["placements"][0]["box"]
    expected = product.resize(
        (box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS
    )
    with Image.open(BytesIO(result.payload)) as output:
        assert output.crop(tuple(box)).tobytes() == expected.tobytes()
        assert output.getpixel((0, 0)) == (0, 0, 255, 255)


@pytest.mark.parametrize("which", ["background", "source"])
def test_compositor_rejects_any_changed_input_hash(which):
    background = source_pixels.artifact(
        Image.new("RGB", (40, 40), "white"), "native.png"
    )
    layer = source_pixels.artifact(Image.new("RGBA", (20, 30), "red"), "source.png")
    target = background if which == "background" else layer
    # Simulate corrupted replay bytes after the dataclass's admission check.
    object.__setattr__(target, "payload", target.payload + b"tampered")
    with pytest.raises(ValueError, match="changed"):
        source_pixels.compose(background, (layer,), [[0, 0, 1, 1]])


def test_matte_copies_rgb_without_drawing_or_inventing_print(monkeypatch):
    original = Image.new("RGB", (100, 100), (100, 150, 200))
    for x in range(20, 80):
        original.putpixel((x, 55), (x, 0, 255 - x))
    source = source_pixels.artifact(original, "source.png")
    from types import SimpleNamespace

    authority = SimpleNamespace(
        asset_id="own-source", payload=source.payload, sha256=source.sha256
    )
    monkeypatch.setattr(
        source_pixels, "_alpha", lambda image, _: Image.new("L", image.size, 255)
    )
    layer = source_pixels.extract_layers(
        (authority,),
        {
            "selected_regions": [
                {"asset_id": "own-source", "box": [0.1, 0.1, 0.9, 0.9]}
            ],
        },
        None,
    )[0]
    with Image.open(BytesIO(layer.artifact.payload)) as result:
        assert (
            result.convert("RGB").tobytes() == original.crop(layer.source_box).tobytes()
        )
    assert layer.source_sha256 == source.sha256


def test_refinement_recovers_small_fitting_without_erasing_coarse_foreground(
    monkeypatch, tmp_path
):
    from PIL import ImageDraw

    monkeypatch.setattr(source_pixels, "_net", object())
    monkeypatch.setattr(source_pixels, "_loaded_path", tmp_path)
    monkeypatch.setattr(source_pixels.importlib, "import_module", lambda _: None)
    seen = []

    def masks(crop, *args):
        seen.append(crop.size)
        result = Image.new("L", crop.size)
        draw = ImageDraw.Draw(result)
        if len(seen) == 1:
            draw.rectangle((30, 30, 70, 80), fill=255)
        else:
            draw.rectangle((5, 0, crop.width - 5, crop.height - 1), fill=255)
        return result

    monkeypatch.setattr(source_pixels, "_segment", masks)
    result = source_pixels._alpha(Image.new("RGB", (100, 100)), tmp_path)
    assert len(seen) == 2 and seen[1] != seen[0]
    assert result.getpixel((30, 30)) == 255
    assert result.getpixel((40, 18)) == 255
    assert result.getpixel((0, 0)) == 0


def test_source_pixel_mode_cannot_undo_requested_garment_or_color_changes():
    for operation in ("change_color", "change_model", "try_on", "model_image"):
        assert not source_pixels.enabled_for(
            {
                "job_type": "product_image",
                "settings": {"task_type": operation},
            }
        )
    assert not source_pixels.enabled_for({"job_type": "detail_pack", "settings": {}})


def test_native_pair_placements_preserve_authority_and_never_cross_seam():
    valid = [
        {"layer_index": 0, "panel": 0, "box": [0.1, 0.1, 0.4, 0.45]},
        {"layer_index": 2, "panel": 1, "box": [0.5, 0.6, 0.9, 0.95]},
    ]
    indices, cells = source_pixels.native_placements(valid, 3)
    assert indices == [0, 2] and cells == [v["box"] for v in valid]
    from copy import deepcopy

    for mutation in ("seam", "foreign", "missing", "overlap"):
        bad = deepcopy(valid)
        if mutation == "seam":
            bad[0]["box"][3] = 0.51
        elif mutation == "foreign":
            bad[0]["layer_index"] = 3
        elif mutation == "missing":
            bad.pop()
        else:
            bad.append(deepcopy(bad[0]))
        with pytest.raises(ValueError):
            source_pixels.native_placements(bad, 3)
