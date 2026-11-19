"""Reuse verified source RGB/alpha instead of extracting a flattened white image."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageOps
from sqlalchemy import select

from . import source_pixels, source_typography
from .domestic_detail.plans import canonical_hash
from .models import AssetRecord, JobAssetRecord, JobItemRecord, JobRecord
from .provider import ProviderArtifact

VERSION = "owned-white-product-lineage-v1"


def pointer(request: Any, recipe: dict, proof: dict, output_sha: str) -> dict:
    return {
        "version": VERSION,
        "workspace_id": request.workspace_id,
        "job_id": request.job_id,
        "item_id": request.job_item_id,
        "receipt_key": hashlib.sha256(request.idempotency_key.encode()).hexdigest(),
        "recipe_sha256": canonical_hash(recipe),
        "output_sha256": output_sha,
        "background_sha256": proof["background_sha256"],
    }


@dataclass(frozen=True)
class WhiteProductLineage:
    white_asset_id: str
    sources: tuple[Any, ...]
    layers: tuple[source_pixels.PixelLayer, ...]
    placements: tuple[tuple[int, ...], ...]
    size: tuple[int, int]
    binding: dict

    def augment_sources(self, sources: tuple[Any, ...]) -> tuple[Any, ...]:
        """Keep an explicitly attached original's role and exact byte identity."""
        present = {source.asset_id: source for source in sources}
        additions = []
        for master in self.sources:
            existing = present.get(master.asset_id)
            if existing is None:
                additions.append(master)
            elif existing.sha256 != master.sha256 or existing.payload != master.payload:
                raise ValueError("Attached original differs from white product lineage")
        return sources + tuple(additions)

    def selected_layers(
        self, preparation: dict
    ) -> tuple[source_pixels.PixelLayer, ...] | None:
        regions = preparation["selected_regions"]
        if len(regions) != len(self.layers):
            return None  # Subsets need the ordinary extraction/selection contract.
        selected = []
        for region in regions:
            if region["asset_id"] != self.white_asset_id:
                return None
            x0, y0, x1, y1 = region["box"]
            matches = [
                index
                for index, (left, top, right, bottom) in enumerate(self.placements)
                if x0 <= (left + right) / (2 * self.size[0]) <= x1
                and y0 <= (top + bottom) / (2 * self.size[1]) <= y1
            ]
            if len(matches) != 1 or matches[0] in selected:
                return None
            selected.append(matches[0])
        return tuple(self.layers[index] for index in selected)


