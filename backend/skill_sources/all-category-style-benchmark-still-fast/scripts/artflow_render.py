#!/usr/bin/env python3
"""Gate-checked artflow render bridge.

This is the replacement for handing the imagegen handoff to a host built-in
image tool. It performs *exactly* the same gate validation as
``imagegen_adapter.py`` - a persisted run, a passing ``audit-plan`` and the
active target request are still mandatory - then actually renders the image
through the artflow provider (``scripts/artflow_generate.py``).

The gate, the one-shot token, the bounded ``imagegen-prompt-<key>.txt`` prompt
and the recovery manifest are all unchanged. Only the pixel source is swapped.

Usage
-----
    python scripts/artflow_render.py \\
        --workspace <run> \\
        --request <run>/artifacts/generation-request-G01.json \\
        --output <run>/actual-pair-G01.png

Prints one JSON object (stdout) describing the handoff and the native rendered
file. Any failure prints to stderr and exits non-zero, so it fails closed just
like the adapter it replaces.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from style_harness import validate_generation_request      # noqa: E402
from generation_handoff import write_handoff               # noqa: E402
from workflow import validate_generation_gate              # noqa: E402

PROVIDER = HERE / "artflow_generate.py"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Render a gated artflow generation for this skill")
    root.add_argument("--workspace", required=True, help="Persisted run workspace")
    root.add_argument("--request", required=True, help="artifacts/generation-request-<key>.json")
    root.add_argument("--output", required=True, help="Where the native rendered file is written")
    root.add_argument("--api-key", help="artflow API key (defaults to the artflow config file)")
    root.add_argument("--model", help="Model id override")
    root.add_argument("--quality", help="low | medium | high override")
    root.add_argument("--size", help="Canvas override as WxH")
    root.add_argument("--timeout", type=int, help="Per-request timeout in seconds")
    root.add_argument("--dry-run", action="store_true", help="Validate the gate and resolve the plan only")
    return root


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = parser().parse_args()
    try:
        # ---- identical gate to imagegen_adapter.py -----------------------
        validate_generation_request(args.request)
        _, request = validate_generation_gate(args.workspace, args.request)

        prompt_path = Path(args.request).with_suffix(".prompt.txt")
        write_handoff(request, prompt_path)

        command = [
            sys.executable, str(PROVIDER),
            "--request", str(Path(args.request).resolve()),
            "--prompt-file", str(prompt_path.resolve()),
            "--out", str(Path(args.output).resolve()),
        ]
        for flag, value in (("--api-key", args.api_key), ("--model", args.model),
                            ("--quality", args.quality), ("--size", args.size)):
            if value:
                command += [flag, str(value)]
        if args.timeout:
            command += ["--timeout", str(args.timeout)]
        if args.dry_run:
            command.append("--dry-run")

        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise SystemExit("ARTFLOW_RENDER_BLOCKED: " + detail[:2000])

        rendered = json.loads(completed.stdout)
        native = rendered.get("native_output", {})

        handoff = {
            "status": "IMAGEGEN_HANDOFF_READY",
            "tool": "artflow",
            "provider_script": str(PROVIDER),
            "provider_command": " ".join(command),
            "run_id": request.get("token") and None,  # kept for shape stability
            "request": str(Path(args.request).resolve()),
            "output": str(Path(args.output).resolve()),
            "token": request.get("token"),
            "quality": request.get("render_contract", {}).get("quality"),
            "text_and_image_single_pass": True,
            "post_text_overlay": False,
            "invocation_count": 1,
            "generation_scope": request.get("generation_scope"),
            "prompt_path": str(prompt_path.resolve()),
            "referenced_image_paths": rendered.get("references", []),
            "native_output_path": native.get("image"),
            "native_width": native.get("width"),
            "native_height": native.get("height"),
            "native_sha256": native.get("sha256"),
            "requested_size": rendered.get("requested_size"),
            "canvas_match": (rendered.get("canvas") or {}).get("canvas_match"),
            "canvas_actual": (rendered.get("canvas") or {}).get("actual"),
            "receipt_path": rendered.get("receipt_path"),
            "request_sha256": rendered.get("prompt_sha256"),
        }
        if request.get("route") == "DETAIL_PAGE":
            handoff["requested_aspect_ratio"] = request.get("render_contract", {}).get("aspect_ratio")
            handoff["clarity_guidance"] = request.get("render_contract", {}).get("clarity_guidance")

        handoff["next_step"] = (
            f"python scripts/render_provenance.py --request {handoff['request']} "
            f"--native-output {handoff['native_output_path']} --artwork <unchanged copy> --output <receipt.json>"
        )
        print(json.dumps(handoff, ensure_ascii=False, indent=2))
        return 0
    except SystemExit:
        raise
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print("ARTFLOW_RENDER_BLOCKED: " + str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - fail closed
        print(f"ARTFLOW_RENDER_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
