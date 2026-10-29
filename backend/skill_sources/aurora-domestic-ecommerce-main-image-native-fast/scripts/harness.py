#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

RUNTIME = Path(__file__).resolve().parent / "runtime"
sys.path.insert(0, str(RUNTIME))

from core import HarnessError  # noqa: E402
import session as workflow  # noqa: E402


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Aurora domestic e-commerce six-image executor v2.0.0")
    sub = value.add_subparsers(dest="command", required=True)

    command = sub.add_parser("start")
    command.add_argument("--input", required=True)
    command.add_argument("--session", required=True)
    command.set_defaults(call=lambda a: workflow.start(a.input, a.session))

    command = sub.add_parser("lock-identity")
    command.add_argument("--session", required=True)
    command.add_argument("--identity", required=True)
    command.set_defaults(call=lambda a: workflow.lock_identity(a.session, a.identity))

    command = sub.add_parser("compile")
    command.add_argument("--session", required=True)
    command.add_argument("--plan", help="optional manual plan using schemas/plan.schema.json")
    command.set_defaults(call=lambda a: workflow.compile_campaign(a.session, a.plan))

    command = sub.add_parser("authorize")
    command.add_argument("--session", required=True)
    command.add_argument("--approval-text", required=True)
    command.set_defaults(call=lambda a: workflow.authorize(a.session, a.approval_text))

    command = sub.add_parser("generate-next")
    command.add_argument("--session", required=True)
    command.add_argument("--out")
    command.set_defaults(call=lambda a: workflow.generate_next(a.session, a.out))

    command = sub.add_parser("record-generation")
    command.add_argument("--session", required=True)
    command.add_argument("--token", required=True)
    command.add_argument("--image", required=True)
    command.set_defaults(call=lambda a: workflow.record_generation(a.session, a.token, a.image))

    command = sub.add_parser("recover-generation")
    command.add_argument("--session", required=True)
    command.add_argument("--token", required=True)
    command.add_argument("--generated-dir", required=True)
    command.set_defaults(call=lambda a: workflow.recover_generation(a.session, a.token, a.generated_dir))

    command = sub.add_parser("audit-current")
    command.add_argument("--session", required=True)
    command.add_argument("--judgment", required=True)
    command.set_defaults(call=lambda a: workflow.audit_current(a.session, a.judgment))

    command = sub.add_parser("prepare-repair")
    command.add_argument("--session", required=True)
    command.add_argument("--repair-plan", required=True)
    command.set_defaults(call=lambda a: workflow.prepare_repair(a.session, a.repair_plan))

    command = sub.add_parser("calibrate-search")
    command.add_argument("--session", required=True)
    command.add_argument("--observation", required=True)
    command.set_defaults(call=lambda a: workflow.calibrate_search(a.session, a.observation))

    command = sub.add_parser("calibrate-style")
    command.add_argument("--session", required=True)
    command.add_argument("--observation", required=True)
    command.set_defaults(call=lambda a: workflow.calibrate_style(a.session, a.observation))

    command = sub.add_parser("apply-user-adjustment")
    command.add_argument("--session", required=True)
    command.add_argument("--adjustment", required=True)
    command.set_defaults(call=lambda a: workflow.apply_user_adjustment(a.session, a.adjustment))

    command = sub.add_parser("audit-set")
    command.add_argument("--session", required=True)
    command.add_argument("--observation", required=True)
    command.set_defaults(call=lambda a: workflow.audit_set(a.session, a.observation))

    command = sub.add_parser("finalize")
    command.add_argument("--session", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--approval-text", required=True)
    command.set_defaults(call=lambda a: workflow.finalize(a.session, a.output, a.approval_text))

    command = sub.add_parser("restart-campaign")
    command.add_argument("--session", required=True)
    command.add_argument("--reason", required=True)
    command.set_defaults(call=lambda a: workflow.restart_campaign(a.session, a.reason))

    command = sub.add_parser("inspect")
    command.add_argument("--session", required=True)
    command.set_defaults(call=lambda a: workflow.inspect(a.session))
    return value


def main() -> int:
    disabled = {"audit-current", "prepare-repair", "calibrate-search", "calibrate-style", "apply-user-adjustment", "audit-set", "restart-campaign"}
    if len(sys.argv) > 1 and sys.argv[1] in disabled:
        print(json.dumps({"status": "BLOCKED", "error": "Command disabled in native-fast; this version performs technical delivery without AI review or automatic modifications."}))
        return 3
    args = parser().parse_args()
    try:
        result = args.call(args)
    except HarnessError as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, ensure_ascii=False))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
