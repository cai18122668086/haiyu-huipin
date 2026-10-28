"""New synthetic jobs verify exact native delivery and second-only recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import select

from app.aurora_detail_runtime import detail_workspace
from app.models import AssetRecord, JobAssetRecord, JobItemRecord, JobRecord, utc_now
from app.provider import ProviderArtifact
from app.xinghe_agent import XingheImageProvider
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_execution import _aurora_approval_payload, _gateway
from tests.test_domestic_detail_plans import planned_job


def delivered(client, job_id):
    with client.app.state.session_factory() as db:
        rows = db.execute(
            select(AssetRecord.object_key, AssetRecord.sha256, JobItemRecord.ordinal)
            .join(JobAssetRecord, JobAssetRecord.asset_id == AssetRecord.id)
            .join(JobItemRecord, JobItemRecord.id == JobAssetRecord.job_item_id)
            .where(
                JobAssetRecord.job_id == job_id, JobAssetRecord.direction == "output"
            )
            .order_by(JobItemRecord.ordinal)
        ).all()
    return [
        (ordinal, sha, asyncio.run(client.app.state.storage.read_bytes(key)))
        for key, sha, ordinal in rows
    ]


def test_wrong_size_rgba_is_normalized_once_audited_delivered_and_recovered_exactly(
    client, monkeypatch
):
    planned = planned_job(client)
    root = detail_workspace(planned["id"])
    gateway = _gateway(client)
    # Explicit synthetic mode exercises output persistence and permits this
    # test's synthetic pixel observer under the accepted-review authority.
    # The network guard and local fake still prohibit any supplier call.
    monkeypatch.setattr(gateway, "provider_name", "synthetic")
    original_edit = gateway.edit
    originals = []
    observed = []

    async def provider(request):
        result = await original_edit(request)
        image = Image.new("RGBA", (240, 640), (20, 50, 80, 180))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 315, 239, 325), fill=(240, 180, 20, 255))
        output = BytesIO()
        image.save(output, format="PNG")
        payload = output.getvalue()
        originals.append(payload)
        return replace(
            result,
            artifact=ProviderArtifact(
                payload=payload,
                filename="supplier-original.png",
                content_type="image/png",
                width=240,
                height=640,
                sha256=hashlib.sha256(payload).hexdigest(),
            ),
        )

    async def review(self, request, native_request, sources, artifact):
        lineage = native_request["pixel_lineage"]
        assert artifact.sha256 == lineage["canvas"]["sha256"]
        assert artifact.payload != originals[-1]
        assert lineage["source"]["sha256"] == hashlib.sha256(originals[-1]).hexdigest()
        assert (artifact.width, artifact.height) == (1440, 3840)
        observed.append((native_request["pair_id"], artifact.sha256))
        return self._native_detail_judgment(native_request, artifact)

    monkeypatch.setattr(gateway, "edit", provider)
    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", review
    )
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}"
    ).json()
    assert current["status"] == "awaiting_final_review", current
    assert len(gateway.image_calls) == len(observed) == 4
    checkpoint_path = root / "session.json"
    checkpoint = checkpoint_path.read_bytes()
    session = json.loads(checkpoint)
    outputs = delivered(client, planned["id"])
    assert len(outputs) == 8
    for ordinal, sha, payload in outputs:
        module = session["modules"][ordinal]
        assert payload == Path(module["accepted_asset"]).read_bytes()
        assert sha == hashlib.sha256(payload).hexdigest()
    for module in session["modules"][::2]:
        attempt = module["attempts"][-1]
        assert Path(attempt["source_image"]).read_bytes() in originals
        assert attempt["source_image_sha256"] != attempt["normalized_image_sha256"]
        assert attempt["artifact_lineage_sha256"]
    before = len(gateway.image_calls)
    second_id = current["items"][1]["id"]
    # Simulate a projection/storage completion failure only in this newly
    # created synthetic job; the accepted native checkpoint stays immutable.
    with client.app.state.session_factory() as db:
        job = db.get(JobRecord, planned["id"])
        job.status, job.stage = "partial", "partial"
        job.succeeded_item_count, job.failed_item_count = 7, 1
        job.finished_at = utc_now()
        item = db.get(JobItemRecord, second_id)
        item.status, item.error_code = "failed", "OUTPUT_STORAGE_FAILED"
        db.commit()
    retried = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/retry",
        headers=idem(),
        json={"scope": "selected", "item_ids": [second_id]},
    )
    assert retried.status_code == 201, retried.text
    target = retried.json()
    dispatch_pending(client)
    recovered = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target['id']}"
    ).json()
    assert recovered["status"] == "awaiting_final_review", recovered
    assert recovered["items"][1]["status"] == "succeeded"
    assert len(gateway.image_calls) == before
    target_outputs = delivered(client, target["id"])
    assert len(target_outputs) == 8
    assert target_outputs[1][2] == outputs[1][2]
    assert checkpoint_path.read_bytes() == checkpoint
