"""Actual durable review packets and master bytes; no external suppliers."""

from __future__ import annotations

import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.aurora_detail_runtime import detail_workspace
from app.models import AssetRecord, JobAssetRecord
from app.storage import S3ImageStorage
from tests.test_api import WORKSPACE_ID, idem
from tests.test_domestic_detail_delivery import (
    APPROVAL,
    REVIEWS,
    explicit_synthetic_delivery,  # noqa: F401
    finalize,
    native_state,
    ready,
)
from tests.test_domestic_detail_execution import _gateway


def prepare(client, job_id):
    return client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}/detail-final-review"
    )


def counts(client):
    gateway = _gateway(client)
    return len(gateway.planner_calls), len(gateway.image_calls)


def test_review_registers_current_masters_once_and_downloads_exact_native_delivery(
    client,
):
    job = ready(client)
    original = (detail_workspace(job["id"]) / "session.json").read_bytes()
    before = counts(client)
    packet = prepare(client, job["id"]).json()
    assert prepare(client, job["id"]).json() == packet
    assert len(packet["screens"]) == 8 and len(packet["pair_canvases"]) == 4
    assert packet["campaign_audit_status"] == "NOT_REQUESTED"
    assert "整页节奏、组间接缝和长图审核未执行" in packet["document_markdown"]
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert len([a for a in current["assets"] if a["role"] == "detail_pair_canvas"]) == 4
    assert current["output_asset_count"] == 8
    master = packet["pair_canvases"][0]
    for variant in ("original", "thumbnail"):
        access = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/assets/{master['asset_id']}/access-url",
            json={"variant": variant, "disposition": "inline"},
        )
        assert access.status_code == 200, access.text
        response = client.get(access.json()["url"])
        assert response.status_code == 200, response.text
        if variant == "original":
            assert hashlib.sha256(response.content).hexdigest() == master["sha256"]
    assert (detail_workspace(job["id"]) / "session.json").read_bytes() == original
    assert counts(client) == before
    result = finalize(client, job["id"])
    assert result.status_code == 200, result.text
    assert (
        result.json()["plan"]["payload"]["final_manifest"]["final_review"]["sha256"]
        == packet["sha256"]
    )
    response = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/deliverables.zip"
    )
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len([n for n in names if n.startswith("screens/")]) == 8
        assert len([n for n in names if n.startswith("pair-masters/")]) == 4
        for row in packet["screens"]:
            payload = archive.read("screens/" + row["module_id"] + ".png")
            assert hashlib.sha256(payload).hexdigest() == row["sha256"]
        for row in packet["pair_canvases"]:
            payload = archive.read("pair-masters/" + row["pair_id"] + ".png")
            assert hashlib.sha256(payload).hexdigest() == row["sha256"]
        assert archive.read("final-review.md").decode() == packet["document_markdown"]
        assert json.loads(archive.read("final-review.json")) == packet
        assert (
            archive.read("native-final-manifest.json")
            == Path(native_state(job["id"])["final"]["manifest"]).read_bytes()
        )
    assert counts(client) == before


@pytest.mark.parametrize(
    "changed", ["missing_confirmation", "packet_document", "another_result"]
)
def test_finalization_rejects_missing_or_changed_human_review(client, changed):
    job = ready(client)
    before = counts(client)
    fingerprint = REVIEWS[job["id"]]
    if changed == "packet_document":
        path = (
            detail_workspace(job["id"]) / "final-review" / fingerprint / "packet.json"
        )
        value = json.loads(path.read_bytes())
        value["document_markdown"] = (
            "A shorter document with omitted material residuals"
        )
        path.write_text(json.dumps(value), encoding="utf-8")
    body = {"approval_text": APPROVAL}
    if changed != "missing_confirmation":
        body["final_review_sha256"] = (
            "f" * 64 if changed == "another_result" else fingerprint
        )
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/finalize",
        headers=idem(),
        json=body,
    )
    assert response.status_code == 409, response.text
    assert native_state(job["id"])["state"] == "AWAITING_USER_FINAL_REVIEW"
    assert counts(client) == before


