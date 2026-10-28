"""Synthetic coordinate/flow regressions; these do not assert visual quality."""

import asyncio
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.replica_native import (
    NativeReplicaRun,
    ReplicaAnalysisUnavailable,
    ReplicaInputTooLarge,
    ReplicaPlanAuditRejected,
    ReplicaSegmentationInvalid,
    digest,
    segmentation_errors,
)

from .test_replica import upload


def observation(sizes):
    return {
        "product_geometry": [
            "Synthetic geometry observation " + str(i) for i in range(3)
        ],
        "product_crops": [{"product_index": 0, "bbox": [0, 0, 64, 64]}],
        "units": [
            {
                "reference_index": i,
                "column_index": 0,
                "screen_index": 0,
                "bbox": [0, 0, w, h],
                "screen_boundary_reason": "Synthetic single observed screen",
            }
            for i, (w, h) in enumerate(sizes)
        ],
        "route": "CAROUSEL_SET",
    }


def intake(tmp_path, sizes):
    data = {}
    records = {}
    for name, size in [("product", (64, 64))] + [
        (f"ref-{i}", size) for i, size in enumerate(sizes)
    ]:
        stream = BytesIO()
        Image.new("RGB", size, "blue").save(stream, "PNG")
        data[name] = stream.getvalue()
        records[name] = (name, "image/png", digest(data[name]), name, *size)

    async def read_bytes(key):
        return data[key]

    run = NativeReplicaRun(
        "exact",
        "rep_" + uuid4().hex,
        "ws_test",
        None,
        SimpleNamespace(read_bytes=read_bytes),
        root=tmp_path,
    )
    payload = {
        "product_asset_ids": ["product"],
        "references": [{"asset_id": f"ref-{i}"} for i in range(len(sizes))],
        "facts": "",
        "language": "zh-CN",
        "platform_code": "tmall",
    }
    return run, payload, records


def test_real_boundary_shape_is_rejected_then_corrected_before_any_crop(tmp_path):
    sizes = [(1440, 1440)] * 5 + [(2480, 2480)] * 4 + [(1440, 1440), (1000, 1300)]
    valid = observation(sizes)
    invalid = deepcopy(valid)
    invalid["units"][5]["bbox"] = [298, 132, 2159, 2500]
    run, payload, records = intake(tmp_path, sizes)
    calls = []

    async def ask(stage, instruction, context, *, paths):
        assert len(paths) == 12
        assert not (tmp_path / "crops").exists()
        assert context["reference_dimensions"][5] == {
            "reference_index": 5,
            "width": 2480,
            "height": 2480,
        }
        calls.append(stage)
        if len(calls) == 1:
            return deepcopy(invalid)
        assert "2500" in context["validation_errors"][0]
        assert "2480x2480" in context["validation_errors"][0]
        assert context["invalid_observation"] == invalid
        return deepcopy(valid)

    async def prepare_exact(products, references, units, geometry):
        assert units == valid["units"]
        assert len(references) == 11
        assert products[0].is_file()
        return [{"fixture": "validated native planning boundary"}]

    run.ask, run._prepare_exact = ask, prepare_exact
    assert asyncio.run(run.prepare(payload, records)) == [
        {"fixture": "validated native planning boundary"}
    ]
    assert calls == ["identity-segmentation", "identity-segmentation-repair-1"]
    assert invalid["units"][5]["bbox"][3] == 2500  # No silent coordinate clamping.


def test_invalid_segmentation_stops_after_three_calls_without_partial_crops(tmp_path):
    run, payload, records = intake(tmp_path, [(2480, 2480)])
    invalid = observation([(2480, 2480)])
    invalid["units"][0]["bbox"][3] = 2500
    calls = []

    async def ask(stage, *args, **kwargs):
        calls.append(stage)
        return deepcopy(invalid)

    run.ask = ask
    with pytest.raises(ReplicaSegmentationInvalid) as error:
        asyncio.run(run.prepare(payload, records))
    assert error.value.code == "REPLICA_SEGMENTATION_INVALID"
    assert len(calls) == 3
    assert not (tmp_path / "crops").exists()
    assert not (tmp_path / "session.json").exists()
    assert run.context["references"] == payload["references"]


