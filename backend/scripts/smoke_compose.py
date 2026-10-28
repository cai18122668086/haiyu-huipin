from __future__ import annotations

import argparse
import time
from io import BytesIO
from typing import Any
from uuid import uuid4

import httpx2
from PIL import Image


def _image_payload() -> bytes:
    output = BytesIO()
    Image.new("RGB", (96, 96), (42, 118, 246)).save(output, format="PNG")
    return output.getvalue()


def run(base_url: str) -> dict[str, Any]:
    workspace_id = "ws_development"
    with httpx2.Client(
        base_url=base_url,
        timeout=10,
        trust_env=False,
    ) as client:
        health = client.get("/health/ready")
        health.raise_for_status()

        upload = client.post(
            f"/api/v1/workspaces/{workspace_id}/assets",
            headers={"Idempotency-Key": str(uuid4())},
            data={"origin_kind": "upload", "media_kind": "image"},
            files={"file": ("smoke.png", _image_payload(), "image/png")},
        )
        upload.raise_for_status()
        source = upload.json()
        mask_upload = client.post(
            f"/api/v1/workspaces/{workspace_id}/assets",
            headers={"Idempotency-Key": str(uuid4())},
            data={"origin_kind": "upload", "media_kind": "mask"},
            files={"file": ("smoke-mask.png", _image_payload(), "image/png")},
        )
        mask_upload.raise_for_status()
        mask = mask_upload.json()

        created = client.post(
            f"/api/v1/workspaces/{workspace_id}/jobs",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "type": "local_edit",
                "intent": "submit",
                "input": {
                    "source_asset_id": source["id"],
                    "selection_mask_asset_id": mask["id"],
                },
                "configuration": {
                    "instruction": "smoke test",
                    "quality": "balanced",
                    "output_format": "png",
                },
            },
        )
        created.raise_for_status()
        job = created.json()

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = client.get(f"/api/v1/workspaces/{workspace_id}/jobs/{job['id']}")
            response.raise_for_status()
            job = response.json()
            if job["status"] in {"completed", "partial", "failed", "cancelled"}:
                break
            time.sleep(0.25)

    if job["status"] != "completed":
        raise RuntimeError(f"job did not complete successfully: {job}")
    if job["api_request_count"] != 0:
        raise RuntimeError("mock runner unexpectedly reported paid API usage")
    if job["cost"]["actual"]["micros"] != "0":
        raise RuntimeError("mock runner cost must be exactly zero")
    return {
        "health": health.json(),
        "asset_id": source["id"],
        "job_id": job["id"],
        "status": job["status"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()
    print(run(arguments.base_url))
