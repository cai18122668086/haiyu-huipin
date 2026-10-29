#!/usr/bin/env python3
"""Artflow image provider for the Aurora / style-benchmark Skill family.

This script replaces the host's built-in image tool (``$imagegen`` / "Codex
built-in image generation") with a real HTTP call to the artflow
OpenAI-compatible gateway. It is the sanctioned generation bridge: the Skills'
generation-token gates, handoff contracts, provenance receipts and audits are
unchanged - only the pixel source is swapped.

Entry modes
-----------
request mode
    ``--request <request.json> --out <image.png>``
    ``request.json`` is whatever the Skill emitted (``generate-next``,
    ``generate-pair-request``, ``imagegen_adapter`` handoff target, or an
    ``ai_packet``). The Skill family is detected from the request shape, and the
    prompt, reference images, canvas size and quality are read out of it.

explicit mode
    ``--prompt "..." | --prompt-file <f> [--reference <p> ...] --out <image.png>``
    Used for ad-hoc calls and by the detail-page provider bridge.

Contract
--------
* Always prints exactly one JSON object on stdout on success.
* On failure prints a JSON object on stderr and exits non-zero (fail closed).
* The file written to ``--out`` is the provider's native original, byte for
  byte. It is never resized, resampled or sharpened locally.
* A sidecar receipt ``<out>.receipt.json`` records the real pixel size,
  SHA-256 and the provider metadata that the Skills' provenance checks consume.

Standard library only, so it runs on the bare Python the Skills already use.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DEFAULT_BASE_URL = "https://api.artflow.vip/v1"
DEFAULT_MODEL = "gpt-image-2.5-flare"
DEFAULT_QUALITY = "high"
DEFAULT_TIMEOUT = 600
DEFAULT_RETRIES = 2
DEFAULT_MAX_REFERENCES = 10

DEFAULT_CONFIG_PATH = Path.home() / ".workbuddy" / "artflow-config.json"


def _config_candidates() -> tuple[Path, ...]:
    """Config locations, most specific first.

    The desktop editions keep their data root in different places (WorkBuddy
    uses ~/.workbuddy, WorkBuddy AI uses ~/.workbuddy-ai, the CodeBuddy CLI
    uses ~/.codebuddy or $CODEBUDDY_CONFIG_DIR), so a Skill installed into any
    of them must find the same credentials without per-app hardcoding.
    """
    candidates: list[Path] = []
    for env_name in ("WORKBUDDY_CONFIG_DIR", "CODEBUDDY_CONFIG_DIR"):
        value = os.environ.get(env_name)
        if value and value.strip():
            candidates.append(Path(value.strip()) / "artflow-config.json")
    candidates.extend([
        DEFAULT_CONFIG_PATH,
        Path.home() / ".workbuddy-ai" / "artflow-config.json",
        Path.home() / ".codebuddy" / "artflow-config.json",
        Path.home() / ".artflow" / "config.json",
    ])
    seen, unique = set(), []
    for path in candidates:
        key = os.path.normcase(os.path.abspath(str(path)))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique)


CONFIG_CANDIDATES = _config_candidates()

# Native canvases per aspect ratio. Values are the largest canvas the gateway
# accepted in testing without local upscaling. 3:8 is the detail-page Pair
# master geometry and is verified to return an exact 1440x3840 original.
ASPECT_CANVAS = {
    "1:1": (1536, 1536),
    "4:3": (1536, 1152),
    "3:4": (1152, 1536),
    "3:2": (1536, 1024),
    "2:3": (1024, 1536),
    "16:9": (1536, 864),
    "9:16": (864, 1536),
    "2:1": (1536, 768),
    "1:2": (768, 1536),
    "3:8": (1440, 3840),
    "8:3": (3840, 1440),
    "9:21": (1080, 2520),
    "21:9": (2520, 1080),
}

REFERENCE_KEYS = (
    "referenced_image_paths",
    "imagegen_reference_paths",
    "reference_images",
    "reference_paths",
)


class Fail(Exception):
    """A fail-closed error that must stop the Skill run."""


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def load_config(explicit_path: str | None) -> dict:
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    env_path = os.environ.get("ARTFLOW_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(CONFIG_CANDIDATES)
    for path in candidates:
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    data["__source__"] = str(path)
                    return data
        except Exception:
            continue
    return {}


def resolve_settings(args: argparse.Namespace, config: dict) -> dict:
    api_key = (
        args.api_key
        or os.environ.get("ARTFLOW_API_KEY")
        or config.get("api_key")
        or ""
    ).strip()
    if not api_key:
        searched = "\n  ".join(str(p) for p in CONFIG_CANDIDATES)
        raise Fail(
            "no artflow API key found. Provide --api-key, set ARTFLOW_API_KEY, or create "
            "one of these files with {\"api_key\": \"sk-...\"}:\n  " + searched
        )
    base_url = (args.base_url or os.environ.get("ARTFLOW_BASE_URL")
                or config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": args.model or config.get("model") or DEFAULT_MODEL,
        "quality": args.quality or config.get("quality") or DEFAULT_QUALITY,
        "timeout": int(args.timeout or config.get("timeout") or DEFAULT_TIMEOUT),
        "retries": int(args.retries if args.retries is not None
                       else config.get("retries", DEFAULT_RETRIES)),
        "max_references": int(args.max_references or config.get("max_references")
                              or DEFAULT_MAX_REFERENCES),
        "config_source": config.get("__source__"),
    }


# --------------------------------------------------------------------------
# request-JSON introspection
# --------------------------------------------------------------------------


def _looks_like_image(path: str) -> bool:
    return Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def _existing(paths) -> list[str]:
    out = []
    for item in paths:
        if not item:
            continue
        text = str(item)
        if Path(text).is_file() and _looks_like_image(text):
            out.append(text)
    return out


def _dedupe(paths) -> list[str]:
    seen, out = set(), []
    for item in paths:
        key = os.path.normcase(os.path.abspath(item))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def resolve_prompt(request_path: Path, request: dict, explicit: str | None) -> tuple[str, str]:
    """Return (prompt_text, source_description)."""
    if explicit:
        return explicit, "explicit --prompt"

    for key in ("prompt", "imagegen_prompt", "compiled_prompt"):
        value = request.get(key)
        if isinstance(value, str) and value.strip():
            return value, f"request.{key}"

    # Aurora Skills emit the readable model prompt as a bounded sidecar file so
    # the machine request JSON never leaks hashes/paths into the canvas.
    stem = request_path.with_suffix("")
    candidates = [
        request_path.with_suffix(".prompt.txt"),
        stem.parent / (stem.name + ".prompt.txt"),
        stem.parent / (stem.name + ".txt"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8-sig")
            if text.strip():
                return text, f"sidecar {candidate.name}"

    # imagegen-prompt-G01.txt style companion
    for candidate in sorted(stem.parent.glob("*prompt*.txt")):
        if stem.name.split(".")[0] in candidate.name or "generation-request" in request_path.name:
            text = candidate.read_text(encoding="utf-8-sig")
            if text.strip():
                return text, f"companion {candidate.name}"

    raise Fail(
        f"no usable prompt found for {request_path.name}; "
        "expected a 'prompt' field or a '<request>.prompt.txt' sidecar"
    )


def resolve_references(request: dict) -> list[str]:
    """Collect reference images in the order the Skill declared them."""
    refs: list[str] = []

    # benchmark-still: product identity first, then sanitized style crops.
    for key in ("product_image_bindings", "sanitized_style_crop_bindings", "material_image_bindings"):
        rows = request.get(key)
        if isinstance(rows, list):
            refs.extend(row.get("path") for row in rows if isinstance(row, dict))

    if not refs:
        for key in REFERENCE_KEYS:
            value = request.get(key)
            if isinstance(value, list):
                refs.extend(value)
            elif isinstance(value, str):
                refs.append(value)

    # A blank geometry canvas is only a local aspect-ratio lock. We pass the
    # exact canvas to the provider via `size` instead, so it is not an image
    # reference and must never reach the model as content.
    canvas = request.get("format_canvas")
    if isinstance(canvas, str):
        refs = [item for item in refs if os.path.normcase(item) != os.path.normcase(canvas)]

    return _dedupe(_existing(refs))


def _aspect_from(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace(" ", "")
    if ":" in text and all(part.isdigit() for part in text.split(":")[:2]):
        w, h = (int(part) for part in text.split(":")[:2])
        if w > 0 and h > 0:
            return f"{w}:{h}"
    return None


def resolve_size(request: dict, explicit: str | None) -> tuple[str, str]:
    """Return (WxH, source_description)."""
    if explicit:
        return explicit, "explicit --size"
    if explicit == "auto":
        return "auto", "explicit --size"

    contract = request.get("render_contract")
    if isinstance(contract, dict):
        ratio = _aspect_from(contract.get("aspect_ratio"))
        native = contract.get("native_generation_size")
        # For a DETAIL_PAGE route the Pair master geometry (3:8) is the
        # authoritative canvas; the skill's own handoff publishes aspect_ratio
        # rather than a pixel size for exactly this reason. Every other route
        # declares an explicit native size.
        prefer_ratio = request.get("route") == "DETAIL_PAGE" and ratio is not None
        if prefer_ratio or not isinstance(native, (list, tuple)) or len(native) != 2:
            if ratio and ratio in ASPECT_CANVAS:
                w, h = ASPECT_CANVAS[ratio]
                return f"{w}x{h}", f"render_contract.aspect_ratio {ratio}"
        if isinstance(native, (list, tuple)) and len(native) == 2:
            return f"{int(native[0])}x{int(native[1])}", "render_contract.native_generation_size"
        if ratio:
            return "auto", f"render_contract.aspect_ratio {ratio} (unmapped)"

    surface = request.get("output_surface")
    if isinstance(surface, dict):
        width, height = surface.get("width_px"), surface.get("height_px")
        if width and height:
            return f"{int(width)}x{int(height)}", "output_surface.width_px/height_px"
        ratio = _aspect_from(surface.get("aspect_ratio"))
        if ratio:
            if ratio in ASPECT_CANVAS:
                w, h = ASPECT_CANVAS[ratio]
                floor = max(int(surface.get("minimum_width") or 0),
                            int(surface.get("minimum_height") or 0))
                if floor and min(w, h) < floor:
                    # scale the native canvas up to the declared floor
                    scale = floor / min(w, h)
                    w, h = int(round(w * scale)), int(round(h * scale))
                return f"{w}x{h}", f"output_surface.aspect_ratio {ratio}"
            return "auto", f"output_surface.aspect_ratio {ratio} (unmapped)"

    canvas = request.get("canvas")
    if isinstance(canvas, dict) and canvas.get("width") and canvas.get("height"):
        return f"{int(canvas['width'])}x{int(canvas['height'])}", "canvas.width/height"

    if request.get("packet_id") or request.get("mode") in {"AI_MASTER", "AI_EDIT", "AI_CLEANUP"}:
        return "1024x1536", "ai_packet default (no canvas declared)"

    return "auto", "provider default (no canvas declared)"


def resolve_quality(request: dict, fallback: str) -> tuple[str, str]:
    contract = request.get("render_contract")
    if isinstance(contract, dict) and contract.get("quality"):
        return str(contract["quality"]), "render_contract.quality"
    return fallback, "config default"


def detect_family(request: dict, request_path: Path) -> str:
    if request.get("product_image_bindings") is not None and request.get("route"):
        return "all-category-style-benchmark-still"
    if request.get("packet_id") or request.get("mode") in {"AI_MASTER", "AI_EDIT", "AI_CLEANUP"}:
        return "aurora-domestic-ecommerce-sku-image-batch-v2"
    if isinstance(request.get("output_surface"), dict) and request.get("imagegen_reference_paths") is not None:
        return "aurora-domestic-ecommerce-main-image"
    if isinstance(request.get("output_surface"), dict) and request.get("reference_images") is not None:
        return "aurora-domestic-ecommerce-detail-page"
    if isinstance(request.get("canvas"), dict) and request.get("sequence_index") is not None:
        return "aurora-ecommerce-reference-remix"
    if "generation-request" in request_path.name:
        return "all-category-style-benchmark-still"
    return "unknown"


def select_packet(document: dict, packet_id: str) -> dict:
    """Pull one ai_packet out of a compiled render-packets document."""
    packets = document.get("ai_packets")
    if not isinstance(packets, list):
        raise Fail("this JSON is not a render-packets document (no ai_packets array)")
    for packet in packets:
        if isinstance(packet, dict) and packet.get("packet_id") == packet_id:
            return packet
    raise Fail(f"packet_id {packet_id} not present in the render-packets document")


def build_plan(args: argparse.Namespace, settings: dict) -> dict:
    request: dict = {}
    request_path: Path | None = None

    if args.request:
        request_path = Path(args.request)
        if not request_path.is_file():
            raise Fail(f"generation request not found: {request_path}")
        request = json.loads(request_path.read_text(encoding="utf-8-sig"))
        if not isinstance(request, dict):
            raise Fail(f"generation request must be a JSON object: {request_path}")
        if args.packet_id:
            request = select_packet(request, args.packet_id)

        # Generation-token gate. Every Aurora Skill mints a single-use token (or,
        # for the SKU batch, a packet_id) before an image may be produced. The
        # provider enforces that here as well so a hand-written prompt cannot
        # silently bypass the workflow's own gate.
        if not args.allow_untokened:
            token = request.get("token") or request.get("generation_token")
            if not token and not request.get("packet_id"):
                raise Fail(
                    "request carries no generation token or packet_id; this is not a "
                    "Skill-issued generation request. Pass --allow-untokened for an "
                    "explicit ad-hoc generation."
                )

    prompt_file = args.prompt_file
    if not prompt_file and request_path and not args.prompt:
        for candidate in (request_path.with_suffix(".prompt.txt"),
                          request_path.with_name(request_path.stem + ".prompt.txt")):
            if candidate.is_file():
                prompt_file = str(candidate)
                break

    if prompt_file:
        text = Path(prompt_file).read_text(encoding="utf-8-sig")
        if not text.strip():
            raise Fail(f"prompt file is empty: {prompt_file}")
        prompt, prompt_source = text, f"--prompt-file {Path(prompt_file).name}"
    else:
        prompt, prompt_source = resolve_prompt(request_path or Path("request.json"), request, args.prompt)

    references = resolve_references(request)
    if args.reference:
        references = _dedupe(references + _existing(args.reference))
    references = _dedupe(references)

    dropped: list[str] = []
    if len(references) > settings["max_references"]:
        dropped = references[settings["max_references"]:]
        references = references[:settings["max_references"]]

    size, size_source = resolve_size(request, args.size)
    quality, quality_source = resolve_quality(request, settings["quality"])
    family = detect_family(request, request_path) if request_path else "explicit"

    endpoint = "images/edits" if references else "images/generations"
    return {
        "family": family,
        "request": str(request_path.resolve()) if request_path else None,
        "token": request.get("token") or request.get("generation_token"),
        "packet_id": request.get("packet_id"),
        "declared_output": request.get("target_output_path"),
        "prompt": prompt,
        "prompt_source": prompt_source,
        "prompt_chars": len(prompt),
        "references": references,
        "references_requested": len(references) + len(dropped),
        "references_dropped": dropped,
        "size": size,
        "size_source": size_source,
        "quality": quality,
        "quality_source": quality_source,
        "model": settings["model"],
        "endpoint": endpoint,
        "base_url": settings["base_url"],
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _http_json(url: str, headers: dict, body: bytes, method: str,
               timeout: int, retries: int) -> tuple[dict, float]:
    last: Exception | None = None
    for attempt in range(retries + 1):
        started = time.time()
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
                raw = response.read()
            return json.loads(raw.decode("utf-8")), time.time() - started
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:800]
            last = Fail(f"provider returned HTTP {exc.code}: {detail}")
            transient = exc.code in (408, 409, 425, 429, 500, 502, 503, 504)
            # A 5xx may or may not have produced a charge; retrying is the
            # operator's decision, so we only auto-retry when the gateway clearly
            # rejected the request without rendering.
            if not transient or attempt == retries:
                raise last
        except urllib.error.URLError as exc:
            last = Fail(f"provider unreachable: {exc.reason}")
            if attempt == retries:
                raise last
        except json.JSONDecodeError:
            raise Fail("provider returned a non-JSON response")
        time.sleep(2 * (attempt + 1))
    raise last or Fail("provider call failed")


def _multipart(fields: list[tuple[str, str]], files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = "----artflow" + uuid.uuid4().hex
    buf = io.BytesIO()
    for name, value in fields:
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        buf.write(str(value).encode("utf-8"))
        buf.write(b"\r\n")
    for name, path in files:
        suffix = path.suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}.get(suffix, "application/octet-stream")
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode())
        buf.write(f"Content-Type: {mime}\r\n\r\n".encode())
        buf.write(path.read_bytes())
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def call_provider(plan: dict, settings: dict) -> tuple[bytes, dict, float]:
    headers = {"Authorization": "Bearer " + settings["api_key"]}
    if plan["endpoint"] == "images/edits":
        fields = [("model", plan["model"]), ("prompt", plan["prompt"]),
                  ("n", "1"), ("size", plan["size"])]
        if plan["quality"]:
            fields.append(("quality", plan["quality"]))
        files = [("image[]", Path(item)) for item in plan["references"]]
        body, content_type = _multipart(fields, files)
        headers["Content-Type"] = content_type
    else:
        payload = {"model": plan["model"], "prompt": plan["prompt"],
                   "n": 1, "size": plan["size"], "response_format": "b64_json"}
        if plan["quality"]:
            payload["quality"] = plan["quality"]
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    url = f"{plan['base_url']}/{plan['endpoint']}"
    data, elapsed = _http_json(url, headers, body, "POST", settings["timeout"], settings["retries"])

    if isinstance(data.get("error"), (dict, str)):
        raise Fail("provider error: " + json.dumps(data["error"], ensure_ascii=False)[:600])
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        raise Fail("provider returned no image data: " + json.dumps(data, ensure_ascii=False)[:400])

    row = rows[0]
    if row.get("b64_json"):
        return base64.b64decode(row["b64_json"]), row, elapsed
    if row.get("url"):
        last: Exception | None = None
        for attempt in range(settings["retries"] + 1):
            try:
                with urllib.request.urlopen(row["url"], timeout=settings["timeout"],
                                            context=_ssl_context()) as response:
                    return response.read(), row, elapsed
            except Exception as exc:  # noqa: BLE001 - reported as a fail-closed error
                last = exc
                time.sleep(2 * (attempt + 1))
        raise Fail(f"could not download the provider image: {last}")
    raise Fail("provider returned neither b64_json nor url")


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def image_dimensions(raw: bytes) -> tuple[int | None, int | None]:
    try:
        from PIL import Image  # type: ignore
        with Image.open(io.BytesIO(raw)) as image:
            return image.size
    except Exception:
        pass
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if raw[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(raw):
            if raw[index] != 0xFF:
                index += 1
                continue
            marker = raw[index + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                height = int.from_bytes(raw[index + 5:index + 7], "big")
                width = int.from_bytes(raw[index + 7:index + 9], "big")
                return width, height
            index += 2 + int.from_bytes(raw[index + 2:index + 4], "big")
    return None, None


def check_canvas(plan: dict, width, height) -> dict:
    """Report whether the native output honoured the requested canvas."""
    requested = plan.get("size") or "auto"
    if requested == "auto" or not width or not height:
        return {"canvas_match": None, "requested": requested, "actual": f"{width}x{height}"}
    try:
        req_w, req_h = (int(part) for part in requested.split("x")[:2])
    except Exception:
        return {"canvas_match": None, "requested": requested, "actual": f"{width}x{height}"}
    tolerance = 0.02
    ratio_match = abs((width / height) - (req_w / req_h)) / (req_w / req_h) <= tolerance
    exact = (width, height) == (req_w, req_h)
    return {
        "canvas_match": bool(exact),
        "aspect_match": bool(ratio_match),
        "requested": requested,
        "actual": f"{width}x{height}",
    }


def write_output(raw: bytes, out_path: Path) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_suffix(out_path.suffix + ".part")
    temporary.write_bytes(raw)
    os.replace(temporary, out_path)
    width, height = image_dimensions(raw)
    return {
        "image": str(out_path.resolve()),
        "width": width,
        "height": height,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_list_models(args: argparse.Namespace, settings: dict) -> dict:
    url = f"{settings['base_url']}/models"
    data, elapsed = _http_json(url, {"Authorization": "Bearer " + settings["api_key"]},
                               None, "GET", settings["timeout"], settings["retries"])
    return {"status": "OK", "command": "list-models", "elapsed_sec": round(elapsed, 2),
            "models": [row.get("id") for row in data.get("data", [])]}


def cmd_generate(args: argparse.Namespace, settings: dict) -> dict:
    plan = build_plan(args, settings)

    if args.dry_run:
        plan.update({"status": "DRY_RUN", "command": "generate", "prompt": plan["prompt"]})
        return plan

    if not args.out and not plan.get("declared_output"):
        raise Fail("--out is required to generate an image")
    out_path = Path(args.out or plan["declared_output"])

    raw, row, elapsed = call_provider(plan, settings)
    result = write_output(raw, out_path)
    canvas = check_canvas(plan, result["width"], result["height"])

    receipt = {
        "schema_version": "artflow-render-receipt-1.0.0",
        "provider": "artflow",
        "base_url": plan["base_url"],
        "endpoint": plan["endpoint"],
        "model": plan["model"],
        "quality": plan["quality"],
        "quality_source": plan["quality_source"],
        "requested_size": plan["size"],
        "size_source": plan["size_source"],
        "canvas": canvas,
        "family": plan["family"],
        "token": plan["token"],
        "packet_id": plan["packet_id"],
        "request": plan["request"],
        "prompt_source": plan["prompt_source"],
        "prompt_chars": plan["prompt_chars"],
        "prompt_sha256": hashlib.sha256(plan["prompt"].encode("utf-8")).hexdigest(),
        "references": plan["references"],
        "references_requested": plan["references_requested"],
        "references_dropped": plan["references_dropped"],
        "native_output": result,
        "provider_revised_prompt": row.get("revised_prompt"),
        "provider_channel": "b64_json" if row.get("b64_json") else "url",
        "elapsed_sec": round(elapsed, 2),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": (
            "This file is the provider's native original, stored unmodified. "
            "No local resize, resample, upscale or post text overlay was applied."
        ),
    }
    if not args.no_receipt:
        receipt_path = out_path.with_suffix(out_path.suffix + ".receipt.json")
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        receipt["receipt_path"] = str(receipt_path.resolve())

    return {"status": "OK", "command": "generate", **receipt}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an image through the artflow gateway for the Aurora Skill family.")
    parser.add_argument("--request", help="Skill-emitted generation request JSON, or a render-packets document")
    parser.add_argument("--packet-id", help="Select one ai_packet out of a render-packets document")
    parser.add_argument("--out", help="Output image path (native original, written verbatim)")
    parser.add_argument("--prompt", help="Explicit prompt text (overrides the request prompt)")
    parser.add_argument("--prompt-file", help="Read the prompt from a UTF-8 text file")
    parser.add_argument("--reference", action="append", default=[],
                        help="Reference image path; repeatable. Forces the images/edits endpoint.")
    parser.add_argument("--size", help="Canvas as WxH, or 'auto'")
    parser.add_argument("--quality", help="low | medium | high")
    parser.add_argument("--model", help=f"Model id (default {DEFAULT_MODEL})")
    parser.add_argument("--api-key", help="artflow API key")
    parser.add_argument("--base-url", help=f"API base (default {DEFAULT_BASE_URL})")
    parser.add_argument("--config", help="Path to an artflow config JSON")
    parser.add_argument("--timeout", type=int, help=f"Per-request timeout (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--retries", type=int, help=f"Retries for transient failures (default {DEFAULT_RETRIES})")
    parser.add_argument("--max-references", type=int, help=f"Cap on reference images (default {DEFAULT_MAX_REFERENCES})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve and print the plan without calling the provider")
    parser.add_argument("--allow-untokened", action="store_true",
                        help="Permit a request that carries no generation token (ad-hoc use)")
    parser.add_argument("--list-models", action="store_true", help="Query the gateway's model list")
    parser.add_argument("--no-receipt", action="store_true", help="Do not write the sidecar receipt")
    parser.add_argument("--json-errors", action="store_true", help="Accepted for compatibility; errors are always JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = build_parser().parse_args(argv)
    try:
        settings = resolve_settings(args, load_config(args.config))
        if args.list_models:
            result = cmd_list_models(args, settings)
        else:
            result = cmd_generate(args, settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Fail as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "FAILED", "error": "interrupted"}, ensure_ascii=False), file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - fail closed with a machine-readable error
        print(json.dumps({"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
