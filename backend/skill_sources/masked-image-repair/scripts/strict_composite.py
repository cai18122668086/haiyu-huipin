"""Composite an edit with an exact, inward-feathered authority mask."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter


def load_selection(path: Path, size: tuple[int, int]) -> Image.Image:
    image = Image.open(path).convert("RGB")
    if image.size != size:
        image = image.resize(size, Image.Resampling.NEAREST)
    red, green, blue = image.split()
    brightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return brightest.point(lambda value: 255 if value >= 32 else 0, mode="L")


def changed_outside_count(
    source: Image.Image,
    result: Image.Image,
    selection: Image.Image,
) -> tuple[int, tuple[int, int, int, int] | None]:
    difference = ImageChops.difference(source.convert("RGBA"), result.convert("RGBA"))
    red, green, blue, alpha = difference.split()
    any_channel = ImageChops.lighter(
        ImageChops.lighter(ImageChops.lighter(red, green), blue),
        alpha,
    )
    binary = any_channel.point(lambda value: 255 if value else 0, mode="L")
    outside = ImageChops.multiply(binary, ImageChops.invert(selection))
    return sum(outside.histogram()[1:]), outside.getbbox()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--feather", type=float, default=0.0)
    args = parser.parse_args()

    source = Image.open(args.source).convert("RGBA")
    generated = Image.open(args.generated).convert("RGBA")
    if generated.size != source.size:
        generated = generated.resize(source.size, Image.Resampling.LANCZOS)
    selection = load_selection(args.selection, source.size)

    authority = selection
    if args.feather > 0:
        blurred = selection.filter(ImageFilter.GaussianBlur(min(args.feather, 64)))
        authority = ImageChops.multiply(blurred, selection)

    visible = Image.alpha_composite(source, generated)
    result = Image.composite(visible, source, authority)
    # Apply the exact binary authority after all antialiasing and feathering.
    result = Image.composite(result, source, selection)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.save(args.out, format="PNG")

    changed, bbox = changed_outside_count(source, result, selection)
    report = {
        "output": str(args.out.resolve()),
        "size": list(result.size),
        "outside_changed_pixels": changed,
        "outside_change_bbox": list(bbox) if bbox else None,
    }
    print(json.dumps(report, ensure_ascii=False))
    if changed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
