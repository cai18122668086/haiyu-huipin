from __future__ import annotations

from pathlib import Path
from PIL import Image

from .state import HarnessError


def assemble_vertical(paths: list[str], output: str, profile: dict) -> dict:
    if not paths:
        raise HarnessError("No module images to assemble")
    images = [Image.open(path).convert("RGB") for path in paths]
    try:
        width = profile["canvas_width_px"]
        height = profile["module_height_px"]
        mismatched = [
            (paths[i], image.size)
            for i, image in enumerate(images)
            if image.size != (width, height)
        ]
        if mismatched:
            raise HarnessError(
                f"Module dimensions do not match approved profile {width}x{height}: {mismatched}"
            )
        total_height = sum(image.height for image in images)
        canvas = Image.new("RGB", (width, total_height), "white")
        y = 0
        boundaries = []
        for path, image in zip(paths, images):
            canvas.paste(image, (0, y))
            boundaries.append({"path": str(Path(path).resolve()), "top": y, "bottom": y + image.height})
            y += image.height
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target, format="PNG", optimize=True)

        slice_height = int(profile.get("slice_height_px") or total_height)
        slice_dir = target.with_name(target.stem + "-slices")
        slice_dir.mkdir(parents=True, exist_ok=True)
        slices = []
        for number, top in enumerate(range(0, total_height, slice_height), start=1):
            bottom = min(top + slice_height, total_height)
            slice_path = slice_dir / f"{number:02d}.png"
            canvas.crop((0, top, width, bottom)).save(slice_path, format="PNG", optimize=True)
            slices.append({"path": str(slice_path.resolve()), "top": top, "bottom": bottom})
        return {
            "long_page": str(target.resolve()),
            "width_px": width,
            "height_px": total_height,
            "boundaries": boundaries,
            "slices": slices,
        }
    finally:
        for image in images:
            image.close()


