import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app import source_pixels, source_print_evidence


def fixture():
    image = Image.new("RGB", (100, 100), "white")
    for y in range(20, 60):
        for x in range(10, 20):
            image.putpixel((x, y), (x * 10, y * 3, 17))
    view = source_pixels.artifact(image, "original.png")
    source = SimpleNamespace(
        asset_id="product", payload=view.payload, sha256=view.sha256
    )
    prep = {"selected_regions": [{
        "asset_id": "product", "box": [0, 0, 1, 1], "visible_print": ["LITERAL"],
        "print_regions": [[0.1, 0.2, 0.2, 0.2, 0.2, 0.6, 0.1, 0.6]],
    }]}
    return image, source, prep


def test_reading_evidence_contains_only_exact_nearest_neighbor_physical_print():
    original, source, prep = fixture()
    evidence = source_print_evidence.crops((source,), prep)[0]
    with Image.open(BytesIO(evidence.payload)) as sheet:
        expected = original.crop((10, 20, 20, 60)).resize(
            (80, 320), Image.Resampling.NEAREST
        )
        assert sheet.crop((72, 72, 152, 392)).tobytes() == expected.tobytes()
        assert sheet.getpixel((0, 0)) == (238, 238, 238)
    assert evidence.sha256 == hashlib.sha256(evidence.payload).hexdigest()


def test_actual_edge_glyphs_remain_visible_in_the_original_evidence_margin():
    original, source, prep = fixture()
    original.putpixel((9, 61), (241, 19, 77))
    view = source_pixels.artifact(original, "source-with-edge-glyph.png")
    source.payload, source.sha256 = view.payload, view.sha256
    evidence = source_print_evidence.crops((source,), prep)[0]
    with Image.open(BytesIO(evidence.payload)) as sheet:
        assert sheet.getpixel((64, 400)) == (241, 19, 77)


@pytest.mark.parametrize("fault", ["missing", "overlap", "outside", "hash"])
def test_print_evidence_rejects_missing_duplicated_or_foreign_source_surfaces(fault):
    _, source, prep = fixture()
    region = prep["selected_regions"][0]
    if fault == "missing":
        region["print_regions"] = []
    elif fault == "overlap":
        region["print_regions"] *= 2
    elif fault == "outside":
        region["print_regions"] = [[-0.1, 0.2, 0.2, 0.2, 0.2, 0.6, -0.1, 0.6]]
    else:
        source.sha256 = "f" * 64
    with pytest.raises(ValueError):
        source_print_evidence.crops((source,), prep)


def test_angled_print_columns_use_disjoint_real_surfaces_without_clipping_glyphs():
    _, source, prep = fixture()
    region = prep["selected_regions"][0]
    # Axis-aligned bounds overlap; these separate photographed columns do not.
    region["print_regions"] = [
        [0.1, 0.1, 0.2, 0.1, 0.5, 0.9, 0.4, 0.9],
        [0.25, 0.1, 0.35, 0.1, 0.65, 0.9, 0.55, 0.9],
    ]
    assert len(source_print_evidence.crops((source,), prep)) == 1
