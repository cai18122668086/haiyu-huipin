#!/usr/bin/env python3
"""artflow provider bridge for `generation_adapter.py run`.

`generation_adapter.py run --provider-command <cmd> --request <request.json>`
expects a local provider CLI that prints UTF-8 JSON containing an ``image``
path. This script is that CLI. It is the local replacement for the host's
built-in image generation, which has no CLI of its own.

Usage
-----
    python scripts/artflow_provider.py --request <request.json> --out-dir <dir>

Prints ``{"image": "<absolute path>", ...}``. Any failure exits non-zero with a
JSON error on stderr, so `generation_adapter.py` treats it as a provider
failure and the run fails closed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROVIDER = HERE / "artflow_generate.py"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="artflow provider for the detail-page generation adapter")
    root.add_argument("--request", required=True, help="Request emitted by `generation_adapter.py prepare`")
    root.add_argument("--out-dir", help="Directory for the rendered original (defaults next to the request)")
    root.add_argument("--out", help="Explicit output path")
    root.add_argument("--api-key", help="artflow API key")
    root.add_argument("--model", help="Model id override")
    root.add_argument("--quality", help="low | medium | high override")
    root.add_argument("--size", help="Canvas override as WxH")
    root.add_argument("--timeout", type=int, help="Per-request timeout in seconds")
    return root


def output_path(args: argparse.Namespace, request: dict) -> Path:
    if args.out:
        return Path(args.out)
    directory = Path(args.out_dir) if args.out_dir else Path(args.request).resolve().parent
    stem = request.get("module_id") or "pair"
    token = (request.get("token") or "notoken")[:8]
    return directory / f"artflow-{stem}-{token}.png"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = parser().parse_args()
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")

        destination = output_path(args, request)
        command = [sys.executable, str(PROVIDER),
                   "--request", str(Path(args.request).resolve()),
                   "--out", str(destination.resolve())]
        for flag, value in (("--api-key", args.api_key), ("--model", args.model),
                            ("--quality", args.quality), ("--size", args.size)):
            if value:
                command += [flag, str(value)]
        if args.timeout:
            command += ["--timeout", str(args.timeout)]

        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if completed.returncode:
            raise ValueError((completed.stderr or completed.stdout or "artflow provider failed").strip()[:1500])

        rendered = json.loads(completed.stdout)
        native = rendered.get("native_output", {})
        if not native.get("image"):
            raise ValueError("artflow provider returned no image path")

        print(json.dumps({
            "image": native["image"],
            "provider": "artflow",
            "model": rendered.get("model"),
            "quality": rendered.get("quality"),
            "requested_size": rendered.get("requested_size"),
            "canvas": rendered.get("canvas"),
            "width": native.get("width"),
            "height": native.get("height"),
            "sha256": native.get("sha256"),
            "receipt_path": rendered.get("receipt_path"),
            "elapsed_sec": rendered.get("elapsed_sec"),
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - fail closed
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