@pytest.mark.parametrize(
    "bbox",
    [
        None,
        [0, 0, 2480, 2500],
        [0, 0, 2480.0, 2480],
        [False, 0, 2480, 2480],
        [-1, 0, 2480, 2480],
        [0, 0, 0, 2480],
        [0, 0, 2480],
    ],
)
def test_malformed_or_out_of_bounds_coordinates_are_not_accepted(bbox):
    value = observation([(2480, 2480)])
    value["units"][0]["bbox"] = bbox
    assert segmentation_errors(value, [(64, 64)], [(2480, 2480)])


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_index", True),
        ("reference_index", 1),
        ("column_index", None),
        ("screen_index", -1),
        ("screen_boundary_reason", None),
    ],
)
def test_unit_fields_fail_validation_before_sorting_or_indexing(field, value):
    result = observation([(1000, 1300)])
    result["units"][0][field] = value
    assert segmentation_errors(result, [(64, 64)], [(1000, 1300)])


def test_all_references_and_product_images_must_be_covered():
    result = observation([(1440, 1440), (2480, 2480)])
    assert segmentation_errors(result, [(64, 64)], [(1440, 1440), (2480, 2480)]) == []
    assert segmentation_errors(
        result, [(64, 64), (64, 64)], [(1440, 1440), (2480, 2480)]
    )
    result["units"].pop()
    assert segmentation_errors(result, [(64, 64)], [(1440, 1440), (2480, 2480)])


@pytest.mark.parametrize(
    "error_factory",
    [
        ReplicaSegmentationInvalid,
        ReplicaInputTooLarge,
        ReplicaAnalysisUnavailable,
        lambda: ReplicaPlanAuditRejected({"pass": False}),
    ],
)
def test_expected_failure_retains_stage_selection_and_retry_policy(
    client, monkeypatch, error_factory
):
    from app import replica_exact_compact

    # This regression models saved pre-pilot analysis and its native retry.
    # New submissions are exercised by the compact HTTP/worker regression.
    monkeypatch.setattr(replica_exact_compact, "VERSION", None)
    client.app.state.settings.reference_remix_provider_enabled = True
    client.app.state.replica_gateway = SimpleNamespace()
    product, reference = upload(client, "red"), upload(client, "blue")

    expected_error = error_factory()

    async def failed(self, payload, records):
        self.on_progress(15, "校正参考图拆屏坐标")
        raise expected_error

    monkeypatch.setattr(NativeReplicaRun, "prepare", failed)
    route = "/api/v1/workspaces/ws_test/replica"
    body = {
        "request_id": str(uuid4()),
        "replication_method": "exact",
        "product_asset_ids": [product],
        "references": [{"asset_id": reference}],
    }
    created = client.post(route + "/sessions", json=body).json()
    document = client.get(route + "/documents/" + created["id"]).json()
    assert document["status"] == "failed"
    assert document["payload"]["error_code"] == expected_error.code
    if isinstance(expected_error, ReplicaPlanAuditRejected):
        assert "请先调整" in document["payload"]["error"]
    else:
        assert document["payload"]["error"] == expected_error.message
    assert document["payload"]["analysis_stage"] == "校正参考图拆屏坐标"
    assert document["payload"]["progress"] == 15
    assert document["payload"]["product_asset_ids"] == [product]
    assert document["payload"]["references"][0]["asset_id"] == reference
    if isinstance(expected_error, ReplicaPlanAuditRejected):
        # Retaining input is not authority to replay an exhausted quality gate.
        retried = client.post(route + "/sessions/" + created["id"] + "/retry")
        assert retried.status_code == 409
        assert retried.json()["error"]["code"] == "REPLICA_PLAN_REVISION_REQUIRED"
        return

    async def prepared(self, payload, records):
        return [
            {
                "reference": payload["references"][0],
                "prompt": "synthetic fixture",
                "analysis": {},
                "unit_id": "M01",
            }
        ]

    async def snapshot(self):
        return {"schema": "replica-native-v1", "files": {}}

    monkeypatch.setattr(NativeReplicaRun, "prepare", prepared)
    monkeypatch.setattr(NativeReplicaRun, "snapshot", snapshot)
    retried = client.post(route + "/sessions/" + created["id"] + "/retry")
    assert retried.status_code == 202
    document = client.get(route + "/documents/" + created["id"]).json()
    assert document["status"] == "awaiting_confirmation"
    assert "error" not in document["payload"]
    assert document["payload"]["job_ids"] == []


@pytest.mark.parametrize("route", [None, [], {}, "unsupported"])
def test_invalid_route_returns_validation_error(route):
    result = observation([(1000, 1300)])
    result["route"] = route
    assert segmentation_errors(result, [(64, 64)], [(1000, 1300)])


def test_duplicate_screen_indices_are_not_accepted():
    result = observation([(1000, 1300)])
    result["units"].append(deepcopy(result["units"][0]))
    assert segmentation_errors(result, [(64, 64)], [(1000, 1300)])
