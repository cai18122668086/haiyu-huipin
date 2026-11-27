import hashlib
from io import BytesIO

import pytest
from PIL import Image

from app import source_pixels


def test_white_exterior_cleanup_keeps_printing_white_detail_and_original_rgb():
    from PIL import ImageDraw

    image = Image.new("RGB", (300, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 279, 279), fill=(35, 90, 130))
    draw.rectangle((35, 35, 55, 55), fill="white")  # Interior physical printing.
    draw.line((50, 2, 50, 19), fill="white", width=1)  # Thin white detail.
    alpha = Image.new("L", image.size)
    mask_draw = ImageDraw.Draw(alpha)
    mask_draw.rectangle((19, 19, 280, 280), fill=255)
    mask_draw.line((50, 2, 50, 19), fill=255, width=1)
    before = image.tobytes()
    cleaned = source_pixels._trim_white_matte_edge(image, alpha)
    assert cleaned.getpixel((19, 40)) == 0
    assert cleaned.getpixel((20, 40)) == 255
    assert cleaned.getpixel((45, 45)) == 255
    assert cleaned.getpixel((50, 5)) == 255
    assert image.tobytes() == before


def test_white_edge_cleanup_fails_closed_if_removal_is_not_narrow():
    image = Image.new("RGB", (20, 20), "white")
    image.paste((20, 70, 130), (4, 4, 16, 16))
    alpha = Image.new("L", image.size)
    alpha.paste(255, (3, 3, 17, 17))
    with pytest.raises(ValueError, match="narrow source-edge bound"):
        source_pixels._trim_white_matte_edge(image, alpha)


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


def test_background_plate_keeps_required_marketing_copy_outside_product_cells():
    prompt = source_pixels.background_prompt(
        "Pastel continuous background", [[0.1, 0.4, 0.9, 0.9]], ("批准标题",)
    )
    assert "批准标题" in prompt and "REQUIRED VISIBLE MARKETING TEXT" in prompt
    assert "Missing approved copy is a failure" in prompt
    empty = source_pixels.background_prompt("Pure white", [[0, 0, 1, 1]])
    assert "NO TEXT anywhere" in empty
    assert "REQUIRED VISIBLE MARKETING TEXT" not in empty


def test_scene_plate_preserves_required_supports_and_actual_product_geometry():
    geometry = [{"visible_product_bounds": [0.4, 0.3, 0.6, 0.8]}]
    prompt = source_pixels.background_prompt(
        "An open palm supporting the product and a tote attachment loop",
        [[0.3, 0.2, 0.7, 0.9]],
        product_geometry=geometry,
    )
    assert "open palm supporting" in prompt and "tote attachment loop" in prompt
    assert "preserve" in prompt.casefold() and "required supporting surface" in prompt
    assert '"visible_product_bounds": [0.4, 0.3, 0.6, 0.8]' in prompt
    assert "measured upper contact band" in prompt
    assert "measured lower contact band" in prompt
    assert "Do not draw products" in prompt
    assert "unless the approved scene explicitly requires that support" in prompt


def test_two_screen_background_keeps_one_uninterrupted_backdrop():
    description = "Blue daylight with a display rod behind the clasp position."
    cells = [[0.1, 0.1, 0.9, 0.4], [0.1, 0.6, 0.9, 0.9]]
    prompt = source_pixels.background_prompt(description, cells, continuous_pair=True)
    assert "horizontal midpoint (y=0.5)" in prompt
    assert "No hard color band, horizontal divider" in prompt
    assert "guide composition, not background panels" in prompt
    assert description in prompt
    assert "TWO-SCREEN CONTINUITY" not in source_pixels.background_prompt(
        description, cells
    )


