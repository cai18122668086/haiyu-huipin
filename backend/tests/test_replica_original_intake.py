"""Exact intake binds originals, retaining crops without duplicating model inputs."""

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.replica_native import digest, read_json
from app.replica_native_provider import NativeReplicaProvider

from .test_replica_resolution import result
from .test_replica_transport import run_at


@pytest.mark.parametrize("repair", [False, True])
def test_original_sha_and_dimensions_reach_native_intake_facts_and_image_calls(
    tmp_path, repair
):
    sizes = [(64, 64), (96, 80), (120, 100)]
    records, data = {}, {}
    for index, size in enumerate([*sizes, (100, 200)]):
        key = f"asset-{index}"
        buffer = BytesIO()
        image = Image.new("RGB", size, (index * 50, 30, 80))
        image.putpixel((0, 0), (255, 255, 255))
        image.save(buffer, "PNG")
        data[key] = buffer.getvalue()
        records[key] = (key, "image/png", digest(data[key]), key, *size)

    async def read_bytes(key):
        return data[key]

    run = run_at(tmp_path, "exact")
    run.storage = SimpleNamespace(read_bytes=read_bytes)
    payload = {
        "product_asset_ids": [f"asset-{i}" for i in range(3)],
        "references": [{"asset_id": "asset-3"}],
        "facts": "Provided synthetic facts",
        "language": "zh-CN",
        "platform_code": "tmall",
    }
    originals = [tmp_path / "inputs" / f"product-{i:02}.img" for i in range(3)]
    calls = []

    class EvidenceObserved(Exception):
        pass

    async def ask(stage, instruction, context, *, paths, **kwargs):
        calls.append(stage)
        if stage == "identity-segmentation":
            return {
                "product_geometry": [
                    f"Supported synthetic geometry {i}" for i in range(3)
                ],
                "product_crops": [
                    {"product_index": i, "bbox": [8, 8, w - 8, h - 8]}
                    for i, (w, h) in enumerate(sizes)
                ],
                "units": [
                    {
                        "reference_index": 0,
                        "column_index": 0,
                        "screen_index": 0,
                        "bbox": [0, 0, 100, 200],
                        "screen_boundary_reason": "One complete synthetic screen",
                    }
                ],
                "route": "DETAIL_PAGE",
            }
        assert stage == "exact-product-evidence-0"
        assert paths == originals
        assert [(r["width"], r["height"]) for r in context["products"]] == sizes
        assert [r["sha256"] for r in context["products"]] == [
            digest(data[f"asset-{i}"]) for i in range(3)
        ]
        raise EvidenceObserved()

    run.ask = ask
    with pytest.raises(EvidenceObserved):
        asyncio.run(run.prepare(payload, records))
    assert calls == ["identity-segmentation", "exact-product-evidence-0"]
    state = read_json(tmp_path / "session.json")
    assert state["input"]["product_images"] == list(map(str, originals))
    assert run.context["product_paths"] == list(map(str, originals))
    assert len(run.context["product_identity_paths"]) == 3
    for i, crop in enumerate(run.context["product_identity_paths"]):
        assert digest(Path(crop).read_bytes()) != digest(data[f"asset-{i}"])
        assert originals[i].read_bytes() == data[f"asset-{i}"]

    sent = []

    async def edit(request):
        sent.append(request)
        return result((64, 128))

    run.gateway = SimpleNamespace(edit=edit)
    provider = NativeReplicaProvider(None, run.gateway, None)

    async def save(*args, **kwargs):
        pass

    provider._save = save
    native = {
        "token": "native-original-test",
        "mode": "REPAIR" if repair else "INITIAL",
    }
    reference = tmp_path / "crops" / "screen-000.png"
    paths = [reference, *map(Path, state["input"]["product_images"])]
    if repair:
        draft = tmp_path / "draft.png"
        Image.new("RGB", (64, 128), "pink").save(draft)
        paths.insert(0, draft)
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_test",
        job_item_id="item_test",
        attempt_id="attempt_test",
    )
    asyncio.run(
        provider._render(
            run,
            request,
            native,
            "Reference image 1 is the binding composition blueprint;",
            paths,
            [64, 128],
            "M001",
        )
    )
    assert len(sent) == 1
    assert [s.asset_id for s in sent[0].sources] == list(map(str, paths))
    assert len(sent[0].sources) == (5 if repair else 4)
    assert [s.payload for s in sent[0].sources[-3:]] == [
        data[f"asset-{i}"] for i in range(3)
    ]
