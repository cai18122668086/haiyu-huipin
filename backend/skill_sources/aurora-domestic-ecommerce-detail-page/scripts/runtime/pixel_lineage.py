"""Read-only raster lineage: provider original, approved normalization, fixed crops."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .artifact_binding import file_sha256
from .image_format import pixel_sha256, verify_normalized_image
from .state import HarnessError

VERSION = "native-pixel-lineage-v1"


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _preserve_immutable(source: Path, workspace: Path, token: str, directory: str, suffix: str) -> Path:
    payload = source.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    name = base64.urlsafe_b64encode(hashlib.sha256((token + ":" + sha).encode()).digest()).decode().rstrip("=")
    target = (workspace / directory / (name + suffix)).resolve()
    if not target.is_relative_to(workspace.resolve()):
        raise HarnessError("Immutable source escaped the session workspace")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if file_sha256(target) != sha:
            raise HarnessError("The immutable original bytes changed")
    return target


def preserve_provider_original(source: Path, workspace: Path, token: str) -> Path:
    return _preserve_immutable(source, workspace, token, "provider-originals", ".image")


def preserve_signed_request(source: Path, workspace: Path, token: str) -> Path:
    return _preserve_immutable(source, workspace, token, "signed-requests", ".json")


def create_fixed_slices(canvas: Path, workspace: Path, pair_id: str, module_ids: list[str], width: int, height: int, attempt: int) -> list[dict]:
    directory = (workspace / "pair-slices" / pair_id).resolve()
    if not directory.is_relative_to(workspace.resolve()):
        raise HarnessError("Pair slices escaped the session workspace")
    directory.mkdir(parents=True, exist_ok=True)
    canvas_sha = file_sha256(canvas)
    result = []
    with Image.open(canvas) as opened:
        if opened.size != (width, height * 2) or opened.mode != "RGB":
            raise HarnessError("Pair slicing requires the exact normalized RGB canvas")
        for offset, module_id in enumerate(module_ids):
            path = (directory / f"{module_id}-attempt-{attempt:02d}.png").resolve()
            if not path.is_relative_to(directory):
                raise HarnessError("A module slice escaped its pair directory")
            box = (0, offset * height, width, (offset + 1) * height)
            crop = opened.crop(box)
            output = BytesIO()
            crop.save(output, format="PNG", optimize=True)
            payload = output.getvalue()
            sha = hashlib.sha256(payload).hexdigest()
            pixel_sha = pixel_sha256(crop)
            crop.close()
            try:
                with path.open("xb") as stream:
                    stream.write(payload)
            except FileExistsError:
                if file_sha256(path) != sha:
                    raise HarnessError("An existing derived slice changed; it cannot be recreated silently")
            result.append({"module_id": module_id, "path": str(path),
                           "start_y": box[1], "end_y": box[3], "crop_box": list(box),
                           "sha256": sha, "pixel_sha256": pixel_sha,
                           "canvas_sha256": canvas_sha})
    return result


def verify_fixed_slices(slices: list[dict], canvas: Path, canvas_sha: str, module_ids: list[str], width: int, height: int) -> None:
    if len(slices) != 2 or [row.get("module_id") for row in slices] != module_ids:
        raise HarnessError("Fixed slices must match the ordered adjacent module IDs")
    if file_sha256(canvas) != canvas_sha:
        raise HarnessError("The normalized pair canvas bytes changed")
    with Image.open(canvas) as master:
        if master.size != (width, height * 2) or master.mode != "RGB":
            raise HarnessError("Pair canvas dimensions or RGB mode changed")
        for offset, row in enumerate(slices):
            box = [0, offset * height, width, (offset + 1) * height]
            if (row.get("crop_box") != box or row.get("start_y") != box[1]
                    or row.get("end_y") != box[3] or row.get("canvas_sha256") != canvas_sha):
                raise HarnessError("Derived slice no longer uses its approved fixed window")
            path = Path(row["path"])
            if file_sha256(path) != row.get("sha256"):
                raise HarnessError("Derived pair slice bytes changed")
            with Image.open(path) as image:
                if image.size != (width, height) or image.mode != "RGB" or image.format != "PNG":
                    raise HarnessError("Derived pair slice dimensions, RGB mode or PNG format changed")
                actual = pixel_sha256(image)
            crop = master.crop(tuple(box))
            expected = pixel_sha256(crop)
            crop.close()
            if actual != row.get("pixel_sha256") or actual != expected:
                raise HarnessError("Derived slice pixels do not match their exact fixed canvas crop")


def pair_artifacts(session: dict, pair_id: str, selection: str = "LATEST_RECORDED") -> dict:
    try:
        plan = session["plan"]
        strategy = plan["output_strategy"]
        pair = next(row for row in strategy["continuous_pair_rendering"]["pair_ids"] if row["pair_id"] == pair_id)
        module_ids = pair["module_ids"]
        if len(module_ids) != 2 or len(set(module_ids)) != 2:
            raise HarnessError("Artifact lineage requires two ordered modules")
        by_id = {row["id"]: row for row in session["modules"]}
        modules = [by_id[key] for key in module_ids]
        if selection == "ACCEPTED":
            if any(row.get("status") not in {"COMPLETE", "COMPLETE_WITH_RESIDUALS"} for row in modules):
                raise HarnessError("The requested pair has no accepted artifact lineage")
            attempts = [next(attempt for attempt in reversed(row["attempts"]) if attempt["image"] == row.get("accepted_asset")) for row in modules]
        elif selection == "LATEST_RECORDED":
            attempts = [row["attempts"][-1] for row in modules]
        else:
            raise HarnessError("Unknown artifact lineage selection")
        first = attempts[0]
        common = ("pair_id", "token", "pair_canvas", "source_image", "source_image_sha256", "normalized_image_sha256", "normalization", "cycle_id", "cycle_attempt")
        if first["pair_id"] != pair_id or any(any(row.get(key) != first.get(key) for key in common) for row in attempts):
            raise HarnessError("Both slices must belong to one original render and normalization")
        entry = next(row for row in session["generation_requests"] if row["token"] == first["token"])
        request_path = Path(entry["request"])
        request_bytes = request_path.read_bytes()
        request = json.loads(request_bytes)
        unsigned = {key: value for key, value in request.items() if key != "request_fingerprint"}
        if (hashlib.sha256(request_bytes).hexdigest() != entry["request_sha256"]
                or request.get("request_fingerprint") != _fingerprint(unsigned)
                or request.get("request_fingerprint") != entry["request_fingerprint"]
                or request.get("token") != first["token"] or request.get("pair_id") != pair_id
                or request.get("module_ids") != module_ids):
            raise HarnessError("Pair pixels do not belong to the original signed request")
        width, height = int(strategy["canvas_width_px"]), int(strategy["module_height_px"])
        if request["output_surface"]["width_px"] != width or request["output_surface"]["height_px"] != height * 2:
            raise HarnessError("Signed request dimensions differ from the pixel lineage")
        source, canvas = Path(first["source_image"]), Path(first["pair_canvas"])
        source_sha, canvas_sha = file_sha256(source), file_sha256(canvas)
        if source_sha != first["source_image_sha256"] or canvas_sha != first["normalized_image_sha256"]:
            raise HarnessError("Provider original or normalized canvas bytes changed")
        normalization = first["normalization"]
        canvas_pixel_sha = verify_normalized_image(source, canvas, width, height * 2, normalization)
        slices = [row["pair_slice"] for row in attempts]
        if any(row["image"] != row["pair_slice"]["path"] for row in attempts):
            raise HarnessError("Module image differs from its fixed native slice")
        verify_fixed_slices(slices, canvas, canvas_sha, module_ids, width, height)
        result = {"version": VERSION, "pair_id": pair_id, "module_ids": module_ids,
                  "token": first["token"], "cycle_id": first["cycle_id"], "cycle_attempt": first["cycle_attempt"],
                  "signed_request_sha256": hashlib.sha256(request_bytes).hexdigest(),
                  "request_fingerprint": request["request_fingerprint"],
                  "source": {"path": str(source), "sha256": source_sha,
                             "width_px": normalization["source_encoded_width_px"], "height_px": normalization["source_encoded_height_px"]},
                  "canvas": {"path": str(canvas), "sha256": canvas_sha,
                             "pixel_sha256": canvas_pixel_sha, "width_px": width, "height_px": height * 2},
                  "normalization": normalization, "slices": slices}
        result["sha256"] = _fingerprint(result)
        if selection == "ACCEPTED" and any(row.get("artifact_lineage_sha256") != result["sha256"] for row in attempts):
            raise HarnessError("Accepted artifact lineage differs from its original pixel audit")
        return result
    except (KeyError, IndexError, StopIteration, TypeError, OSError, ValueError, UnidentifiedImageError) as exc:
        if isinstance(exc, HarnessError):
            raise
        raise HarnessError("The exact original pair pixel lineage is missing, changed or unreadable") from exc