def test_placement_geometry_uses_the_actual_asymmetric_attachment_contour():
    original = Image.new("RGBA", (100, 100))
    original.paste((40, 50, 60, 255), (15, 30, 85, 95))
    original.paste((80, 60, 40, 255), (70, 5, 85, 30))
    cutout = source_pixels.artifact(original, "physical-original.png")
    geometry = source_pixels.placement_geometry(cutout, (100, 100), [0, 0, 1, 1])
    x0, y0, x1, y1 = geometry["visible_product_bounds"]
    upper_x, upper_y, _, _ = geometry["upper_contact_band"]
    lower_x, _, lower_end_x, lower_y = geometry["lower_contact_band"]
    assert upper_x > (x0 + x1) / 2
    assert upper_y == y0
    assert (lower_x, lower_end_x) == (x0, x1)
    assert lower_y == y1


def test_comparison_checks_real_candidate_opaque_pixels_without_approving_quality():
    background = source_pixels.artifact(Image.new("RGB", (80, 80), "blue"), "bg.png")
    cutout = source_pixels.artifact(Image.new("RGBA", (20, 40), "red"), "source.png")
    cells = [[0.1, 0.1, 0.9, 0.9]]
    candidate, _ = source_pixels.compose(background, (cutout,), cells)
    panels = source_pixels.comparison_panels(candidate, (cutout,), cells)
    assert len(panels) == 1
    with Image.open(BytesIO(candidate.payload)) as opened:
        changed = opened.convert("RGBA")
    changed.putpixel((40, 40), (0, 255, 0, 255))
    tampered = source_pixels.artifact(changed, "tampered.png")
    with pytest.raises(ValueError, match="differs from original opaque"):
        source_pixels.comparison_panels(tampered, (cutout,), cells)


def test_owned_context_excludes_adjacent_products_without_changing_fragment_threshold():
    inventory = [
        {"asset_id": "source", "box": [0.0, 0.1, 0.3, 0.9]},
        {"asset_id": "source", "box": [0.35, 0.1, 0.65, 0.9]},
        {"asset_id": "source", "box": [0.7, 0.1, 1.0, 0.9]},
    ]
    crop = source_pixels.owned_context(
        inventory[1], inventory, (1000, 1000), (250, 0, 750, 1000)
    )
    assert crop == (325, 0, 675, 1000)
    assert source_pixels.owned_context(
        inventory[1],
        [{**inventory[0], "asset_id": "another"}],
        (1000, 1000),
        (250, 0, 750, 1000),
    ) == (250, 0, 750, 1000)


def test_approved_detail_crop_preserves_exact_photographed_pixels():
    rgba = Image.new("RGBA", (100, 100), "red")
    rgba.putpixel((40, 70), (25, 50, 75, 255))
    original = source_pixels.artifact(rgba, "source.png")
    full = source_pixels.crop_windows((original,), [[0, 0, 1, 1]])[0]
    assert full is original
    detail = source_pixels.crop_windows((original,), [[0.2, 0.5, 0.8, 0.8]])[0]
    with Image.open(BytesIO(detail.payload)) as actual:
        assert actual.size == (60, 30)
        assert actual.tobytes() == rgba.crop((20, 50, 80, 80)).tobytes()
    with pytest.raises(ValueError, match="leaves its source"):
        source_pixels.crop_windows((original,), [[0.2, 0.5, 1.1, 0.8]])


def test_macro_layout_cannot_enlarge_tiny_original_details_into_a_pixelated_hero():
    original = source_pixels.artifact(Image.new("RGBA", (200, 300), "red"), "src.png")
    windows = [[0.1, 0.1, 0.3, 0.3]]
    cropped = source_pixels.crop_windows((original,), windows)
    source_pixels.validate_detail_resolution(
        cropped, [[0.1, 0.1, 0.4, 0.4]], windows, (400, 400)
    )
    with pytest.raises(
        source_pixels.DetailResolutionError, match="maximum is 4x"
    ) as error:
        source_pixels.validate_detail_resolution(
            cropped * 2, [[0.1, 0.1, 0.9, 0.4], [0.1, 0.6, 0.9, 0.9]],
            windows * 2, (1440, 3840), panels=2,
        )
    violations = error.value.diagnostic["violations"]
    assert [row["placement_index"] for row in violations] == [0, 1]
    diagnostic = violations[0]
    assert diagnostic["placement_index"] == 0
    assert diagnostic["original_detail_pixels"] == [40, 60]
    assert diagnostic["maximum_inset_pixels"] == [160, 240]
    assert diagnostic["maximum_panel_box_extent"] == pytest.approx([160 / 1440, 0.125])
    assert original.sha256 == hashlib.sha256(original.payload).hexdigest()


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
        source_pixels, "_alpha", lambda image, *_: Image.new("L", image.size, 255)
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


