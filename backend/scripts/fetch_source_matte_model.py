"""Fetch a public model at image-build time; no runtime downloads or secrets."""

import hashlib
from pathlib import Path
from urllib.request import urlopen

from app.source_pixels import MODEL_SHA256, MODEL_URL

destination = Path("/app/models/u2netp.onnx")
destination.parent.mkdir(parents=True, exist_ok=True)
with urlopen(MODEL_URL, timeout=120) as response:
    payload = response.read(10 * 1024 * 1024 + 1)
if len(payload) > 10 * 1024 * 1024:
    raise RuntimeError("Public matte model exceeds its pinned size envelope")
if hashlib.sha256(payload).hexdigest() != MODEL_SHA256:
    raise RuntimeError("Public matte model SHA256 differs from the reviewed model")
destination.write_bytes(payload)
