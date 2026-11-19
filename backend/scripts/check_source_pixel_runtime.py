"""Check the native matte runtime with synthetic pixels, never customer data."""

import argparse
import hashlib
import json
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app import source_typography
from app.source_pixels import (
    MODEL_SHA256,
    MatteFragmentError,
    PixelLayer,
    _recover_fine_edges,
    _single_product,
    artifact,
    repair_layers,
)

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
connected = Image.new("L", (100, 100))
connected_draw = ImageDraw.Draw(connected)
connected_draw.rectangle((20, 20, 70, 80), fill=255)
connected_draw.rectangle((90, 20, 91, 21), fill=255)
isolated = _single_product(connected, cv2, np)
assert isolated.getpixel((90, 20)) == 0
assert isolated.getpixel((30, 30)) == 255
connected_draw.rectangle((80, 10, 99, 90), fill=255)
try:
    _single_product(connected, cv2, np)
except MatteFragmentError:
    pass
else:
    raise AssertionError("Large disconnected context escaped the 95% guard")
hardware = Image.new("RGBA", (80, 160), (30, 80, 120, 0))
hardware_draw = ImageDraw.Draw(hardware)
hardware_draw.rectangle((39, 10, 45, 30), fill=(190, 145, 55, 0))
hardware_alpha = Image.new("L", hardware.size)
hardware_mask = ImageDraw.Draw(hardware_alpha)
hardware_mask.rectangle((40, 10, 45, 30), fill=255)
hardware_mask.rectangle((20, 31, 65, 150), fill=255)
hardware.putalpha(hardware_alpha)
repaired = repair_layers(
    (PixelLayer(artifact(hardware, "fitting.png"), "synthetic", "0" * 64,
                (0, 0, 80, 160)),),
    [{"layer_index": 0, "action": "restore_foreground", "box": [
        51 / 112, 26 / 192, 66 / 112, 46 / 192,
    ]}],
    0,
)[0]
with Image.open(BytesIO(repaired.artifact.payload)) as recovered_hardware:
    assert recovered_hardware.getpixel((39, 20))[3] == 255
    assert recovered_hardware.getpixel((35, 10))[3] == 0
    assert (
        recovered_hardware.convert("RGB").tobytes()
        == hardware.convert("RGB").tobytes()
    )
font_path = source_typography.FONT_PATH
assert (
    hashlib.sha256(font_path.read_bytes()).hexdigest() == source_typography.FONT_SHA256
)
text_recipe = source_typography.recipe(
    ["批准标题：原样文字", "Original approved copy"],
    [
        {
            "copy_index": 0,
            "panel": 0,
            "panel_box": [0.08, 0.05, 0.92, 0.24],
            "role": "heading",
            "color": "#334155",
            "align": "center",
        },
        {
            "copy_index": 1,
            "panel": 1,
            "panel_box": [0.08, 0.08, 0.92, 0.22],
            "role": "body",
            "color": "#334155",
            "align": "left",
        },
    ],
    [[0.2, 0.2, 0.8, 0.45], [0.2, 0.7, 0.8, 0.95]],
    2,
    font_path,
    (1440, 3840),
)
texted, text_proof = source_typography.render(
    artifact(Image.new("RGB", (1440, 3840), "white"), "synthetic.png"),
    text_recipe,
    font_path,
)
assert (texted.width, texted.height) == (1440, 3840)
assert len(text_proof["blocks"]) == 2
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
