"""Check the native matte runtime with synthetic pixels, never customer data."""

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.source_pixels import MODEL_SHA256, _recover_fine_edges

parser = argparse.ArgumentParser()
parser.add_argument("--model", type=Path, default=Path("/app/models/u2net.onnx"))
args = parser.parse_args()
assert hashlib.sha256(args.model.read_bytes()).hexdigest() == MODEL_SHA256
cv2.setNumThreads(1)
network = cv2.dnn.readNetFromONNX(str(args.model))
network.setInput(np.zeros((1, 3, 320, 320), dtype=np.float32))
assert np.isfinite(network.forward()).all()

source = Image.new("RGB", (120, 120), (245, 245, 245))
draw = ImageDraw.Draw(source)
draw.rectangle((40, 40, 80, 105), fill=(20, 100, 50))
draw.rectangle((57, 20, 63, 42), fill=(190, 145, 55))
draw.rectangle((55, 15, 65, 22), fill=(190, 145, 55))
draw.rectangle((5, 5, 10, 10), fill=(190, 145, 55))
coarse = Image.new("L", source.size)
mask_draw = ImageDraw.Draw(coarse)
mask_draw.rectangle((40, 40, 80, 105), fill=255)
mask_draw.rectangle((57, 20, 63, 42), fill=255)
mask_draw.rectangle((55, 18, 65, 22), fill=255)
before = source.tobytes()
recovered = _recover_fine_edges(source, coarse, cv2, np)
assert coarse.getpixel((60, 15)) == 0
assert recovered.getpixel((60, 15)) == 255, "Fine gold fitting was not recovered"
assert recovered.getpixel((7, 7)) == 0, "Unrelated prop entered the product mask"
assert np.all(np.array(recovered)[np.array(coarse) == 255] == 255)
assert source.tobytes() == before, "Source RGB was changed"
print(
    json.dumps(
        {
            "model_sha256": MODEL_SHA256,
            "model_inference": True,
            "fine_fitting_recovered": True,
            "unrelated_prop_excluded": True,
            "source_rgb_unchanged": True,
            "image_api_calls": 0,
        }
    )
)
