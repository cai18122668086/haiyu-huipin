from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = BACKEND_ROOT / "scripts" / "verify_requirements_lock.py"
PYPROJECT = BACKEND_ROOT / "pyproject.toml"
LOCK = BACKEND_ROOT / "requirements.lock"


def _verify(lock: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--pyproject",
            str(PYPROJECT),
            "--lock",
            str(lock),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _drop_first_production_requirement(text: str) -> str:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    first_direct = project["dependencies"][0]
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", first_direct)
    assert match is not None
    direct_name = match.group(0)

    lines = text.splitlines(keepends=True)
    first = next(
        index
        for index, line in enumerate(lines)
        if re.match(rf"(?i)^{re.escape(direct_name)}==", line)
    )
    end = first + 1
    while end < len(lines) and (
        not lines[end].strip() or lines[end][0].isspace()
    ):
        end += 1
    return "".join(lines[:first] + lines[end:])


def test_production_requirements_lock_verifies() -> None:
    result = _verify(LOCK)

    assert result.returncode == 0, result.stderr
    assert "verified packages=" in result.stdout


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda text: text.replace("==", ">=", 1), "exact package pin"),
        (
            lambda text: re.sub(
                r"(?m)^    --hash=sha256:[0-9a-f]{64}\r?\n", "", text, count=1
            ),
            "has no sha256 hash",
        ),
        (
            lambda text: text + "\n--index-url https://example.invalid/simple\n",
            "forbidden lock option",
        ),
        (
            lambda text: re.sub(
                r"(?m)^[A-Za-z0-9_.-]+==[^\r\n]+",
                "demo @ https://example.invalid/demo.whl",
                text,
                count=1,
            ),
            "exact package pin",
        ),
        (
            lambda text: text + "\npytest==9.0.0 \\\n"
            "    --hash=sha256:" + ("0" * 64) + "\n",
            "dev-only dependencies",
        ),
        (_drop_first_production_requirement, "missing production dependencies"),
    ],
)
def test_requirements_lock_rejects_invalid_content(
    tmp_path: Path, mutate: Callable[[str], str], expected: str
) -> None:
    lock_text = LOCK.read_text(encoding="utf-8")
    invalid_lock = tmp_path / "requirements.lock"
    invalid_lock.write_text(mutate(lock_text), encoding="utf-8")

    result = _verify(invalid_lock)

    assert result.returncode == 1
    assert expected in result.stderr
