"""Partition the complete tracked backend suite across independent CI checkouts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.shard < args.shards <= 16:
        parser.error("Expected 0 <= shard < shards <= 16")
    root = Path(__file__).resolve().parents[2]
    tracked = (
        subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={root.as_posix()}",
                "ls-files",
                "-z",
                "--",
                "backend",
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )
    files = sorted(
        name
        for name in tracked
        if Path(name).name.startswith("test_")
        and name.endswith(".py")
        and "skill_sources" not in Path(name).parts
    )
    if not files:
        raise RuntimeError("No tracked backend tests were selected")
    selected = files[args.shard :: args.shards]
    print(
        json.dumps(
            {
                "shard": args.shard,
                "shards": args.shards,
                "all_files": len(files),
                "selected_files": len(selected),
                "files": selected,
            }
        ),
        flush=True,
    )
    if args.list:
        return 0
    if not selected:
        raise RuntimeError("An empty shard cannot satisfy backend quality")
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "pytest",
            *selected,
            "-vv",
            "-o",
            "faulthandler_timeout=180",
            "--durations=25",
        ],
        cwd=root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
