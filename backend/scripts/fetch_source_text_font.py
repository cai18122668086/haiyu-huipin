"""Download the pinned OFL font at image build, never at request time."""

import hashlib
from urllib.request import urlopen

from app.source_typography import FONT_PATH, FONT_SHA256, FONT_URL

with urlopen(FONT_URL, timeout=120) as response:
    data = response.read(20 * 1024 * 1024 + 1)
if len(data) > 20 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != FONT_SHA256:
    raise RuntimeError("Public font differs from the reviewed source")
FONT_PATH.parent.mkdir(parents=True, exist_ok=True)
FONT_PATH.write_bytes(data)