def test_review_registration_recovers_commit_without_native_or_supplier_changes(
    client, monkeypatch
):
    job = ready(client, prepare=False)
    original = (detail_workspace(job["id"]) / "session.json").read_bytes()
    before = counts(client)
    commit = Session.commit

    def lose_commit(self):
        raise OSError("synthetic review registration interruption")

    monkeypatch.setattr(Session, "commit", lose_commit)
    with pytest.raises(OSError, match="registration interruption"):
        prepare(client, job["id"])
    monkeypatch.setattr(Session, "commit", commit)
    paths = list((detail_workspace(job["id"]) / "final-review").glob("*/packet.json"))
    assert len(paths) == 1
    saved = paths[0].read_bytes()
    with client.app.state.session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(JobAssetRecord)
                .where(JobAssetRecord.role == "detail_pair_canvas")
            )
            == 0
        )
    response = prepare(client, job["id"])
    assert response.status_code == 200, response.text
    assert paths[0].read_bytes() == saved
    assert response.json()["sha256"] == json.loads(saved)["sha256"]
    with client.app.state.session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(JobAssetRecord)
                .where(JobAssetRecord.role == "detail_pair_canvas")
            )
            == 4
        )
    assert (detail_workspace(job["id"]) / "session.json").read_bytes() == original
    assert counts(client) == before


def test_master_access_rejects_changed_valid_raster_instead_of_serving_wrong_bytes(
    client,
):
    job = ready(client)
    master = prepare(client, job["id"]).json()["pair_canvases"][0]
    before = counts(client)
    with client.app.state.session_factory() as db:
        asset = db.get(AssetRecord, master["asset_id"])
        path = client.app.state.storage.root / asset.object_key
    with Image.open(path) as source:
        image = source.copy()
    image.putpixel((0, 0), (7, 9, 11))
    image.save(path, "PNG")
    access = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/assets/{master['asset_id']}/access-url",
        json={"variant": "original", "disposition": "inline"},
    )
    response = client.get(access.json()["url"])
    assert response.status_code == 409, response.text
    assert finalize(client, job["id"]).status_code == 409
    assert counts(client) == before


@pytest.mark.parametrize("fault", [None, "wrong_version", "wrong_headers"])
def test_s3_master_reader_pins_actual_version_and_rejects_inconsistent_metadata(fault):
    output = BytesIO()
    Image.new("RGB", (3, 8), "navy").save(output, "PNG")
    payload = output.getvalue()
    calls = []

    class FakeS3:
        def get_object(self, **parameters):
            calls.append(parameters)
            return {
                "Body": BytesIO(payload),
                "VersionId": "v-other" if fault == "wrong_version" else "v-accepted",
                "ContentType": "image/jpeg"
                if fault == "wrong_headers"
                else "image/png",
                "ContentLength": len(payload),
                "ETag": '"exact-etag"',
            }

    storage = object.__new__(S3ImageStorage)
    storage.client = FakeS3()
    storage.bucket = storage.bucket_name = "isolated-test-bucket"
    storage.provider_name = "s3"
    call = storage.read_stored_image("ws_test/job/master.png", "v-accepted")
    if fault:
        with pytest.raises(ValueError):
            asyncio.run(call)
    else:
        actual, metadata = asyncio.run(call)
        assert actual == payload
        assert metadata.object_version == "v-accepted" and metadata.etag == "exact-etag"
        assert (metadata.width, metadata.height, metadata.sha256) == (
            3,
            8,
            hashlib.sha256(payload).hexdigest(),
        )
    assert calls == [
        {
            "Bucket": "isolated-test-bucket",
            "Key": "ws_test/job/master.png",
            "VersionId": "v-accepted",
        }
    ]
