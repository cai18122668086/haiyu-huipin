from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app import source_pixels, source_recolor


def fixture():
    image = Image.new("RGB", (200, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 49, 69), fill=(185, 145, 60))
    draw.text((30, 120), "ORIGINAL TEXT", fill="black")
    draw.rectangle((100, 40, 150, 80), fill="orange")
    image.putpixel((35, 35), (230, 220, 200))
    original = source_pixels.artifact(image, "source.png")
    source = SimpleNamespace(
        asset_id="product", sha256=original.sha256, payload=original.payload
    )
    recipe = {
        "version": source_recolor.VERSION,
        "source_asset_id": "product",
        "source_sha256": source.sha256,
        "selection": {
            "pixel_inspected": True,
            "unresolved": [],
            "regions": [{"part": "metal_clasp", "box": [0.15, 0.15, 0.25, 0.35]}],
        },
    }
    return image, source, recipe


def test_silver_changes_only_selected_gold_chroma_and_keeps_print_and_geometry():
    original, source, recipe = fixture()
    result, proof = source_recolor.render(source, recipe)
    assert (result.width, result.height) == original.size
    assert proof["outside_mask_changed_pixels"] == 0 and proof["image_requests"] == 0
    with Image.open(BytesIO(result.payload)) as actual:
        assert actual.getpixel((40, 40)) == (147, 147, 147)
        assert actual.getpixel((35, 35)) == (230, 220, 200)
        assert (
            actual.crop((0, 80, 200, 200)).tobytes()
            == original.crop((0, 80, 200, 200)).tobytes()
        )
        assert (
            actual.crop((100, 40, 151, 81)).tobytes()
            == original.crop((100, 40, 151, 81)).tobytes()
        )


@pytest.mark.parametrize("change", ["authority", "broad", "unresolved", "no_gold"])
def test_source_recolor_fails_before_an_image_or_unbounded_pixel_change(change):
    _, source, recipe = fixture()
    if change == "authority":
        recipe["source_sha256"] = "f" * 64
    elif change == "broad":
        recipe["selection"]["regions"][0]["box"] = [0, 0, 1, 1]
    elif change == "unresolved":
        recipe["selection"]["unresolved"] = ["Uncertain fitting"]
    else:
        recipe["selection"]["regions"][0]["box"] = [0.4, 0.7, 0.5, 0.8]
    with pytest.raises(ValueError):
        source_recolor.render(source, recipe)


def test_local_silver_path_does_not_apply_to_other_requested_edits():
    _, source, _ = fixture()
    source.source_role = "product_reference"
    context = {
        "job_type": "product_image",
        "settings": {
            "task_type": "change_color",
            "target_color": "银色，仅金属配件",
            "instruction": "只将金属扣改为银色，保留文字背景",
        },
    }
    assert source_recolor.enabled(context, (source,))
    assert not source_recolor.enabled(
        {**context, "job_type": "creative_image"}, (source,)
    )
    assert not source_recolor.enabled(
        {**context, "settings": {**context["settings"], "target_color": "蓝色"}},
        (source,),
    )
