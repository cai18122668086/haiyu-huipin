#!/usr/bin/env python3
"""Concurrent image generation for the Aurora / style-benchmark Skill family.

This is the batch sibling of ``artflow_generate.py``. It changes only *how many
images are in flight at once* - every image still goes through exactly the same
single-image code path, so the Skill family's generation-token gates, handoff
contracts, provenance receipts and audits are untouched.

Why this exists, and the limit found while building it
------------------------------------------------------
Image generation is network-bound: measured over 111 real renders from this
project the provider took a median of ~53s and an average of ~63s per image
(max 239s), and the Skill invoked the bridge once per image, waiting each time.
A fourteen-image set therefore spent roughly fifteen minutes doing nothing but
waiting on sockets that could have been open simultaneously.

How many the gateway will actually hold open depends on the endpoint, and that
was measured rather than assumed:

* ``images/generations`` (prompt only) - four at once returned four good images:
  698s of provider time in 176s of wall clock, 3.96x.
* ``images/edits`` (carries product reference images) - two at once returned two
  HTTP 413s; three at once returned two HTTP 413s; three issued one at a time
  returned three good images. The gateway draws the line at reference images.

So this script is concurrent where concurrency pays and deliberately serial
where it does not, splitting jobs by endpoint and applying a separate limit to
each (``--concurrency`` and ``--edits-concurrency``). Almost every render in this
Skill family carries a product reference, so expecting a blanket speedup here
would be wrong - the honest win is that this costs nothing everywhere and saves
about four fifths of the wall clock wherever a render has no reference image.

Usage
-----
    python scripts/artflow_generate_batch.py \
        --job "<request.json>=<image.png>" \
        --job "<request.json>=<image.png>" \
        --concurrency 4

Each job is one (request, output) pair. A job whose request is a render-packets
document selects one ``ai_packet`` with a third field::

    --job "<render-packets.json>=<image.png>|<packet-id>"

Jobs are grouped by endpoint and each group runs at its own limit - see above.
``--size``, ``--quality``, ``--model``, ``--reference`` and the credential flags
apply to every job; anything per-job belongs in the request JSON, which is where
the Skill already puts its canvas, quality and token.

Why the output paths are named on the command line
--------------------------------------------------
The Skill audit attributes every image to the call that produced it by looking
for the image's name in the recorded tool calls (``check_provenance`` in
``scripts/audit-skill-run.py``). An output path that only ever appears inside a
JSON file is invisible to that check and would be reported as unattributed, i.e.
as having no recorded origin. Listing every output on the command line as above
keeps each one attributable. Prefer this form over a jobs file for exactly that
reason.

Contract
--------
* Always prints exactly one JSON object on stdout describing every job.
* Writes the same sidecar ``<out>.receipt.json`` per image that single-image
  mode writes, byte-compatible, one per image.
* Exits non-zero (fail closed) if any job failed. Images that succeeded are
  kept, so a partial batch is repairable rather than lost - but the Skill must
  treat a non-zero exit as "the delivery is not complete yet".
* A failure in one job never cancels the others.
* Failures sustained while everything was in flight are retried one at a time
  before the run reports; see `--drain-retries`. Concurrency causes those
  failures, so retrying them serially is what actually clears them.

Standard library only, matching the bridge it builds on.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

BRIDGE_FILENAME = "artflow_generate.py"

DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 16
# What the provider was measured to accept, not what we would like it to accept.
# Four concurrent `images/generations` returned four good images (698s of provider
# time in 176s of wall clock); two concurrent `images/edits` returned two HTTP 413s
# and three returned two HTTP 413s, whereas three edits issued one at a time all
# succeeded. A carry-along reference image is the difference the gateway draws.
DEFAULT_EDITS_CONCURRENCY = 1
DEFAULT_DRAIN_RETRIES = 1
# Grace period before the drain starts; see drain() for why waiting helps.
DRAIN_SETTLE_SEC = 15

_print_lock = threading.Lock()


# --------------------------------------------------------------------------
# load the sibling bridge
# --------------------------------------------------------------------------


def load_bridge():
    """Import ``artflow_generate.py`` from whichever directory this file sits in.

    Loading it explicitly - rather than relying on ``sys.path`` - matters because
    the Skill may be invoked from any working directory, and because reusing the
    module is the whole point: the batch path must render an image in exactly the
    same way single-image mode does, including the token gate, family detection,
    canvas resolution and receipt schema.
    """
    here = Path(__file__).resolve().parent
    candidate = here / BRIDGE_FILENAME
    if not candidate.is_file():
        raise SystemExit(
            json.dumps({
                "status": "FAILED",
                "command": "generate-batch",
                "error": f"the provider bridge {BRIDGE_FILENAME} is not present beside this script "
                         f"(looked in {here}); batch rendering needs it",
            }, ensure_ascii=False)
        )
    spec = importlib.util.spec_from_file_location("artflow_generate", candidate)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(json.dumps({"status": "FAILED", "command": "generate-batch",
                                     "error": f"could not load {candidate}"}, ensure_ascii=False))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BRIDGE = load_bridge()
Fail = BRIDGE.Fail


# --------------------------------------------------------------------------
# job parsing
# --------------------------------------------------------------------------


def parse_job(raw: str) -> dict:
    """``"<request>=<out>"`` or ``"<request>=<out>|<packet-id>"`` -> job dict."""
    if "=" not in raw:
        raise Fail(f'job must look like "<request.json>=<image.png>", got {raw!r}')
    request, _, rest = raw.partition("=")
    out, _, packet = rest.partition("|")
    request, out = request.strip(), out.strip().strip('"').strip("'")
    packet = packet.strip() or None
    if not request or not out:
        raise Fail(f'job is missing its request or output side: {raw!r}')
    return {"request": request, "out": out, "packet_id": packet}


def validate(jobs: list[dict]) -> None:
    seen: dict[str, str] = {}
    for job in jobs:
        request = Path(job["request"])
        if not request.is_file():
            raise Fail(f"generation request not found: {request}")
        key = os.path.normcase(os.path.abspath(job["out"]))
        if key in seen:
            raise Fail(
                f"two jobs write the same output ({job['out']}); rendering concurrent jobs to one "
                "path would produce whichever finished last, with no way to tell which"
            )
        seen[key] = job["out"]


def job_namespace(job: dict, args: argparse.Namespace):
    """Build the single-image argument namespace this job represents."""
    argv = ["--request", job["request"], "--out", job["out"]]
    if job.get("packet_id"):
        argv += ["--packet-id", job["packet_id"]]
    for flag, value in (
        ("--size", args.size), ("--quality", args.quality), ("--model", args.model),
        ("--config", args.config), ("--api-key", args.api_key), ("--base-url", args.base_url),
        ("--prompt", args.prompt), ("--prompt-file", args.prompt_file),
    ):
        if value:
            argv += [flag, str(value)]
    for reference in args.reference or []:
        argv += ["--reference", reference]
    if args.timeout is not None:
        argv += ["--timeout", str(args.timeout)]
    if args.retries is not None:
        argv += ["--retries", str(args.retries)]
    if args.max_references is not None:
        argv += ["--max-references", str(args.max_references)]
    if args.allow_untokened:
        argv.append("--allow-untokened")
    if args.no_receipt:
        argv.append("--no-receipt")
    return BRIDGE.build_parser().parse_args(argv)


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


def render(job: dict, namespace, settings: dict) -> dict:
    started = time.time()
    try:
        result = BRIDGE.cmd_generate(namespace, settings)
        result.update({"id": job["out"], "job": f'{job["request"]}={job["out"]}'})
        return result
    except Fail as exc:
        return {"id": job["out"], "job": f'{job["request"]}={job["out"]}',
                "status": "FAILED", "error": str(exc), "elapsed_sec": round(time.time() - started, 2)}
    except Exception as exc:  # noqa: BLE001 - one job must not take the others down with it
        return {"id": job["out"], "job": f'{job["request"]}={job["out"]}',
                "status": "FAILED", "error": f"{type(exc).__name__}: {exc}",
                "elapsed_sec": round(time.time() - started, 2)}


def run_batch(jobs: list[dict], namespaces, settings: dict, concurrency: int) -> list[dict | None]:
    results: list[dict | None] = [None] * len(jobs)
    with futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending = {
            pool.submit(render, job, namespace, settings): index
            for index, (job, namespace) in enumerate(zip(jobs, namespaces))
        }
        for future in futures.as_completed(pending):
            index = pending[future]
            results[index] = future.result()
            with _print_lock:
                one = results[index] or {}
                marker = "ok" if one.get("status") == "OK" else "FAILED"
                native = one.get("native_output") or {}
                size = f'{native.get("width")}x{native.get("height")}' if native.get("width") else "-"
                print(f"[{index + 1}/{len(jobs)}] {marker} {os.path.basename(str(one.get('id', '')))} "
                      f"{size} {one.get('elapsed_sec', 0)}s", file=sys.stderr, flush=True)
    return results


def drain(jobs: list[dict], namespaces, results: list[dict | None], settings: dict,
          attempts: int) -> int:
    """Retry failures one at a time, and return how many recovered.

    Concurrency is the whole point of this script, but it is also the reason some
    jobs fail: measured on artflow, three simultaneous `images/edits` calls came
    back `HTTP 413 The image generation tool encountered an error` while the first
    of them succeeded - the gateway, not the request, was the thing that could not
    take them together. Those failures are worth retrying on their own rather than
    letting them sink the batch, so this phase re-runs them serially. Exactly one
    extra attempt per job is the default: if a job fails twice it is genuinely
    failing, and saying so beats spending more time assuming otherwise.
    """
    if attempts < 1:
        return 0

    # Failures caused by overloading the gateway do not clear instantly: a rerun
    # started ~10 minutes after a failed concurrent burst succeeded three for
    # three, while a retry issued immediately afterwards failed again. Give the
    # window a chance to roll over before asking for anything else.
    time.sleep(DRAIN_SETTLE_SEC)

    recovered = 0
    for index, one in enumerate(results):
        if one and one.get("status") == "OK":
            continue
        for attempt in range(attempts):
            with _print_lock:
                print(f"draining {os.path.basename(str(jobs[index]['out']))} "
                      f"(attempt {attempt + 1}/{attempts}, one at a time)", file=sys.stderr, flush=True)
            retry = render(jobs[index], namespaces[index], settings)
            attempts_made = float(one.get("elapsed_sec") or 0) if one else 0.0
            retry["elapsed_sec"] = round(attempts_made + float(retry.get("elapsed_sec") or 0), 2)
            results[index] = retry
            if retry.get("status") == "OK":
                recovered += 1
                break
            time.sleep(2 * (attempt + 1))
    return recovered


def cmd_generate_batch(args: argparse.Namespace, settings: dict) -> dict:
    jobs = [parse_job(raw) for raw in args.job]
    if not jobs:
        raise Fail("no jobs given; repeat --job \"<request.json>=<image.png>\" once per image")
    validate(jobs)

    namespaces = [job_namespace(job, args) for job in jobs]

    plans: list[dict] = []
    for index, (job, namespace) in enumerate(zip(jobs, namespaces)):
        try:
            # Resolving every plan before spending anything means a request with
            # no generation token, or one that names a file that is not there,
            # stops the whole batch before a single image is paid for.
            plans.append(BRIDGE.build_plan(namespace, settings))
        except Fail as exc:
            raise Fail(f"job {index + 1} ({job['request']} -> {job['out']}): {exc}") from exc

    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "command": "generate-batch",
                          "jobs": len(jobs), "model": settings["model"],
                          "results": [{**job, **plan, "prompt": None}
                                      for job, plan in zip(jobs, plans)]},
                         ensure_ascii=False, indent=2))
        return {}

    # Endpoint decides how many may fly at once. See build_parser: measured on
    # artflow, text-only `images/generations` scales cleanly, while every attempt
    # at two-or-more simultaneous `images/edits` came back HTTP 413.
    edits = [i for i, p in enumerate(plans) if p.get("endpoint") == "images/edits"]
    plain = [i for i, p in enumerate(plans) if p.get("endpoint") != "images/edits"]

    started = time.time()
    results: list[dict | None] = [None] * len(jobs)

    def place(indices: list[int], concurrency: int) -> None:
        if not indices:
            return
        part = run_batch([jobs[i] for i in indices], [namespaces[i] for i in indices],
                         settings, concurrency)
        for slot, index in enumerate(indices):
            results[index] = part[slot]

    place(plain, args.concurrency)
    place(edits, args.edits_concurrency)

    failures_before = len([r for r in results if r and r.get("status") != "OK"])
    recovered = drain(jobs, namespaces, results, settings, args.drain_retries)
    wall = time.time() - started

    ok = [r for r in results if r and r.get("status") == "OK"]
    bad = [r for r in results if r and r.get("status") != "OK"]
    individual = sum(float(r.get("elapsed_sec") or 0) for r in results if r)

    return {
        "status": "OK" if not bad else ("PARTIAL" if ok else "FAILED"),
        "command": "generate-batch",
        "jobs": len(jobs),
        "succeeded": len(ok),
        "failed": len(bad),
        "concurrency": args.concurrency,
        "editsConcurrency": args.edits_concurrency,
        "groups": {
            "images/generations": {"jobs": len(plain), "concurrency": args.concurrency},
            "images/edits": {"jobs": len(edits), "concurrency": args.edits_concurrency},
        },
        "failedOnSurge": failures_before,
        "recoveredOnDrain": recovered,
        "drainRetries": args.drain_retries,
        "model": settings["model"],
        "quality": settings["quality"],
        "base_url": settings["base_url"],
        "elapsed_sec": round(wall, 2),
        "serial_estimate_sec": round(individual, 2),
        "speedup": round(individual / wall, 2) if wall > 0 else None,
        "images": [r.get("image") for r in ok],
        "results": results,
        "errors": [{"id": r.get("id"), "error": r.get("error")} for r in bad],
        "note": (
            "Each image kept its own sidecar receipt, so every one of them is attributable "
            "individually. `failedOnSurge` is how many failed while everything was in flight, "
            "`recoveredOnDrain` how many of those then succeeded when retried alone. A non-zero "
            "exit below means at least one job failed; succeeded images are kept so the batch "
            "can be repaired rather than restarted."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render several images concurrently through the artflow gateway.")
    parser.add_argument("--job", action="append", default=[], metavar="REQUEST=OUT",
                        help='One image to render: "<request.json>=<image.png>", optionally '
                             '"<request.json>=<image.png>|<packet-id>". Repeat per image.')
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help=f"Simultaneous provider calls (default {DEFAULT_CONCURRENCY}, max {MAX_CONCURRENCY})")
    parser.add_argument("--edits-concurrency", type=int, default=DEFAULT_EDITS_CONCURRENCY,
                        help="How many reference-bearing jobs (images/edits) may overlap. "
                             f"Defaults to {DEFAULT_EDITS_CONCURRENCY}, i.e. one at a time, because that "
                             "is what the gateway was measured to accept: two simultaneous edits calls "
                             "came back HTTP 413 'The image generation tool encountered an error', as "
                             "did three, while three issued one at a time all succeeded. Raise it only "
                             "if you have evidence your gateway tolerates overlapping edits.")
    parser.add_argument("--drain-retries", type=int, default=DEFAULT_DRAIN_RETRIES,
                        help="After the concurrent pass, retry failures one at a time this many "
                             f"times each (default {DEFAULT_DRAIN_RETRIES}; 0 disables it)")
    parser.add_argument("--size", help="Canvas as WxH, or 'auto' (applied to every job)")
    parser.add_argument("--quality", help="low | medium | high (applied to every job)")
    parser.add_argument("--model", help="Model id (applied to every job)")
    parser.add_argument("--reference", action="append", default=[],
                        help="Extra reference image; repeatable (applied to every job)")
    parser.add_argument("--prompt", help="Override prompt text for every job")
    parser.add_argument("--prompt-file", help="Override prompt from file for every job")
    parser.add_argument("--api-key", help="artflow API key")
    parser.add_argument("--base-url", help="API base override")
    parser.add_argument("--config", help="Path to an artflow config JSON")
    parser.add_argument("--timeout", type=int, help="Per-request timeout")
    parser.add_argument("--retries", type=int, help="Retries for transient failures")
    parser.add_argument("--max-references", type=int, help="Cap on reference images per job")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve every job and print the plans without calling the provider")
    parser.add_argument("--allow-untokened", action="store_true",
                        help="Permit requests that carry no generation token (ad-hoc use)")
    parser.add_argument("--no-receipt", action="store_true", help="Do not write sidecar receipts")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = build_parser().parse_args(argv)
    args.concurrency = max(1, min(args.concurrency, MAX_CONCURRENCY))
    try:
        settings = BRIDGE.resolve_settings(args, BRIDGE.load_config(args.config))
        result = cmd_generate_batch(args, settings)
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if not result.get("failed") else 2
        return 0
    except Fail as exc:
        print(json.dumps({"status": "FAILED", "command": "generate-batch", "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "FAILED", "command": "generate-batch", "error": "interrupted"},
                         ensure_ascii=False), file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - fail closed with a machine-readable error
        print(json.dumps({"status": "FAILED", "command": "generate-batch",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
