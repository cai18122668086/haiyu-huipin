"""Audit file identity is server-bound; model judgments remain unchanged."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.replica_native import NativeReplicaRun, ReplicaNativeError, digest


def fixture(tmp_path, payload):
    calls, received = [], []

    async def plan(request):
        calls.append(request)
        return SimpleNamespace(
            payload=payload,
            error_code=None,
            normalized_usage={"reported": False},
            actual_cost_micros=None,
        )

    run = NativeReplicaRun(
        "exact",
        "rep_" + uuid4().hex,
        "ws_test",
        SimpleNamespace(plan=plan),
        None,
        root=tmp_path,
    )

    def cli(command, **kwargs):
        received.append(json.loads(kwargs["judgment"].read_text()))

    run.cli = cli
    reference, artwork = tmp_path / "reference.png", tmp_path / "artwork.png"
    Image.new("RGB", (64, 80), "red").save(reference)
    Image.new("RGB", (64, 80), "blue").save(artwork)
    context = {
        "module": {"reference_path": str(reference)},
        "artwork": str(artwork),
        "reference_units": [
            {
                "id": "R001",
                "crop_path": str(reference),
                "crop_sha256": digest(reference.read_bytes()),
            }
        ],
    }
    return run, calls, received, context, reference, artwork


def test_pixel_audit_binds_server_files_before_validating_model_placeholders(tmp_path):
    payload = {
        "reference_path": "../placeholder.png",
        "artwork_path": "/model/artwork.png",
        "reference_sha256": "placeholder",
        "artwork_sha256": "placeholder",
        "decision": "FAIL",
        "major_mismatches": ["Synthetic product geometry mismatch"],
        "comparisons": [{"dimension": "product_geometry_match", "match": False}],
    }
    run, calls, received, context, reference, artwork = fixture(tmp_path, payload)
    value = asyncio.run(
        run.contract(
            "exact-pixel-audit-fixture",
            "synthetic fixture",
            context,
            "audit-current",
            "judgment",
            paths=[reference, artwork],
        )
    )
    assert len(calls) == len(received) == 1
    assert value == received[0]
    assert value["reference_path"] == str(reference.resolve())
    assert value["artwork_path"] == str(artwork.resolve())
    assert value["reference_sha256"] == digest(reference.read_bytes())
    assert value["artwork_sha256"] == digest(artwork.read_bytes())
    for key in ["decision", "major_mismatches", "comparisons"]:
        assert value[key] == payload[key]


def test_plan_audit_binds_known_unit_before_validating_placeholder(tmp_path):
    run, calls, received, context, reference, _ = fixture(
        tmp_path,
        {
            "unit_audits": [
                {
                    "unit_id": "R001",
                    "reference_path": "../placeholder.png",
                    "decision": "FAIL",
                }
            ]
        },
    )
    value = asyncio.run(
        run.contract(
            "exact-plan-audit-fixture",
            "synthetic fixture",
            context,
            "audit-plan",
            "judgment",
            paths=[reference],
        )
    )
    assert len(calls) == len(received) == 1
    unit = value["unit_audits"][0]
    assert unit["reference_path"] == str(reference)
    assert unit["reference_sha256"] == digest(reference.read_bytes())
    assert unit["decision"] == "FAIL"


@pytest.mark.parametrize("kind", ["nested_path", "unknown_unit", "ordinary_ask"])
def test_binding_keeps_other_paths_untrusted(tmp_path, kind):
    payload = {
        "nested_path": {
            "reference_path": "../placeholder.png",
            "artwork_path": "../placeholder.png",
            "extra": {"source_crop_path": "../outside.png"},
        },
        "unknown_unit": {
            "unit_audits": [{"unit_id": "UNKNOWN", "reference_path": "../outside.png"}]
        },
        "ordinary_ask": {"reference_path": "../outside.png"},
    }[kind]
    run, calls, received, context, reference, _ = fixture(tmp_path, payload)
    if kind == "ordinary_ask":
        request = run.ask("identity", "fixture", {}, paths=[reference])
    else:
        request = run.contract(
            "synthetic-audit",
            "fixture",
            context,
            "audit-plan" if kind == "unknown_unit" else "audit-current",
            "judgment",
            paths=[reference],
        )
    with pytest.raises(ReplicaNativeError, match="目录"):
        asyncio.run(request)
    assert len(calls) == 1
    assert received == []