async def load(sessions: Any, storage: Any, request: Any) -> WhiteProductLineage | None:
    white = [s for s in request.sources if s.source_role == "white_background_product"]
    if len(white) != 1:
        return None
    reference = white[0]
    with sessions() as db:
        links = db.scalars(
            select(JobAssetRecord).where(
                JobAssetRecord.workspace_id == request.workspace_id,
                JobAssetRecord.asset_id == reference.asset_id,
                JobAssetRecord.direction == "output",
                JobAssetRecord.is_deliverable.is_(True),
            )
        ).all()
        eligible = [row for row in links if row.validation.get("white_product_lineage")]
        if not eligible:
            return None  # Uploaded/older white images retain strict extraction.
        if len(eligible) != 1:
            raise ValueError("White product has ambiguous original lineage")
        link = eligible[0]
        binding = dict(link.validation["white_product_lineage"])
        parent = db.get(JobRecord, link.job_id)
        item = db.get(JobItemRecord, link.job_item_id)
        asset = db.get(AssetRecord, reference.asset_id)
        if (
            binding.get("version") != VERSION
            or parent is None
            or item is None
            or asset is None
            or asset.workspace_id != request.workspace_id
            or parent.workspace_id != request.workspace_id
            or item.workspace_id != request.workspace_id
            or item.job_id != parent.id
            or parent.id == request.job_id
            or parent.type != "product_image"
            or parent.configuration.get("settings", {}).get("task_type")
            != "white_background"
            or parent.status != "completed"
            or item.status != "succeeded"
            or binding.get("workspace_id") != request.workspace_id
            or binding.get("job_id") != parent.id
            or binding.get("item_id") != item.id
            or binding.get("output_sha256") != asset.sha256
            or asset.sha256 != reference.sha256
            or hashlib.sha256(reference.payload).hexdigest() != reference.sha256
            or asset.lifecycle_status != "ready"
            or asset.deleted_at is not None
        ):
            raise ValueError("White product lineage escaped its accepted owner scope")
        frozen = item.configuration.get("_image_fidelity_frozen", {}).get(
            binding.get("receipt_key")
        )
        if not isinstance(frozen, dict) or frozen.get(
            "receipt_sha256"
        ) != canonical_hash({k: v for k, v in frozen.items() if k != "receipt_sha256"}):
            raise ValueError("Original white product receipt changed")
        recipe = frozen.get("pixel_recipe")
        if (
            not isinstance(recipe, dict)
            or canonical_hash(recipe) != binding.get("recipe_sha256")
            or recipe.get("version") != source_pixels.VERSION
            or recipe.get("model_sha256") != source_pixels.MODEL_SHA256
            or not 1 <= len(recipe.get("layers", [])) <= 6
            or recipe.get("placement_indices")
            or recipe.get("source_windows")
        ):
            raise ValueError("Original white product source recipe changed")

        def owned(asset_id: str, role: str | None = None) -> AssetRecord:
            statement = (
                select(AssetRecord)
                .join(JobAssetRecord, JobAssetRecord.asset_id == AssetRecord.id)
                .where(
                    AssetRecord.id == asset_id,
                    AssetRecord.workspace_id == request.workspace_id,
                    AssetRecord.lifecycle_status == "ready",
                    AssetRecord.deleted_at.is_(None),
                    JobAssetRecord.workspace_id == request.workspace_id,
                    JobAssetRecord.job_id == parent.id,
                )
            )
            if role:
                statement = statement.where(
                    JobAssetRecord.job_item_id == item.id,
                    JobAssetRecord.role == role,
                    JobAssetRecord.is_deliverable.is_(False),
                    JobAssetRecord.validation["fidelity_receipt"].as_string()
                    == binding["receipt_key"],
                )
            row = db.scalar(statement)
            if row is None:
                raise ValueError("Original white product evidence is unavailable")
            return row

        records = [
            (
                row,
                owned(row["asset_id"], "fidelity_source_cutout"),
                owned(row["source_asset_id"]),
            )
            for row in recipe["layers"]
        ]
        backgrounds = db.scalars(
            select(AssetRecord)
            .join(JobAssetRecord, JobAssetRecord.asset_id == AssetRecord.id)
            .where(
                AssetRecord.workspace_id == request.workspace_id,
                AssetRecord.sha256 == binding["background_sha256"],
                AssetRecord.lifecycle_status == "ready",
                AssetRecord.deleted_at.is_(None),
                JobAssetRecord.workspace_id == request.workspace_id,
                JobAssetRecord.job_id == parent.id,
                JobAssetRecord.job_item_id == item.id,
                JobAssetRecord.role == "fidelity_background",
                JobAssetRecord.is_deliverable.is_(False),
                JobAssetRecord.validation["fidelity_receipt"].as_string()
                == binding["receipt_key"],
            )
        ).all()
        if len(backgrounds) != 1:
            raise ValueError("Original white background receipt is unavailable")
        background = backgrounds[0]

    async def read(row: AssetRecord) -> ProviderArtifact:
        width, height = row.width, row.height
        if width is None or height is None:
            raise ValueError("Original white product evidence dimensions are missing")
        data = await storage.read_bytes(row.object_key)
        if len(data) != row.byte_size or hashlib.sha256(data).hexdigest() != row.sha256:
            raise ValueError("Original white product evidence bytes changed")
        with Image.open(BytesIO(data)) as image:
            if image.size != (width, height):
                raise ValueError("Original white product evidence dimensions changed")
        return ProviderArtifact(
            data, row.original_filename, row.content_type, width, height, row.sha256
        )

    from .xinghe_agent import SourceImage

    masters = {}
    layers = []
    for record, cutout_row, source_row in records:
        cutout, master = await read(cutout_row), await read(source_row)
        if (
            cutout.sha256 != record["sha256"]
            or master.sha256 != record["source_sha256"]
        ):
            raise ValueError("Original white product authority changed")
        with (
            Image.open(BytesIO(cutout.payload)) as rgba,
            Image.open(BytesIO(master.payload)) as photo,
        ):
            expected = (
                ImageOps.exif_transpose(photo)
                .convert("RGB")
                .crop(tuple(record["source_box"]))
            )
            if (
                "A" not in rgba.getbands()
                or rgba.size != expected.size
                or (rgba.convert("RGB").tobytes() != expected.tobytes())
            ):
                raise ValueError(
                    "Inherited product RGB differs from its original photo"
                )
        masters[source_row.id] = SourceImage(
            source_row.id,
            request.group_key,
            master.filename,
            master.content_type,
            master.sha256,
            master.payload,
            source_role="source_pixel_parent_reference",
        )
        layers.append(
            source_pixels.PixelLayer(
                cutout, source_row.id, master.sha256, tuple(record["source_box"])
            )
        )
    replayed, proof = source_pixels.compose(
        await read(background),
        tuple(layer.artifact for layer in layers),
        recipe["cells"],
    )
    text = recipe.get("text_recipe")
    if text is not None:
        if (
            not isinstance(text, dict)
            or text.get("version") != source_typography.VERSION
            or text.get("font_sha256") != source_typography.FONT_SHA256
        ):
            raise ValueError("Original white product typography recipe changed")
        if text.get("blocks"):
            replayed, _ = source_typography.render(replayed, text)
    if replayed.sha256 != reference.sha256:
        raise ValueError(
            "White product output does not replay from its original layers"
        )
    return WhiteProductLineage(
        reference.asset_id,
        tuple(masters.values()),
        tuple(layers),
        tuple(tuple(row["box"]) for row in proof["placements"]),
        (replayed.width, replayed.height),
        binding,
    )
