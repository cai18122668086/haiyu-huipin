"""Import the legacy 18-card inspiration gallery through the normal asset API.

Usage (with the development API running):
  python backend/scripts/import_static_inspiration.py

The legacy source files remain untouched. The script is idempotent by title and
uploads each distinct image once, then creates a published database case.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8000"
WORKSPACE = "ws_development"
USER = "usr_development"


def read_cards() -> list[dict]:
    source = (ROOT / "components" / "inspiration-gallery.tsx").read_text(
        encoding="utf-8"
    )
    expression = source[
        source.index("const cards: Card[] =")
        + len("const cards: Card[] =") : source.index(
            "];", source.index("const cards: Card[] =")
        )
        + 1
    ]
    script = f"console.log(JSON.stringify(Function('return '+{expression!r})()))"
    return json.loads(
        subprocess.check_output(
            ["node", "-e", script], cwd=ROOT, text=True, encoding="utf-8"
        )
    )


def main() -> None:
    session = requests.Session()
    session.headers.update({"X-User-Id": USER})
    existing = session.get(
        f"{API}/api/v1/admin/inspiration/cases?status=all", timeout=30
    )
    existing.raise_for_status()
    existing_titles = {item["title"] for item in existing.json().get("items", [])}
    asset_ids: dict[str, str] = {}
    imported = 0
    cards = read_cards()
    for card in cards:
        if card["title"] in existing_titles:
            continue
        image_paths = [card["image"], *card.get("gallery", [])]
        relations: list[dict] = []
        for ordinal, public_path in enumerate(dict.fromkeys(image_paths)):
            path = ROOT / "public" / public_path.lstrip("/")
            if not path.exists():
                raise FileNotFoundError(path)
            key = str(path.resolve())
            asset_id = asset_ids.get(key)
            if asset_id is None:
                with path.open("rb") as image_file:
                    response = session.post(
                        f"{API}/api/v1/workspaces/{WORKSPACE}/assets",
                        files={"file": (path.name, image_file, "image/*")},
                        data={"media_kind": "image", "origin_kind": "upload"},
                        headers={
                            "Idempotency-Key": f"inspiration-import-{uuid.uuid4().hex}"
                        },
                        timeout=60,
                    )
                response.raise_for_status()
                asset_id = response.json()["id"]
                asset_ids[key] = asset_id
            role = (
                "result"
                if ordinal == 0
                else ("user_upload" if ordinal == 1 else "reference")
            )
            relations.append(
                {
                    "asset_id": asset_id,
                    "role": role,
                    "ordinal": ordinal if role == "reference" else 0,
                }
            )
        payload = {
            "slug": card["id"],
            "title": card["title"],
            "description": card["description"],
            "role": card["role"],
            "type": card["type"],
            "category": card["category"],
            "platform": card["platform"],
            "ratio": card["ratio"],
            "intent": card["intent"],
            "prompt": card["prompt"],
            "sort_order": imported,
            "is_pinned": False,
            "assets": relations,
        }
        response = session.post(
            f"{API}/api/v1/admin/inspiration/cases", json=payload, timeout=60
        )
        response.raise_for_status()
        session.post(
            f"{API}/api/v1/admin/inspiration/cases/{response.json()['id']}/publish",
            timeout=30,
        ).raise_for_status()
        imported += 1
        print(f"imported {imported:02d}: {card['title']}")
    print(f"done: imported={imported}, skipped={len(cards) - imported}")


if __name__ == "__main__":
    main()