def test_high_resolution_matte_bounds_inference_and_keeps_native_rgb(monkeypatch):
    from types import SimpleNamespace

    from PIL import ImageDraw

    original = Image.new("RGB", (3072, 4096), "white")
    draw = ImageDraw.Draw(original)
    draw.rectangle((0, 0, 2500, 3800), fill=(30, 80, 210))
    draw.line((430, 430, 2500, 2400), fill=(20, 40, 80), width=3)
    source = source_pixels.artifact(original, "source.png")
    authority = SimpleNamespace(
        asset_id="own-source", payload=source.payload, sha256=source.sha256
    )

    def alpha(image, model, selection):
        width, height = image.size
        assert width * height <= source_pixels.MAX_MATTE_WORK_PIXELS
        assert selection == (0, 0, width, height)
        mask = Image.new("L", image.size)
        mask.paste(255, (width // 8, height // 8, width * 7 // 8, height * 7 // 8))
        return mask

    monkeypatch.setattr(source_pixels, "_alpha", alpha)
    layer = source_pixels.extract_layers(
        (authority,),
        {"selected_regions": [{"asset_id": "own-source", "box": [0, 0, 1, 1]}]},
        None,
    )[0]
    with Image.open(BytesIO(layer.artifact.payload)) as result:
        source_region = original.crop(layer.source_box)
        assert result.size == source_region.size
        assert result.height > 3000
        assert result.convert("RGB").tobytes() == source_region.tobytes()
        assert result.getpixel((0, 0))[3] == 0
    assert layer.source_sha256 == source.sha256


def test_refinement_recovers_small_fitting_without_erasing_coarse_foreground(
    monkeypatch, tmp_path
):
    from PIL import ImageDraw

    monkeypatch.setattr(source_pixels, "_net", object())
    monkeypatch.setattr(source_pixels, "_loaded_path", tmp_path)
    monkeypatch.setattr(source_pixels.importlib, "import_module", lambda _: None)
    monkeypatch.setattr(
        source_pixels, "_recover_fine_edges", lambda crop, coarse, *args: coarse
    )
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

    monkeypatch.setattr(source_pixels, "_single_product", lambda mask, *args: mask)
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


@pytest.mark.parametrize(("instruction", "enabled"), [
    ("增加图案背景，不额外增加内容", True),
    ("更换吸引外国客户购买的背景，不额外增加内容", True),
    ("替换背景，同时修改商品颜色", False),
    ("添加配件并更换背景", False),
    ("添加项链和蓝色背景", False),
    ("把背景换成浅灰色", True),
    ("将背景改成红色再把商品放大", False),
    ("修改图案，不额外增加内容", False),
])
def test_creative_background_only_selection(instruction, enabled):
    assert source_pixels.enabled_for({
        "job_type": "creative_image", "settings": {"instruction": instruction},
    }) is enabled


@pytest.mark.parametrize(("target", "expected"), [
    ("200x100", (61, 40, 91, 66)), ("200x200", (61, 90, 91, 116)),
])
def test_background_edit_preserves_subject_size_and_position(target, expected):
    from types import SimpleNamespace

    full = source_pixels.artifact(Image.new("RGB", (200, 100), "white"), "source.png")
    source = SimpleNamespace(
        asset_id="source", sha256=full.sha256, payload=full.payload,
    )
    cutout = Image.new("RGBA", (50, 35))
    cutout.paste((10, 25, 60, 255), (5, 4, 35, 30))
    layer = source_pixels.PixelLayer(
        source_pixels.artifact(cutout, "layer.png"), "source", full.sha256,
        (56, 36, 106, 71),
    )
    cell, = source_pixels.original_positions((layer,), source, target)
    w, h = map(int, target.split("x"))
    _, bounds, _ = source_pixels.placed_layer(layer.artifact, (w, h), cell)
    assert bounds == expected


def repair_fixture():
    from PIL import ImageDraw

    rgb = Image.new("RGBA", (100, 200), (45, 80, 120, 0))
    source_draw = ImageDraw.Draw(rgb)
    source_draw.rectangle((44, 8, 55, 45), fill=(190, 145, 55, 0))
    source_draw.rectangle((49, 12, 51, 16), fill=(140, 145, 150, 0))
    alpha = Image.new("L", rgb.size)
    draw = ImageDraw.Draw(alpha)
    draw.rectangle((45, 8, 55, 45), fill=255)
    draw.rectangle((20, 46, 80, 190), fill=255)
    rgb.putalpha(alpha)
    layer = source_pixels.PixelLayer(
        source_pixels.artifact(rgb, "source.png"),
        "original",
        "a" * 64,
        (0, 0, 100, 200),
    )
    return layer


def correction(action, box):
    return {
        "layer_index": 0,
        "action": action,
        "box": [
            (box[0] + 16) / 132,
            (box[1] + 16) / 232,
            (box[2] + 16) / 132,
            (box[3] + 16) / 232,
        ],
    }


def test_mask_correction_copies_only_alpha_and_keeps_source_rgb():
    layer = repair_fixture()
    repaired = source_pixels.repair_layers(
        (layer,),
        [
            correction("restore_foreground", (44, 10, 45, 15)),
            correction("remove_background", (49, 12, 51, 16)),
        ],
        0,
    )[0]
    with (
        Image.open(BytesIO(layer.artifact.payload)) as before,
        Image.open(BytesIO(repaired.artifact.payload)) as after,
    ):
        assert before.convert("RGB").tobytes() == after.convert("RGB").tobytes()
        assert before.getpixel((44, 12))[3] == 0
        assert after.getpixel((44, 12))[3] == 255
        assert after.getpixel((50, 14))[3] == 0
        assert (
            before.crop((20, 46, 81, 191)).tobytes()
            == after.crop((20, 46, 81, 191)).tobytes()
        )


def test_hardware_repair_box_never_becomes_an_opaque_background_rectangle():
    layer = repair_fixture()
    fixed = source_pixels.repair_layers(
        (layer,), [correction("restore_foreground", (40, 8, 60, 30))], 0
    )[0]
    with Image.open(BytesIO(fixed.artifact.payload)) as after:
        assert after.getpixel((44, 12))[3] == 255
        assert after.getpixel((40, 8))[3] == 0
        assert after.getpixel((59, 29))[3] == 0
    with pytest.raises(ValueError, match="photographed metal"):
        source_pixels.repair_layers(
            (layer,), [correction("remove_background", (45, 10, 47, 15))], 0
        )


@pytest.mark.parametrize(
    "repairs,feather,reason",
    [
        ([correction("remove_background", (25, 50, 30, 55))], 0, "protected artwork"),
        ([correction("restore_foreground", (0, 0, 100, 200))], 0, "too much"),
        ([], 0, "no verifiable"),
        ([], 3, "bounded contract"),
        (
            [{"layer_index": 1, "action": "restore_foreground", "box": [0, 0, 1, 1]}],
            0,
            "foreign layer",
        ),
    ],
)
def test_mask_correction_fails_closed(repairs, feather, reason):
    with pytest.raises(ValueError, match=reason):
        source_pixels.repair_layers((repair_fixture(),), repairs, feather)


def test_native_pair_placements_preserve_authority_and_never_cross_seam():
    valid = [
        {"layer_index": 0, "panel": 0, "panel_box": [0.1, 0.1, 0.4, 0.9]},
        {"layer_index": 2, "panel": 1, "panel_box": [0.5, 0.2, 0.9, 0.9]},
    ]
    indices, cells = source_pixels.native_placements(valid, 3)
    assert indices == [0, 2] and cells == [
        [0.1, 0.05, 0.4, 0.45],
        [0.5, 0.6, 0.9, 0.95],
    ]
    from copy import deepcopy

    for mutation in ("seam", "foreign", "missing", "overlap"):
        bad = deepcopy(valid)
        if mutation == "seam":
            bad[0]["panel_box"][3] = 1.01
        elif mutation == "foreign":
            bad[0]["layer_index"] = 3
        elif mutation == "missing":
            bad.pop()
        else:
            bad.append(deepcopy(bad[0]))
        with pytest.raises(ValueError):
            source_pixels.native_placements(bad, 3)


def test_single_marketing_canvas_selects_only_its_planned_product_cell():
    value = [{"layer_index": 1, "panel": 0, "panel_box": [0.2, 0.3, 0.7, 0.9]}]
    assert source_pixels.native_placements(value, 3, panels=1) == (
        [1],
        [[0.2, 0.3, 0.7, 0.9]],
    )
    value[0]["panel"] = 1
    with pytest.raises(ValueError):
        source_pixels.native_placements(value, 3, panels=1)


def test_ambiguous_source_matte_changes_context_without_relaxing_fragment_guard(
    monkeypatch, tmp_path
):
    from PIL import ImageDraw

    monkeypatch.setattr(source_pixels, "_net", object())
    monkeypatch.setattr(source_pixels, "_loaded_path", tmp_path)
    monkeypatch.setattr(source_pixels.importlib, "import_module", lambda _: None)
    monkeypatch.setattr(
        source_pixels, "_recover_fine_edges", lambda crop, coarse, *args: coarse
    )
    seen = []

    def segment(crop, *args):
        seen.append(crop.size)
        if len(seen) == 1:
            raise source_pixels.MatteFragmentError("ambiguous foreground")
        result = Image.new("L", crop.size)
        ImageDraw.Draw(result).rectangle(
            (10, 10, crop.width - 10, crop.height - 10), fill=255
        )
        return result

    monkeypatch.setattr(source_pixels, "_single_product", lambda mask, *args: mask)
    monkeypatch.setattr(source_pixels, "_segment", segment)
    result = source_pixels._alpha(
        Image.new("RGB", (100, 100)), tmp_path, (30, 30, 70, 70)
    )
    assert len(seen) == 3 and seen[1] == (64, 64)
    assert result.size == (100, 100)
    seen.clear()

    def ambiguous_refinement(crop, *args):
        seen.append(crop.size)
        if len(seen) == 2:
            raise source_pixels.MatteFragmentError("fragmented recentered crop")
        mask = Image.new("L", crop.size)
        ImageDraw.Draw(mask).rectangle((20, 20, 70, 70), fill=255)
        return mask

    monkeypatch.setattr(source_pixels, "_segment", ambiguous_refinement)
    recovered = source_pixels._alpha(
        Image.new("RGB", (100, 100)), tmp_path, (15, 15, 75, 75)
    )
    assert len(seen) == 2 and recovered.getbbox() == (20, 20, 71, 71)
    seen.clear()

    def always_ambiguous(*args):
        seen.append(1)
        raise source_pixels.MatteFragmentError("ambiguous foreground")

    monkeypatch.setattr(source_pixels, "_segment", always_ambiguous)
    with pytest.raises(source_pixels.MatteFragmentError):
        source_pixels._alpha(Image.new("RGB", (100, 100)), tmp_path, (30, 30, 70, 70))
    assert len(seen) == 2
