"""Generate display-only WebP derivatives; never overwrite source/delivery files.

Run with the project's Pillow environment from the repository root.
"""

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
OUTPUT = PUBLIC / "images/display"


def main() -> None:
    sources = sorted((PUBLIC / "images/cases").rglob("*"))
    sources += sorted((PUBLIC / "images/homepage-features").glob("*.png"))
    sources += [PUBLIC / "brand/haiyu-logo-folded-h.png", PUBLIC / "brand/haiyu-icon-folded-h.png"]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for path in sources:
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        original = path.read_bytes()
        digest = hashlib.sha256(b"display-v1-webp82-" + original).hexdigest()[:16]
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGBA" if "A" in opened.getbands() else "RGB")
            widths = [142, 284, 426] if path.name == "haiyu-logo-folded-h.png" else [320, 640, 960]
            widths = sorted({min(width, image.width) for width in widths})
            variants = []
            for width in widths:
                height = max(1, round(image.height * width / image.width))
                name = f"{digest}-{width}.webp"
                image.resize((width, height), Image.Resampling.LANCZOS).save(OUTPUT / name, "WEBP", quality=82, method=6)
                variants.append({"src": "/images/display/" + name, "width": width, "height": height, "bytes": (OUTPUT / name).stat().st_size})
        fallback = next((v for v in variants if v["width"] >= 640), variants[-1])
        manifest["/" + path.relative_to(PUBLIC).as_posix()] = {
            "src": fallback["src"], "srcSet": ", ".join(f'{v["src"]} {v["width"]}w' for v in variants),
            "originalSha256": hashlib.sha256(original).hexdigest(), "variants": variants,
        }
    (ROOT / "lib/display-images.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"originals_preserved": len(manifest), "derivative_bytes": sum(v["bytes"] for item in manifest.values() for v in item["variants"])}, indent=2))


if __name__ == "__main__":
    main()
