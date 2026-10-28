"""Verify that complete rendered content fits an authority selection.

Optionally derive an effective selection under an explicitly enabled,
safe-region-constrained autonomous expansion policy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter


def binary_from_brightest(
    path: Path,
    size: tuple[int, int],
    threshold: int,
) -> Image.Image:
    image = Image.open(path)
    if image.size != size:
        raise ValueError(
            f"size mismatch for {path}: expected {size}, got {image.size}"
        )
    if image.mode == "L":
        channel = image
    else:
        red, green, blue = image.convert("RGB").split()
        channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return channel.point(
        lambda value: 255 if value >= threshold else 0,
        mode="L",
    )


def binary_content_alpha(
    path: Path,
    size: tuple[int, int],
    threshold: int,
) -> Image.Image:
    image = Image.open(path)
    if image.size != size:
        raise ValueError(
            f"size mismatch for {path}: expected {size}, got {image.size}"
        )
    if image.mode == "L":
        alpha = image
    elif "A" in image.getbands():
        alpha = image.getchannel("A")
    else:
        red, green, blue = image.convert("RGB").split()
        alpha = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return alpha.point(
        lambda value: 255 if value > threshold else 0,
        mode="L",
    )


def pixel_count(mask: Image.Image) -> int:
    return sum(mask.histogram()[1:])


def mask_or(first: Image.Image, second: Image.Image) -> Image.Image:
    return ImageChops.lighter(first, second)


def max_expansion_distance(
    selection: Image.Image,
    added: Image.Image,
    limit: int = 256,
) -> int | None:
    if added.getbbox() is None:
        return 0
    grown = selection
    for distance in range(1, limit + 1):
        grown = grown.filter(ImageFilter.MaxFilter(3))
        remaining = ImageChops.multiply(added, ImageChops.invert(grown))
        if remaining.getbbox() is None:
            return distance
    return None


def add_color(
    base: Image.Image,
    mask: Image.Image,
    color: tuple[int, int, int, int],
) -> Image.Image:
    solid = Image.new("RGBA", base.size, color)
    colored = Image.new("RGBA", base.size, (0, 0, 0, 0))
    colored = Image.composite(solid, colored, mask)
    return Image.alpha_composite(base, colored)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--content", type=Path, required=True)
    parser.add_argument("--safe-region", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--effective-out", type=Path)
    parser.add_argument("--overlay-out", type=Path)
    parser.add_argument("--selection-threshold", type=int, default=32)
    parser.add_argument("--alpha-threshold", type=int, default=0)
    parser.add_argument("--halo", type=int, default=0)
    parser.add_argument("--max-added-pixels", type=int)
    parser.add_argument("--allow-expand", action="store_true")
    args = parser.parse_args()

    if not 0 <= args.selection_threshold <= 255:
        raise ValueError("--selection-threshold must be in 0..255")
    if not 0 <= args.alpha_threshold <= 254:
        raise ValueError("--alpha-threshold must be in 0..254")
    if not 0 <= args.halo <= 32:
        raise ValueError("--halo must be in 0..32")
    if args.max_added_pixels is not None and args.max_added_pixels < 0:
        raise ValueError("--max-added-pixels must be nonnegative")
    if args.allow_expand and args.safe_region is None:
        raise ValueError("--safe-region is required with --allow-expand")
    if args.allow_expand and args.max_added_pixels is None:
        raise ValueError("--max-added-pixels is required with --allow-expand")

    content_image = Image.open(args.content)
    size = content_image.size
    selection = binary_from_brightest(
        args.selection,
        size,
        args.selection_threshold,
    )
    content = binary_content_alpha(
        args.content,
        size,
        args.alpha_threshold,
    )
    required = content
    if args.halo:
        required = content.filter(ImageFilter.MaxFilter(args.halo * 2 + 1))

    outside_selection = ImageChops.invert(selection)
    overflow = ImageChops.multiply(content, outside_selection)
    required_overflow = ImageChops.multiply(required, outside_selection)
    overflow_pixels = pixel_count(overflow)
    proposed_added_pixels = pixel_count(required_overflow)

    safe_region = None
    outside_safe = required_overflow
    if args.safe_region:
        safe_region = binary_from_brightest(
            args.safe_region,
            size,
            args.selection_threshold,
        )
        outside_safe = ImageChops.multiply(
            required_overflow,
            ImageChops.invert(safe_region),
        )
    outside_safe_pixels = pixel_count(outside_safe)
    within_budget = (
        args.max_added_pixels is None
        or proposed_added_pixels <= args.max_added_pixels
    )
    needs_expansion = proposed_added_pixels > 0
    can_auto_expand = (
        not needs_expansion
        or (
            safe_region is not None
            and outside_safe_pixels == 0
            and within_budget
        )
    )

    effective = mask_or(selection, required_overflow)
    effective_written = False
    if args.effective_out:
        if not needs_expansion or (args.allow_expand and can_auto_expand):
            args.effective_out.parent.mkdir(parents=True, exist_ok=True)
            effective.save(args.effective_out, format="PNG")
            effective_written = True

    if args.overlay_out:
        if args.source:
            source = Image.open(args.source).convert("RGBA")
            if source.size != size:
                raise ValueError(
                    f"size mismatch for {args.source}: "
                    f"expected {size}, got {source.size}"
                )
            overlay = source
        else:
            overlay = Image.new("RGBA", size, (32, 32, 32, 255))
        overlay = add_color(
            overlay,
            required_overflow,
            (255, 196, 0, 128),
        )
        overlay = add_color(overlay, overflow, (255, 0, 0, 192))
        if safe_region is not None:
            overlay = add_color(overlay, outside_safe, (255, 0, 255, 224))
        args.overlay_out.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(args.overlay_out, format="PNG")

    report = {
        "size": list(size),
        "valid_without_expansion": not needs_expansion,
        "overflow_pixels": overflow_pixels,
        "overflow_bbox": list(overflow.getbbox()) if overflow.getbbox() else None,
        "halo": args.halo,
        "proposed_added_pixels": proposed_added_pixels,
        "proposed_added_bbox": (
            list(required_overflow.getbbox())
            if required_overflow.getbbox()
            else None
        ),
        "outside_safe_pixels": outside_safe_pixels,
        "within_budget": within_budget,
        "can_auto_expand": can_auto_expand,
        "allow_expand": args.allow_expand,
        "effective_selection_written": effective_written,
        "maximum_expansion_distance": max_expansion_distance(
            selection,
            required_overflow,
        ),
    }
    print(json.dumps(report, ensure_ascii=False))

    accepted = not needs_expansion or (
        args.allow_expand and can_auto_expand and effective_written
    )
    if not accepted:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
