"""Two real independent observations of frozen references; no image generation."""

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.replica_analysis_recovery import document_file
from app.replica_column_evidence import add_evidence, visual_path
from app.replica_exact_compact import CompactExactReplicaRun, protocol_binding
from app.replica_screen_boundaries import disagreements
from app.xinghe_agent import build_tengli_gateway
from PIL import Image


async def main():
    audit = Path(__file__).resolve().parent
    run_name = os.environ.get("HUIPIN_SEGMENTATION_DIAGNOSTIC_RUN", "live-v16")
    target = audit / run_name
    target.mkdir(exist_ok=False)
    source = audit / "evidence"
    snapshot = json.loads((source / "checkpoint.json").read_text(encoding="utf-8"))
    original = document_file(snapshot, "context.json")
    settings = Settings(
        _env_file=audit.parents[2] / "polaris-commerce-next/backend/.env",
        aurora_work_root=target / "transport",
        image_capacity_redis_url="",
    )
    gateway = build_tengli_gateway(settings)
    run = CompactExactReplicaRun(
        "exact",
        "rep_" + uuid4().hex,
        "segmentation-diagnostic",
        gateway,
        None,
        root=target,
    )
    references = []
    dimensions = []
    for i, _ in enumerate(original["reference_paths"]):
        relative = f"inputs/reference-{i:02}.img"
        destination = target / relative
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source / relative, destination)
        references.append(str(destination))
        with Image.open(destination) as image:
            dimensions.append(
                {"index": i, "width": image.width, "height": image.height}
            )
    columns = []
    for i, old in enumerate(original["reference_columns"]):
        destination = target / f"crops/reference-column-{i:03}.png"
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source / f"crops/reference-column-{i:03}.png", destination)
        column = {k: v for k, v in old.items() if not k.startswith("coordinate_")}
        column["crop_path"] = str(destination)
        columns.append(add_evidence(column, target / "crops"))
    run.context = {
        "reference_paths": references,
        "product_paths": [],
        "reference_columns": columns,
    }
    inputs = {"columns": columns, "reference_dimensions": dimensions}
    started = time.monotonic()
    split = await run._visual_stage(
        "identity-screen-split-0", inputs, paths=[visual_path(c) for c in columns]
    )
    inventory = await run._visual_stage(
        "identity-screen-inventory-0",
        inputs,
        paths=[*map(Path, references), *[visual_path(c) for c in columns]],
    )
    report = {
        "protocol": protocol_binding(),
        "duration_seconds": round(time.monotonic() - started, 2),
        "columns": [
            {
                "id": c["column_id"],
                "split_count": len(a["screens"]),
                "inventory_count": len(b["screens"]),
            }
            for c, a, b in zip(
                columns, split["columns"], inventory["columns"], strict=True
            )
        ],
        "disagreements": disagreements(columns, split, inventory),
        "split": split,
        "inventory": inventory,
        "usage": run.usage,
        "image_generation_calls": 0,
    }
    (audit / f"{run_name}-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in {"split", "inventory", "usage"}
            },
            ensure_ascii=False,
        )
    )


asyncio.run(main())
