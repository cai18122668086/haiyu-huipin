"""Reconcile the published inspiration gallery with the approved static source map.

This operator script is development-only by default. It uploads missing canonical
files through the normal asset API, reuses an existing asset with the same
SHA-256, and replaces each published case's complete gallery relation in one
PATCH. The role map is explicit: user_upload is product/source input,
reference is visual strategy input, and result is display-only. It never turns a
result into a reference and intentionally leaves ``生成AI模特`` empty because
that case has no verified historical asset pair.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_WORKSPACE_ID = "ws_development"
DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_USER_ID = "usr_development"

# Imported lazily in main after the CLI has established the repository root.


def _decode_json(request: Request) -> dict:
    with urlopen(request, timeout=60) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise RuntimeError("API returned a non-object JSON response")
    return value


def _request_json(
    api: str,
    path: str,
    *,
    user_id: str,
    method: str = "GET",
    body: dict | None = None,
) -> dict:
    headers = {"Accept": "application/json", "X-User-Id": user_id}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        return _decode_json(
            Request(f"{api}{path}", data=data, headers=headers, method=method)
        )
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {path} failed with HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {api}: {exc.reason}") from exc


def _multipart_upload(
    api: str, workspace_id: str, user_id: str, file_path: Path
) -> dict:
    content = file_path.read_bytes()
    boundary = f"----polaris-{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts = [
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{file_path.name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode(),
        content,
        (
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="origin_kind"'
            f"\r\n\r\nimported\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="media_kind"\r\n\r\nimage\r\n'
            f"--{boundary}--\r\n"
        ).encode(),
    ]
    digest = hashlib.sha256(content).hexdigest()
    request = Request(
        f"{api}/api/v1/workspaces/{workspace_id}/assets",
        data=b"".join(parts),
        headers={
            "Accept": "application/json",
            "X-User-Id": user_id,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Idempotency-Key": f"inspiration-static-{digest}",
        },
        method="POST",
    )
    try:
        return _decode_json(request)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"upload {file_path.name} failed with HTTP {exc.code}: {detail[:500]}"
        ) from exc


def _asset_input(asset_id: str, role: str, ordinal: int) -> dict:
    return {
        "asset_id": asset_id,
        "role": role,
        "ordinal": ordinal,
        "label": "管理员参考图" if role == "reference" else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).parents[2] / "public" / "images" / "inspiration",
    )
    parser.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parents[2] / "backend"))
    from app.inspiration_workflows import (  # pylint: disable=import-outside-toplevel
        CANONICAL_ASSETS_BY_TITLE,
        CANONICAL_METADATA_BY_TITLE,
        CANONICAL_PROMPT_BY_TITLE,
        CANONICAL_SHA_BY_FILENAME,
        FINAL_INSPIRATION_WORKFLOW_BY_TITLE,
    )

    cases = _request_json(
        args.api,
        "/api/v1/admin/inspiration/cases?status=published",
        user_id=args.user_id,
    )["items"]
    by_title = {case["title"]: case for case in cases}
    expected_titles = set(FINAL_INSPIRATION_WORKFLOW_BY_TITLE)
    if set(by_title) != expected_titles:
        missing = sorted(expected_titles - set(by_title))
        unexpected = sorted(set(by_title) - expected_titles)
        raise RuntimeError(
            f"published case set mismatch: missing={missing}, unexpected={unexpected}"
        )

    assets = _request_json(
        args.api,
        f"/api/v1/workspaces/{args.workspace_id}/assets?limit=100",
        user_id=args.user_id,
    )["items"]
    by_sha = {asset["sha256"]: asset for asset in assets}

    asset_ids_by_filename: dict[str, str] = {}
    for title_assets in CANONICAL_ASSETS_BY_TITLE.values():
        for filename, _role, _ordinal in title_assets:
            if filename in asset_ids_by_filename:
                continue
            file_path = args.source / filename
            if not file_path.is_file():
                raise RuntimeError(f"canonical file not found: {file_path}")
            content = file_path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            expected_digest = CANONICAL_SHA_BY_FILENAME[filename]
            if digest != expected_digest:
                raise RuntimeError(
                    f"SHA-256 mismatch for {filename}: expected {expected_digest}, "
                    f"got {digest}"
                )
            asset = by_sha.get(digest)
            if asset is None:
                asset = _multipart_upload(
                    args.api, args.workspace_id, args.user_id, file_path
                )
                by_sha[digest] = asset
                print(f"uploaded {filename} -> {asset['id']}")
            else:
                print(f"reused {filename} -> {asset['id']}")
            asset_ids_by_filename[filename] = asset["id"]

    for title, case in by_title.items():
        relations = [
            _asset_input(asset_ids_by_filename[filename], role, ordinal)
            for filename, role, ordinal in CANONICAL_ASSETS_BY_TITLE.get(title, ())
        ]
        metadata = CANONICAL_METADATA_BY_TITLE.get(title)
        payload = {
            key: (
                metadata[key] if metadata is not None and key in metadata else case[key]
            )
            for key in (
                "slug",
                "title",
                "description",
                "role",
                "type",
                "category",
                "platform",
                "ratio",
                "intent",
                "button_label",
                "sort_order",
                "is_pinned",
            )
        }
        payload.update(
            {
                "prompt": CANONICAL_PROMPT_BY_TITLE[title],
                "workflow_type": FINAL_INSPIRATION_WORKFLOW_BY_TITLE[title],
                "reference_source": (
                    "admin_reference"
                    if FINAL_INSPIRATION_WORKFLOW_BY_TITLE[title] == "reference_remix"
                    else "none"
                ),
                "assets": relations,
            }
        )
        updated = _request_json(
            args.api,
            f"/api/v1/admin/inspiration/cases/{case['id']}",
            method="PATCH",
            user_id=args.user_id,
            body=payload,
        )
        print(
            f"reconciled {title}: {len(relations)} asset(s), "
            f"status={updated['configuration_status']}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
