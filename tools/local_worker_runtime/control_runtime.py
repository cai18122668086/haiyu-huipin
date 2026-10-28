"""Request a drain without terminating the supervisor or its active task."""

import argparse
import json
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "drain"))
    parser.add_argument("--worker-root", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    root = args.worker_root / "runtime-control"
    status = root / "status.json"
    if args.action == "status":
        print(
            status.read_text(encoding="utf-8")
            if status.exists()
            else '{"phase":"unknown"}'
        )
        return 0
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".stop.{os.getpid()}.tmp"
    temporary.write_text("drain\n", encoding="ascii")
    os.replace(temporary, root / "stop.request")
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if status.exists():
            state = json.loads(status.read_text(encoding="utf-8"))
            if state.get("phase") == "stopped":
                print(json.dumps({"drained": True, "pid": state.get("pid")}))
                return 0
        time.sleep(0.2)
    print(json.dumps({"drained": False, "forced_termination": False}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
