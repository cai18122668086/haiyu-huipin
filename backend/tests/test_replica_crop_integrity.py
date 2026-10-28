"""A physical source crop must pass before planning; no invented repairs."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

from app.replica_crop_integrity import (
    SourceCropIncomplete,
    _review,
    validate_source_crops,
)
from app.replica_exact_compact import CompactResponseInvalid
from app.replica_exact_contracts import canonical_hash
from app.replica_native import digest

from .test_replica_compact import setup_run


def crop_fixture(tmp_path):
    run, gateway, _, _ = setup_run(tmp_path)
    (run.root / "contracts").mkdir(exist_ok=True)
    path = run.root / "original.png"
    image = Image.new("RGB", (256, 640), "blue")
    image.paste("red", (0, 0, 256, 400))
    image.save(path)
    column = {
        "column_id": "C001",
        "width": 256,
        "height": 640,
        "crop_path": str(path),
    }
    proposal = {
        "column_id": "C001",
        "boundary_ys": [320],
        "screens": [
            {"headline": "A", "buyer_question": "First question"},
            {"headline": "B", "buyer_question": "Second question"},
        ],
    }
    return run, gateway, column, {"columns": [proposal]}


def revise(result, cut=400):
    result.payload.update(
        decision="REVISE",
        boundary_ys=[cut],
        dependent_copy_complete=False,
        evidence=(
            "The first crop clips its dependent caption; "
            "move the cut below the complete first panel."
        ),
    )
    return result


def test_revised_actual_pixels_are_reviewed_again_before_planning(tmp_path):
    run, gateway, column, split = crop_fixture(tmp_path)
    old = gateway.plan
    shapes = []

    async def inspect(request):
        data = json.loads(request.product_information[0])["context"]
        shapes.append(deepcopy(data["boundary_ys"]))
        result = await old(request)
        return revise(result) if len(shapes) == 1 else result

    gateway.plan = inspect
    original = Path(column["crop_path"]).read_bytes()
    result = asyncio.run(validate_source_crops(run, [column], split))
    assert result["columns"][0]["boundary_ys"] == [400]
    assert split["columns"][0]["boundary_ys"] == [320]
    assert shapes == [[320], [400]]
    assert Image.open(run.root / "crops/source-review-C001-0-0.png").size == (256, 320)
    assert Image.open(run.root / "crops/source-review-C001-1-0.png").size == (256, 400)
    records = run.context["source_crop_integrity"]
    assert [r["raw"]["decision"] for r in records] == ["REVISE", "PASS"]
    assert records[0]["source_sha256"][0] == digest(original)
    assert records[0]["source_sha256"][1] != records[1]["source_sha256"][1]
    assert Path(column["crop_path"]).read_bytes() == original
    assert len(result["columns"][0]["screens"]) == 2
    assert not gateway.image_requests


def test_persistent_cut_failure_stops_actual_prepare_before_plan(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    old = gateway.plan

    async def inspect(request):
        result = await old(request)
        if json.loads(request.product_information[0])["stage"] == "crop_integrity":
            revise(result, 400 if result.payload["boundary_ys"] == [320] else 420)
        return result

    gateway.plan = inspect
    with pytest.raises(SourceCropIncomplete, match="C001.*dependent caption"):
        asyncio.run(run.prepare(payload, records))
    phases = [json.loads(r.product_information[0])["stage"] for r in gateway.requests]
    assert phases.count("crop_integrity") == 2
    assert "plan" not in phases and "review_plan" not in phases
    assert not gateway.image_requests
    assert not run.checkpoints["source_crop_passes"]


def test_repaired_crop_is_the_actual_reference_used_by_complete_plan(tmp_path):
    run, gateway, payload, records = setup_run(tmp_path)
    old = gateway.plan
    count = 0

    async def inspect(request):
        nonlocal count
        result = await old(request)
        if json.loads(request.product_information[0])["stage"] == "crop_integrity":
            count += 1
            if count == 1:
                revise(result)
        return result

    gateway.plan = inspect
    asyncio.run(run.prepare(payload, records))
    modules = run.state()["plan"]["modules"]
    assert len(modules) == 2 and count == 2
    assert Image.open(modules[0]["reference_path"]).size == (256, 400)
    assert Image.open(modules[1]["reference_path"]).size == (256, 240)
    phases = [json.loads(r.product_information[0])["stage"] for r in gateway.requests]
    assert phases.index("plan") > max(
        i for i, p in enumerate(phases) if p == "crop_integrity"
    )
    assert not gateway.image_requests


@pytest.mark.parametrize(
    "invalid", ["contradictory-pass", "wrong-count", "out-of-bounds"]
)
def test_invalid_verdict_cannot_authorize_planning(tmp_path, invalid):
    run, gateway, column, split = crop_fixture(tmp_path)
    old = gateway.plan

    async def inspect(request):
        result = await old(request)
        if invalid == "contradictory-pass":
            result.payload["headlines_attached"] = False
        elif invalid == "wrong-count":
            result.payload["screen_count"] = 3
        else:
            revise(result, 700)
        return result

    gateway.plan = inspect
    with pytest.raises(CompactResponseInvalid, match="Physical source crop response"):
        asyncio.run(validate_source_crops(run, [column], split))
    assert len(gateway.requests) == 2
    assert json.loads(gateway.requests[1].product_information[0])["validation_error"]
    assert not gateway.image_requests


def test_cached_pass_requires_same_actual_pixels_and_schema(tmp_path):
    run, gateway, column, split = crop_fixture(tmp_path)
    proposal = split["columns"][0]
    paths = [Path(column["crop_path"])]
    for top, bottom in [(0, 320), (320, 640)]:
        target = run.root / f"cut-{top}.png"
        run.crop(paths[0], [0, top, 256, bottom], target)
        paths.append(target)
    first = asyncio.run(_review(run, column, proposal, paths))
    assert asyncio.run(_review(run, column, proposal, paths)) == first
    assert len(gateway.requests) == 1
    Image.new("RGB", (256, 320), "green").save(paths[1])
    asyncio.run(_review(run, column, proposal, paths))
    assert len(gateway.requests) == 2


@pytest.mark.parametrize("tamper", ["hash", "contradiction", "revise"])
def test_cached_tampering_never_bypasses_complete_pass(tmp_path, tamper):
    run, gateway, column, split = crop_fixture(tmp_path)
    asyncio.run(validate_source_crops(run, [column], split))
    cached = next(iter(run.checkpoints["source_crop_passes"].values()))
    cached["raw"]["headlines_attached"] = False
    if tamper == "revise":
        cached["raw"]["decision"] = "REVISE"
    if tamper != "hash":
        cached["response_sha256"] = canonical_hash(cached["raw"])
    with pytest.raises(CompactResponseInvalid, match="Cached|Only a real"):
        asyncio.run(validate_source_crops(run, [column], split))
    assert len(gateway.requests) == 1 and not gateway.image_requests
