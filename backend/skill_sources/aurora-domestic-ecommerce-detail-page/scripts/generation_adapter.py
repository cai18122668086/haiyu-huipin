from __future__ import annotations

import argparse
import json
import hashlib
import subprocess
import sys
from pathlib import Path


HARNESS = Path(__file__).resolve().with_name("harness.py")


def _run_harness(arguments: list[str]) -> dict:
    completed = subprocess.run(
        [sys.executable, str(HARNESS), *arguments], check=False,
        capture_output=True, text=True, encoding="utf-8",
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def prepare(args: argparse.Namespace) -> dict:
    return _run_harness(["generate-next", "--session", args.session, "--out", args.request])


def commit(args: argparse.Namespace) -> dict:
    request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
    embedded = request.pop("request_fingerprint", None)
    canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    calculated = hashlib.sha256(canonical).hexdigest()
    if embedded != calculated:
        raise RuntimeError("Generation request fingerprint is stale or invalid")
    return _run_harness([
        "record-generation", "--session", args.session,
        "--token", request["token"], "--image", args.image,
    ])


def run(args: argparse.Namespace) -> dict:
    request_result = prepare(args)
    command = [part.replace("{request}", str(Path(args.request).resolve())) for part in args.provider_command]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "Image provider failed")
    provider_result = json.loads(completed.stdout)
    image = provider_result.get("image")
    if not image:
        raise RuntimeError("Image provider must return UTF-8 JSON containing an image path")
    record_result = commit(argparse.Namespace(session=args.session, request=args.request, image=image))
    return {"request": request_result, "provider": provider_result, "record": record_result}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Aurora signed generation transaction adapter")
    sub = root.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--session", required=True)
    prepare_parser.add_argument("--request", required=True)
    commit_parser = sub.add_parser("commit")
    commit_parser.add_argument("--session", required=True)
    commit_parser.add_argument("--request", required=True)
    commit_parser.add_argument("--image", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--session", required=True)
    run_parser.add_argument("--request", required=True)
    run_parser.add_argument("--provider-command", required=True, nargs=argparse.REMAINDER)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        result = {"prepare": prepare, "commit": commit, "run": run}[args.command](args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
