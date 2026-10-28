"""Fail when any delivered pixel outside the exact selection differs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from strict_composite import changed_outside_count, load_selection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    args = parser.parse_args()

    source = Image.open(args.source).convert("RGBA")
    result = Image.open(args.result).convert("RGBA")
    if result.size != source.size:
        print(
            json.dumps(
                {
                    "valid": False,
                    "reason": "size_mismatch",
                    "source_size": list(source.size),
                    "result_size": list(result.size),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)

    selection = load_selection(args.selection, source.size)
    changed, bbox = changed_outside_count(source, result, selection)
    report = {
        "valid": changed == 0,
        "outside_changed_pixels": changed,
        "outside_change_bbox": list(bbox) if bbox else None,
        "size": list(source.size),
    }
    print(json.dumps(report, ensure_ascii=False))
    if changed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
