"""Real owned asset receipts replay before any inherited pixel can be reused."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from app import source_pixels, white_product_lineage
from app.domestic_detail.plans import canonical_hash
from app.image_fidelity import FidelityGateway
from app.models import AssetRecord, JobAssetRecord, JobAttemptRecord, utc_now
from app.storage import ValidatedImage
from app.xinghe_agent import SourceImage
from tests.test_api import WORKSPACE_ID
from tests.test_customer_credit_billing import seed_job
from tests.test_image_fidelity import PixelGateway, request


@pytest.fixture
def ancestry(client):
    sessions, storage = client.app.state.session_factory, client.app.state.storage
    with sessions() as db:
        parent, items = seed_job(
            db,
            kind="product_image",
            count=1,
            deliverables=False,
            configuration={"settings": {"task_type": "white_background"}},
        )
        parent.status, items[0].status = "processing", "processing"
        parent_id, item_id, attempt_id = (
            parent.id,
            items[0].id,
            "attempt_" + uuid4().hex,
        )
        db.add(
            JobAttemptRecord(
                id=attempt_id,
                workspace_id=WORKSPACE_ID,
                job_id=parent_id,
                job_item_id=item_id,
                attempt_no=1,
                status="running",
                lease_token="unit",
                leased_by="unit",
                lease_expires_at=utc_now() + timedelta(minutes=2),
                provider="unit",
                operation="product_image",
                provider_idempotency_key="same-key",
            )
        )
        db.commit()

    def save(artifact, *, origin="generated"):
        asset_id = "asset_" + uuid4().hex
        key = f"{WORKSPACE_ID}/{asset_id}.png"
        stored = asyncio.run(
            storage.save_validated(
                ValidatedImage(
                    artifact.payload,
                    artifact.filename,
                    artifact.content_type,
                    artifact.width,
                    artifact.height,
                    key,
                    artifact.sha256,
                )
            )
        )
        with sessions() as db:
            db.add(
                AssetRecord(
                    id=asset_id,
                    workspace_id=WORKSPACE_ID,
                    origin_kind=origin,
                    lifecycle_status="ready",
                    original_filename=stored.filename,
                    content_type=stored.content_type,
                    byte_size=stored.byte_size,
                    width=stored.width,
                    height=stored.height,
                    sha256=stored.sha256,
                    storage_provider=stored.storage_provider,
                    storage_bucket=stored.storage_bucket,
                    object_key=stored.object_key,
                )
            )
            db.commit()
        return asset_id

    original = Image.new("RGB", (100, 100), "#778899")
    ImageDraw.Draw(original).rectangle((25, 10, 74, 89), fill="#cd324f")
    ImageDraw.Draw(original).rectangle((40, 40, 59, 49), fill="white")
    photo = source_pixels.artifact(original, "original.png")
    original_id = save(photo, origin="upload")
    with sessions() as db:
        db.add(
            JobAssetRecord(
                workspace_id=WORKSPACE_ID,
                job_id=parent_id,
                asset_id=original_id,
                direction="input",
                role="product_reference",
                ordinal=0,
                is_deliverable=False,
            )
        )
        db.commit()
    source = SourceImage(
        original_id,
        "primary",
        photo.filename,
        photo.content_type,
        photo.sha256,
        photo.payload,
        source_role="product_reference",
    )
    call = replace(
        request(PixelGateway()),
        workspace_id=WORKSPACE_ID,
        job_id=parent_id,
        job_item_id=item_id,
        attempt_id=attempt_id,
        sources=(source,),
    )
    guard = FidelityGateway(PixelGateway(), sessions, storage)
    rgba = original.convert("RGBA")
    alpha = Image.new("L", rgba.size)
    ImageDraw.Draw(alpha).rectangle((25, 10, 74, 89), fill=255)
    rgba.putalpha(alpha)
    layer = source_pixels.artifact(rgba, "original-alpha.png")
    layer_id = asyncio.run(guard._retain(call, layer, role="fidelity_source_cutout"))
    background = source_pixels.artifact(
        Image.new("RGB", (300, 300), "white"), "native-white.png"
    )
    asyncio.run(guard._retain(call, background, role="fidelity_background"))
    recipe = {
        "version": source_pixels.VERSION,
        "model_sha256": source_pixels.MODEL_SHA256,
        "layers": [
            {
                "asset_id": layer_id,
                "sha256": layer.sha256,
                "source_asset_id": original_id,
                "source_sha256": photo.sha256,
                "source_box": [0, 0, 100, 100],
            }
        ],
        "cells": [[0.1, 0.1, 0.9, 0.9]],
    }
    guard._frozen(call, {"pixel_recipe": recipe})
    output, proof = source_pixels.compose(background, (layer,), recipe["cells"])
    output_id = save(output)
    binding = white_product_lineage.pointer(call, recipe, proof, output.sha256)
    with sessions() as db:
        from app.models import JobItemRecord, JobRecord

        parent = db.get(JobRecord, parent_id)
        parent.status = "completed"
        parent.succeeded_item_count = 1
        parent.finished_at = utc_now()
        db.get(JobItemRecord, item_id).status = "succeeded"
        db.get(JobAttemptRecord, attempt_id).status = "succeeded"
        link = JobAssetRecord(
            workspace_id=WORKSPACE_ID,
            job_id=parent_id,
            job_item_id=item_id,
            asset_id=output_id,
            direction="output",
            role="generated_image",
            ordinal=0,
            is_deliverable=True,
            validation={"white_product_lineage": binding},
        )
        db.add(link)
        db.flush()
        link_id = link.id
        db.commit()
    white = SourceImage(
        output_id,
        "primary",
        output.filename,
        output.content_type,
        output.sha256,
        output.payload,
        source_role="white_background_product",
    )
    child = replace(call, job_id="job_new_amazon", sources=(white,))
    return SimpleNamespace(
        sessions=sessions,
        storage=storage,
        call=child,
        layer=layer,
        source=source,
        parent_id=parent_id,
        item_id=item_id,
        link_id=link_id,
        layer_id=layer_id,
        output_id=output_id,
        original_id=original_id,
    )


def load(fixture):
    return asyncio.run(
        white_product_lineage.load(fixture.sessions, fixture.storage, fixture.call)
    )


def rewrite_recipe(fixture, change):
    from app.models import JobItemRecord

    with fixture.sessions() as db:
        item = db.get(JobItemRecord, fixture.item_id)
        config = deepcopy(item.configuration)
        link = db.get(JobAssetRecord, fixture.link_id)
        validation = deepcopy(link.validation)
        binding = validation["white_product_lineage"]
        receipt = config["_image_fidelity_frozen"][binding["receipt_key"]]
        change(db, receipt["pixel_recipe"])
        binding["recipe_sha256"] = canonical_hash(receipt["pixel_recipe"])
        receipt["receipt_sha256"] = canonical_hash(
            {k: v for k, v in receipt.items() if k != "receipt_sha256"}
        )
        item.configuration, link.validation = config, validation
        db.commit()


def test_original_already_attached_as_detail_keeps_role_and_inherits_alpha(ancestry):
    inherited = load(ancestry)
    detail = replace(ancestry.source, source_role="product_detail")
    sources = ancestry.call.sources + (detail,)
    assert inherited.augment_sources(sources) == sources
    assert inherited.augment_sources(ancestry.call.sources)[-1] == inherited.sources[0]
    changed = replace(detail, payload=detail.payload + b"changed")
    with pytest.raises(ValueError, match="differs from white product lineage"):
        inherited.augment_sources(ancestry.call.sources + (changed,))


def test_white_output_replays_exact_original_alpha_and_owned_source(ancestry):
    inherited = load(ancestry)
    assert inherited is not None
    assert inherited.layers[0].artifact.payload == ancestry.layer.payload
    assert inherited.sources[0].payload == ancestry.source.payload
    assert inherited.sources[0].source_role == "source_pixel_parent_reference"
    assert (
        inherited.selected_layers(
            {
                "selected_regions": [
                    {
                        "asset_id": ancestry.output_id,
                        "box": [0, 0, 1, 1],
                    }
                ]
            }
        )
        == inherited.layers
    )
    assert inherited.selected_layers({"selected_regions": []}) is None
    assert (
        inherited.selected_layers(
            {
                "selected_regions": [
                    {
                        "asset_id": ancestry.output_id,
                        "box": [0, 0, 0.1, 0.1],
                    }
                ]
            }
        )
        is None
    )


@pytest.mark.parametrize("fault", ["owner", "not_completed", "receipt", "missing_link"])
def test_lineage_never_trusts_foreign_or_unaccepted_receipts(ancestry, fault):
    from app.models import JobItemRecord, JobRecord

    with ancestry.sessions() as db:
        link = db.get(JobAssetRecord, ancestry.link_id)
        if fault == "owner":
            value = deepcopy(link.validation)
            value["white_product_lineage"]["workspace_id"] = "foreign"
            link.validation = value
        elif fault == "not_completed":
            parent = db.get(JobRecord, ancestry.parent_id)
            parent.status = "failed"
            parent.succeeded_item_count = 0
            parent.failed_item_count = 1
        elif fault == "receipt":
            db.get(JobItemRecord, ancestry.item_id).configuration = {}
        else:
            row = db.scalar(
                select(JobAssetRecord).where(
                    JobAssetRecord.asset_id == ancestry.layer_id
                )
            )
            db.delete(row)
        db.commit()
    with pytest.raises(ValueError):
        load(ancestry)


@pytest.mark.parametrize("which", ["source", "cutout"])
def test_original_files_must_remain_byte_identical(ancestry, which):
    asset_id = ancestry.original_id if which == "source" else ancestry.layer_id
    with ancestry.sessions() as db:
        row = db.get(AssetRecord, asset_id)
        path = ancestry.storage.root / row.object_key
    path.write_bytes(b"changed original evidence")
    with pytest.raises(ValueError, match="bytes changed"):
        load(ancestry)


def test_recomputed_checksums_cannot_relabel_changed_rgb_as_original(ancestry):
    from io import BytesIO

    with Image.open(BytesIO(ancestry.layer.payload)) as image:
        image.putpixel((50, 45), (0, 0, 0, 255))
        altered = source_pixels.artifact(image, "changed.png")
    with ancestry.sessions() as db:
        asset = db.get(AssetRecord, ancestry.layer_id)
        (ancestry.storage.root / asset.object_key).write_bytes(altered.payload)
        asset.sha256, asset.byte_size = altered.sha256, len(altered.payload)
        db.commit()
    rewrite_recipe(
        ancestry, lambda _, recipe: recipe["layers"][0].update(sha256=altered.sha256)
    )
    with pytest.raises(ValueError, match="RGB differs"):
        load(ancestry)


def test_owned_output_hash_is_not_a_substitute_for_exact_recomposition(ancestry):
    changed = source_pixels.artifact(
        Image.new("RGB", (300, 300), "pink"), "unrelated.png"
    )
    with ancestry.sessions() as db:
        asset = db.get(AssetRecord, ancestry.output_id)
        asset.sha256, asset.byte_size = changed.sha256, len(changed.payload)
        link = db.get(JobAssetRecord, ancestry.link_id)
        value = deepcopy(link.validation)
        value["white_product_lineage"]["output_sha256"] = changed.sha256
        link.validation = value
        db.commit()
    ancestry.call = replace(
        ancestry.call,
        sources=(
            replace(
                ancestry.call.sources[0], payload=changed.payload, sha256=changed.sha256
            ),
        ),
    )
    with pytest.raises(ValueError, match="does not replay"):
        load(ancestry)


def test_another_workspace_and_unlinked_uploads_never_gain_private_sources(ancestry):
    ancestry.call = replace(ancestry.call, workspace_id="foreign")
    assert load(ancestry) is None
    ancestry.call = replace(
        ancestry.call,
        workspace_id=WORKSPACE_ID,
        sources=(
            replace(
                ancestry.call.sources[0], asset_id="uploaded_without_generated_lineage"
            ),
        ),
    )
    assert load(ancestry) is None


@pytest.mark.parametrize("accepted", [True, False])
def test_inherited_alpha_still_needs_a_fresh_complete_matte_audit(
    ancestry, monkeypatch, accepted
):
    from app.image_fidelity import MATTE
    from tests.test_image_fidelity import PixelGuard

    inherited = load(ancestry)

    def no_segmentation(*args):
        raise AssertionError("Verified alpha must not be extracted from white again")

    monkeypatch.setattr(source_pixels, "extract_layers", no_segmentation)

    class Auditor(PixelGateway):
        async def plan(self, call):
            result = await super().plan(call)
            assert call.planning_profile == MATTE
            if not accepted:
                result.payload.update(
                    complete_objects=False,
                    unresolved=["Missing photographed clasp"],
                    repairs=[],
                    edge_feather_px=0,
                )
            return result

    gateway = Auditor()
    guard = PixelGuard(gateway, None, None)
    preparation = {
        "selected_regions": [
            {
                "asset_id": ancestry.output_id,
                "box": [0, 0, 1, 1],
            }
        ]
    }
    args = (
        ancestry.call,
        ancestry.call.sources + inherited.sources,
        ancestry.call.sources,
        preparation,
        [],
        inherited.layers,
    )
    if accepted:
        layers, audits = asyncio.run(guard._approved_source_layers(*args))
        assert layers == inherited.layers and len(audits) == 1
    else:
        for _ in range(2):
            with pytest.raises(ValueError):
                asyncio.run(guard._approved_source_layers(*args))
    assert len(gateway.plans) == 1 and gateway.edits == []
