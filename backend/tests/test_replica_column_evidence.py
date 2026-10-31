"""Independent screen observations receive unbiased geometry and intact pixels."""

import asyncio
import hashlib
import json
import sys
from copy import deepcopy

import pytest
from PIL import Image, ImageDraw

from app.replica_column_evidence import GUTTER, add_evidence, blank_bands, model_column
from app.replica_direct_delivery import V14_BINDING, V15_BINDING
from app.replica_exact_compact import protocol_binding, run_class
from app.replica_native import ReplicaSegmentationInvalid, read_json
from tests.test_replica_compact import setup_run


def test_column_rationale_cannot_leak_screen_verdicts_to_either_observer(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    original = gateway.plan

    async def biased_intake(request):
        result = await original(request)
        if json.loads(request.product_information[0])["stage"] == "intake":
            for c in result.payload["columns"]:
                c["column_boundary_reason"] = (
                    "One commercial screen; never split this column"
                )
        return result

    gateway.plan = biased_intake
    asyncio.run(run.prepare(payload, records))
    observations = [
        r
        for r in gateway.requests
        if json.loads(r.product_information[0])["stage"]
        in {"screen_split", "screen_inventory"}
    ]
    assert len(observations) == 2
    for request in observations:
        envelope = json.loads(request.product_information[0])
        assert "One commercial screen" not in request.product_information[0]
        assert "column_boundary_reason" not in request.product_information[0]
        c = envelope["context"]["columns"][0]
        assert c["coordinate_evidence"]["source_pixels_modified"] is False
        guides = [
            r
            for r in envelope["image_order"]
            if r["role"] == "reference_column_coordinate_guide"
        ]
        assert len(guides) == 1
        assert guides[0]["column_id"] == c["column_id"]
        assert guides[0]["source_content_box"][0] == GUTTER
    assert run.context["reference_columns"][0]["column_boundary_reason"].startswith(
        "One commercial screen"
    )
    assert not gateway.image_requests


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
@pytest.mark.parametrize("height", [640, 1580])
def test_coordinate_guide_preserves_every_source_pixel_and_unchanged_y(
    tmp_path, mode, height
):
    path = tmp_path / "original.png"
    image = Image.new(mode, (240, height), "#24749b")
    ImageDraw.Draw(image).rectangle(
        (0, height * 2 // 3, 239, height * 2 // 3 + 39), fill="white"
    )
    image.save(path)
    raw = path.read_bytes()
    column = {
        "column_id": "C005",
        "reference_index": 4,
        "column_index": 0,
        "bbox": [0, 0, 240, height],
        "width": 240,
        "height": height,
        "crop_path": str(path),
    }
    result = add_evidence(column, tmp_path / "guides")
    evidence = result["coordinate_evidence"]
    assert (
        path.read_bytes() == raw
        and evidence["source_sha256"] == hashlib.sha256(raw).hexdigest()
    )
    with Image.open(result["coordinate_guide_path"]) as guide:
        assert guide.size == (240 + GUTTER, height)
        assert (
            guide.crop(tuple(evidence["source_content_box"])).tobytes()
            == image.convert("RGBA").tobytes()
        )
    assert evidence["blank_bands"] == [
        {
            "top_y": height * 2 // 3,
            "bottom_y": height * 2 // 3 + 39,
            "midpoint_y": round(height * 2 // 3 + 19.5),
        }
    ]
    assert add_evidence(column, tmp_path / "guides") == result
    model = model_column(result)["coordinate_evidence"]
    assert model["blank_bands"] == evidence["blank_bands"]
    assert model["advisory_geometry_only"] is True
    assert "sha256" not in json.dumps(model)


def test_whitespace_candidates_do_not_imply_screen_count_or_change_evidence(tmp_path):
    image = Image.new("RGB", (200, 800), "#24749b")
    draw = ImageDraw.Draw(image)
    for band in ((100, 130), (350, 390), (650, 700)):
        draw.rectangle((0, band[0], 199, band[1]), fill="white")
    bands = blank_bands(image)
    assert len(bands) == 3
    assert all(set(b) == {"top_y", "bottom_y", "midpoint_y"} for b in bands)


def test_real_count_disagreement_still_stops_at_existing_bound_not_blank_band_count(
    tmp_path,
):
    run, gateway, payload, records = setup_run(tmp_path)
    original = gateway.plan

    async def mismatch(request):
        result = await original(request)
        if json.loads(request.product_information[0])["stage"] == "screen_inventory":
            for c in result.payload["columns"]:
                c["screens"] = c["screens"][:1]
                c["boundary_ys"] = []
        return result

    gateway.plan = mismatch
    with pytest.raises(ReplicaSegmentationInvalid):
        asyncio.run(run.prepare(payload, records))
    assert (
        len(
            [
                r
                for r in gateway.requests
                if json.loads(r.product_information[0])["stage"] == "screen_split"
            ]
        )
        == 2
    )
    assert (
        len(
            [
                r
                for r in gateway.requests
                if json.loads(r.product_information[0])["stage"] == "screen_inventory"
            ]
        )
        == 2
    )
    assert (
        read_json(run.root / "screen-inventory.json")["diagnostics"]["columns"][0][
            "reason"
        ]
        == "count"
    )
    assert not gateway.image_requests


def test_v14_archive_and_new_protocol_keep_separate_hashes():
    old = run_class("exact", V14_BINDING)
    assert sys.modules[old.__module__].protocol_binding() == V14_BINDING
    assert protocol_binding()["version"] == "exact-compact-v17"


def test_v15_published_snapshot_still_resolves_to_frozen_protocol():
    old = run_class("exact", V15_BINDING)
    module = sys.modules[old.__module__]
    assert module.protocol_binding() == V15_BINDING
    assert old is not run_class("exact", protocol_binding())
    current = sys.modules[run_class("exact", protocol_binding()).__module__]
    assert module.PROMPTS != current.PROMPTS


def test_model_column_excludes_any_free_text_or_prior_proposal():
    column = {
        "column_id": "C005",
        "reference_index": 4,
        "column_index": 0,
        "bbox": [0, 0, 790, 1580],
        "width": 790,
        "height": 1580,
        "column_boundary_reason": "one screen",
        "screens": ["one"],
        "prior_count": 1,
        "crop_path": "private/path",
        "previous_plan": "simplified",
    }
    original = deepcopy(column)
    result = model_column(column)
    assert set(result) == {
        "column_id",
        "reference_index",
        "column_index",
        "width",
        "height",
    }
    assert column == original
