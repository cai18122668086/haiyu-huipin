"""Immutable source and native CLI boundary for the user-supplied fast Skill."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import default_skill_root
from .new_detail_skill_provider import BundledSkillManifestValidator

SKILL_ID = "aurora-remix-fast"


@dataclass(frozen=True)
class RemixFastRuntime:
    source_root: Path = default_skill_root(SKILL_ID)

    def verify(self) -> str:
        return BundledSkillManifestValidator(
            source_roots={SKILL_ID: self.source_root}
        ).validate(SKILL_ID)

    def run(
        self,
        command: str,
        workspace: Path,
        image: Path,
        *,
        output: Path | None = None,
        box: tuple[float, ...] | None = None,
    ) -> dict[str, Any]:
        self.verify()
        root = workspace.resolve()
        paths = [image] + ([output] if output is not None else [])
        if any(not path.resolve().is_relative_to(root) for path in paths):
            raise ValueError("Native fastprep path escaped the job workspace")
        if command not in {"ratio", "wmcheck", "zoom"}:
            raise ValueError("Unsupported native fastprep command")
        argv = [
            sys.executable,
            str(self.source_root / "scripts/fastprep.py"),
            command,
            str(image),
        ]
        if command in {"wmcheck", "zoom"}:
            if output is None:
                raise ValueError("Native fastprep output is required")
            argv.extend(["--out", str(output)])
        if command == "zoom":
            if box is None or len(box) != 4:
                raise ValueError("A normalized zoom region is required")
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("Zoom region is outside the candidate")
            argv.extend(["--box", ",".join(str(value) for value in box)])
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            cwd=root,
        )
        if result.returncode:
            raise ValueError("Native fastprep failed")
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get("error"):
            raise ValueError("Native fastprep returned an invalid receipt")
        return value

    def prompt_template(self) -> str:
        text = (self.source_root / "SKILL.md").read_text(encoding="utf-8")
        match = re.search(
            r"\x60{3}\n(Create ONE complete Chinese ecommerce visual.*?"
            r"Target canvas <W>×<H> with the reference crop aspect ratio\.)\n\x60{3}",
            text,
            re.DOTALL,
        )
        if match is None:
            raise ValueError("The original fast Skill prompt template is missing")
        return match.group(1)
