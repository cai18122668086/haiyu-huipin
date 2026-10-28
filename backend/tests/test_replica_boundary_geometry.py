"""Safe pixel geometry keeps independent counts, evidence and immutable sources."""

import asyncio
import json
from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.replica_boundary_geometry import POLICY, resolve_boundaries
from app.replica_exact_compact import run_class
from app.replica_native import ReplicaSegmentationInvalid, digest, read_json
from app.replica_screen_boundaries import disagreements
from tests.test_replica_compact import SyntheticGateway, setup_run


def fixture(tmp_path, *, bands=((231, 279),), cuts=(220, 250)):
    path = tmp_path / "column.png"
    image = Image.new("RGB", (200, 400), "#23679a")
    draw = ImageDraw.Draw(image)
    for top, bottom in bands:
        draw.rectangle((0, top, 199, bottom), fill="white")
    image.save(path)
    columns = [
        {
            "column_id": "C001",
            "reference_index": 0,
            "width": 200,
            "height": 400,
            "crop_path": str(path),
        }
    ]
    row = {"column_id": "C001", "screens": [{"headline": "A"}, {"headline": "B"}]}
    split = {"columns": [{**deepcopy(row), "boundary_ys": [cuts[0]]}]}
    inventory = {
        "all_columns_covered": True,
        "columns": [{**deepcopy(row), "boundary_ys": [cuts[1]]}],
    }
    return path, columns, split, inventory


def test_unique_source_gap_resolves_estimates_without_rewriting_either_model(tmp_path):
    path, columns, split, inventory = fixture(tmp_path)
    source_bytes, original = path.read_bytes(), deepcopy((split, inventory))
    assert disagreements(columns, split, inventory)
    proposed, observed, receipts = resolve_boundaries(columns, split, inventory)
    assert (split, inventory) == original
    assert not disagreements(columns, proposed, observed)
    cut = proposed["columns"][0]["boundary_ys"][0]
    assert 231 <= cut <= 244
    assert proposed["columns"][0]["screens"] == split["columns"][0]["screens"]
    assert observed["columns"][0]["screens"] == inventory["columns"][0]["screens"]
    assert receipts[0]["policy"] == POLICY
    assert receipts[0]["source_sha256"] == digest(source_bytes)
    assert receipts[0]["safe_blank_band"] == [231, 274]
    assert path.read_bytes() == source_bytes


@pytest.mark.parametrize("cuts", [(220, 220), (220, 225)])
def test_even_agreeing_estimates_are_relocated_to_a_safe_gap(tmp_path, cuts):
    _, columns, split, inventory = fixture(tmp_path, cuts=cuts)
    assert not disagreements(columns, split, inventory)
    proposed, observed, receipts = resolve_boundaries(
        columns, split, inventory, include_agreed=True
    )
    assert len(receipts) == 1 and receipts[0]["original_estimates_agreed"] is True
    assert 231 <= proposed["columns"][0]["boundary_ys"][0] <= 244
    assert not disagreements(columns, proposed, observed)


@pytest.mark.parametrize("kind", ["no-gap", "ambiguous", "far", "source-size", "count"])
def test_uncertain_or_count_disagreements_cannot_be_guessed_away(tmp_path, kind):
    bands = (
        ()
        if kind == "no-gap"
        else ((210, 219), (230, 239))
        if kind == "ambiguous"
        else ((231, 279),)
    )
    cuts = (
        (222, 240)
        if kind == "ambiguous"
        else (80, 320)
        if kind == "far"
        else (220, 250)
    )
    _, columns, split, inventory = fixture(tmp_path, bands=bands, cuts=cuts)
    if kind == "source-size":
        columns[0]["width"] = 201
    if kind == "count":
        inventory["columns"][0]["screens"].append({"headline": "C"})
        inventory["columns"][0]["boundary_ys"] = [200, 300]
    original = deepcopy((split, inventory))
    proposed, observed, receipts = resolve_boundaries(columns, split, inventory)
    assert not receipts and (proposed, observed) == original
    assert disagreements(columns, proposed, observed)


def test_multiple_safe_gaps_keep_the_same_screen_count_and_source_order(tmp_path):
    path, columns, split, inventory = fixture(tmp_path)
    image = Image.new("RGB", (200, 1000), "#23679a")
    draw = ImageDraw.Draw(image)
    for top, bottom in ((301, 340), (651, 690)):
        draw.rectangle((0, top, 199, bottom), fill="white")
    image.save(path)
    columns[0]["height"] = 1000
    for value, cuts in ((split, [280, 630]), (inventory, [320, 670])):
        value["columns"][0]["screens"].append({"headline": "C"})
        value["columns"][0]["boundary_ys"] = cuts
    proposed, observed, receipts = resolve_boundaries(columns, split, inventory)
    assert len(receipts) == 2 and not disagreements(columns, proposed, observed)
    cuts = proposed["columns"][0]["boundary_ys"]
    assert 301 <= cuts[0] <= 340 and 651 <= cuts[1] <= 690
    assert (
        len(proposed["columns"][0]["screens"])
        == len(observed["columns"][0]["screens"])
        == 3
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_actual_prepare_keeps_raw_observations_and_complete_source_crops(
    tmp_path, legacy
):
    gateway = SyntheticGateway()
    original = gateway.plan

    async def approximate(req):
        result = await original(req)
        stage = json.loads(req.product_information[0])["stage"]
        if stage in {"screen_split", "screen_inventory"}:
            for column in result.payload["columns"]:
                column["boundary_ys"] = [280 if stage == "screen_split" else 320]
        return result

    gateway.plan = approximate
    run, _, payload, records = setup_run(tmp_path / "native", gateway)
    image = Image.new("RGB", (256, 640), "#23679a")
    ImageDraw.Draw(image).rectangle((0, 300, 255, 339), fill="white")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    source = buffer.getvalue()
    old_read = run.storage.read_bytes

    async def read(key):
        return source if key == "asset-1" else await old_read(key)

    run.storage.read_bytes = read
    records["asset-1"] = ("asset-1", "image/png", digest(source), "asset-1", 256, 640)
    if legacy:
        run = run_class("exact", "exact-compact-v9")(
            "exact", run.document_id, "ws_test", gateway, run.storage, root=run.root
        )
        with pytest.raises(ReplicaSegmentationInvalid):
            asyncio.run(run.prepare(payload, records))
        return
    plans = asyncio.run(run.prepare(payload, records))
    assert len(plans) == 2 and run.state()["state"] == "READY"
    proof = read_json(run.root / "screen-inventory.json")
    receipts = proof["diagnostics"]["source_pixel_boundary_resolutions"]
    assert len(receipts) == 1 and receipts[0]["safe_blank_band"] == [300, 339]
    assert proof["original_model_observations"]["initial_split"]["columns"][0][
        "boundary_ys"
    ] == [280]
    assert proof["original_model_observations"]["initial_independent_inventory"][
        "columns"
    ][0]["boundary_ys"] == [320]
    cut = receipts[0]["selected_y"]
    units = read_json(run.root / "identity-segmentation.json")["units"]
    assert units[0]["bbox"] == [0, 0, 256, cut]
    assert units[1]["bbox"] == [0, cut, 256, 640]
    assert awaitable_bytes(run.storage.read_bytes("asset-1")) == source
    assert not gateway.image_requests


def awaitable_bytes(coroutine):
    return asyncio.run(coroutine)
