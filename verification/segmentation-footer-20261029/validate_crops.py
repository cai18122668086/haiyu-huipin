"""Continue the same diagnostic through existing reconciliation and crop guards."""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.replica_analysis_recovery import document_file
from app.replica_exact_compact import CompactExactReplicaRun
from app.replica_screen_boundaries import segment_references
from app.xinghe_agent import build_tengli_gateway
from PIL import Image


async def main():
    audit = Path(__file__).resolve().parent
    run_name = os.environ.get("HUIPIN_SEGMENTATION_DIAGNOSTIC_RUN", "live-v16")
    target = audit / run_name
    snapshot = json.loads(
        (audit / "evidence/checkpoint.json").read_text(encoding="utf-8")
    )
    original = document_file(snapshot, "context.json")
    intake = next(
        document_file(snapshot, n)
        for n in snapshot["files"]
        if n.startswith("contracts/raw-identity-segmentation-")
    )
    gateway = build_tengli_gateway(
        Settings(
            _env_file=audit.parents[2] / "polaris-commerce-next/backend/.env",
            aurora_work_root=target / "transport",
            image_capacity_redis_url="",
        )
    )
    run = CompactExactReplicaRun(
        "exact",
        "rep_" + uuid4().hex,
        "segmentation-diagnostic",
        gateway,
        None,
        root=target,
    )
    run.context = json.loads((target / "context.json").read_text(encoding="utf-8"))
    run.checkpoints = json.loads(
        (target / "compact-checkpoints.json").read_text(encoding="utf-8")
    )
    dimensions = []
    for i, p in enumerate(run.context["reference_paths"]):
        with Image.open(p) as image:
            dimensions.append(
                {"index": i, "width": image.width, "height": image.height}
            )
    visual = run._visual_stage

    async def stage(name, context, **kwargs):
        if name == "frozen-intake":
            return intake
        return await visual(name, context, **kwargs)

    run._visual_stage = stage
    started = time.monotonic()
    units = await segment_references(
        run,
        "frozen-intake",
        {
            "product_dimensions": original["product_dimensions"],
            "reference_dimensions": dimensions,
        },
    )
    result = {
        "duration_seconds": round(time.monotonic() - started, 2),
        "unit_count": len(units["units"]),
        "diagnostics": run.context["segmentation_diagnostics"],
        "frozen_intake_reused": True,
        "new_analysis_calls": len(run.usage),
        "image_generation_calls": 0,
    }
    (audit / f"{run_name}-crop-validation-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


asyncio.run(main())
