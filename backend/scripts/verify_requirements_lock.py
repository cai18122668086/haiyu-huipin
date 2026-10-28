"""Validate the production requirements lock and an optional target wheelhouse."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path

NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
VERSION_PATTERN = r"[A-Za-z0-9][A-Za-z0-9.!+_-]*"
REQUIREMENT_RE = re.compile(
    rf"^(?P<name>{NAME_PATTERN})==(?P<version>{VERSION_PATTERN})(?:\s+\\)?$"
)
HASH_RE = re.compile(r"^--hash=sha256:(?P<digest>[0-9a-f]{64})(?:\s+\\)?$")
DIRECT_NAME_RE = re.compile(rf"^(?P<name>{NAME_PATTERN})(?:\[[^]]+\])?")
CANONICALIZE_RE = re.compile(r"[-_.]+")
REQUIRED_HEADER_TOKENS = (
    "backend/pyproject.toml",
    "--python-version 3.13",
    "--python-platform x86_64-manylinux_2_28",
    "--only-binary :all:",
    "--offline",
    "--no-index",
)


class LockVerificationError(ValueError):
    """Raised when the lock violates the production lock contract."""


@dataclass(frozen=True)
class LockedRequirement:
    name: str
    version: str
    hashes: frozenset[str]


@dataclass(frozen=True)
class LockStats:
    packages: int
    hashes: int
    wheels: int | None = None


def canonicalize(name: str) -> str:
    return CANONICALIZE_RE.sub("-", name).lower()


def _dependency_names(values: object, *, section: str) -> set[str]:
    if not isinstance(values, list) or not all(
        isinstance(item, str) for item in values
    ):
        raise LockVerificationError(f"{section} must be a list of dependency strings")
    names: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise LockVerificationError(
                f"{section} must be a list of dependency strings"
            )
        value = item
        match = DIRECT_NAME_RE.match(value)
        if match is None:
            raise LockVerificationError(
                f"cannot parse dependency in {section}: {value!r}"
            )
        names.add(canonicalize(match.group("name")))
    return names


def project_dependency_names(pyproject_path: Path) -> tuple[set[str], set[str]]:
    with pyproject_path.open("rb") as source:
        document = tomllib.load(source)
    project = document.get("project")
    if not isinstance(project, dict):
        raise LockVerificationError("pyproject is missing [project]")
    production = _dependency_names(
        project.get("dependencies"), section="[project].dependencies"
    )
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        raise LockVerificationError("[project.optional-dependencies] must be a table")
    dev_values = optional.get("dev", [])
    dev = _dependency_names(dev_values, section="[project.optional-dependencies].dev")
    return production, dev - production


def parse_lock(lock_path: Path) -> dict[str, LockedRequirement]:
    text = lock_path.read_text(encoding="utf-8")
    header = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    missing_tokens = [token for token in REQUIRED_HEADER_TOKENS if token not in header]
    if missing_tokens:
        raise LockVerificationError(
            "lock header is missing target/generation tokens: "
            + ", ".join(missing_tokens)
        )

    parsed: dict[str, tuple[str, set[str]]] = {}
    active_name: str | None = None
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        hash_match = HASH_RE.fullmatch(stripped)
        if hash_match is not None:
            if active_name is None:
                raise LockVerificationError(f"line {line_number}: hash has no package")
            parsed[active_name][1].add(hash_match.group("digest"))
            continue
        if stripped.startswith("--"):
            raise LockVerificationError(f"line {line_number}: forbidden lock option")
        requirement_match = REQUIREMENT_RE.fullmatch(stripped)
        if requirement_match is None:
            raise LockVerificationError(
                f"line {line_number}: expected an exact package pin, found {stripped!r}"
            )
        active_name = canonicalize(requirement_match.group("name"))
        if active_name in parsed:
            raise LockVerificationError(
                f"line {line_number}: duplicate package {active_name}"
            )
        parsed[active_name] = (requirement_match.group("version"), set())

    requirements: dict[str, LockedRequirement] = {}
    for name, (version, hashes) in parsed.items():
        if not hashes:
            raise LockVerificationError(f"package {name} has no sha256 hash")
        requirements[name] = LockedRequirement(name, version, frozenset(hashes))
    if not requirements:
        raise LockVerificationError("lock contains no packages")
    return requirements


def verify_wheelhouse(
    requirements: dict[str, LockedRequirement], wheelhouse: Path
) -> int:
    wheel_paths = sorted(wheelhouse.iterdir())
    if any(path.suffix != ".whl" or not path.is_file() for path in wheel_paths):
        raise LockVerificationError("wheelhouse must contain wheel files only")
    seen: set[str] = set()
    for wheel_path in wheel_paths:
        with zipfile.ZipFile(wheel_path) as wheel:
            metadata_paths = [
                name
                for name in wheel.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                raise LockVerificationError(
                    f"{wheel_path.name}: expected exactly one METADATA file"
                )
            metadata = Parser().parsestr(
                wheel.read(metadata_paths[0]).decode("utf-8", "strict")
            )
        metadata_name = metadata["Name"]
        version = metadata["Version"]
        if metadata_name is None or version is None:
            raise LockVerificationError(
                f"{wheel_path.name}: METADATA is missing Name or Version"
            )
        name = canonicalize(metadata_name)
        locked = requirements.get(name)
        if locked is None:
            raise LockVerificationError(f"{wheel_path.name}: package is not in lock")
        if name in seen:
            raise LockVerificationError(f"wheelhouse has duplicate package {name}")
        if version != locked.version:
            raise LockVerificationError(
                f"{wheel_path.name}: version {version} != locked {locked.version}"
            )
        with wheel_path.open("rb") as wheel_file:
            digest = hashlib.file_digest(wheel_file, "sha256").hexdigest()
        if digest not in locked.hashes:
            raise LockVerificationError(f"{wheel_path.name}: sha256 is not in lock")
        seen.add(name)
    missing = sorted(set(requirements) - seen)
    if missing:
        raise LockVerificationError(
            "wheelhouse is missing locked packages: " + ", ".join(missing)
        )
    return len(wheel_paths)


def verify_lock(
    pyproject_path: Path, lock_path: Path, wheelhouse: Path | None = None
) -> LockStats:
    production, dev_only = project_dependency_names(pyproject_path)
    requirements = parse_lock(lock_path)
    missing_direct = sorted(production - set(requirements))
    if missing_direct:
        raise LockVerificationError(
            "lock is missing production dependencies: " + ", ".join(missing_direct)
        )
    included_dev = sorted(dev_only & set(requirements))
    if included_dev:
        raise LockVerificationError(
            "lock includes dev-only dependencies: " + ", ".join(included_dev)
        )
    wheel_count = verify_wheelhouse(requirements, wheelhouse) if wheelhouse else None
    return LockStats(
        packages=len(requirements),
        hashes=sum(len(item.hashes) for item in requirements.values()),
        wheels=wheel_count,
    )


def main() -> int:
    backend_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pyproject", type=Path, default=backend_root / "pyproject.toml"
    )
    parser.add_argument("--lock", type=Path, default=backend_root / "requirements.lock")
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    try:
        stats = verify_lock(args.pyproject, args.lock, args.wheelhouse)
    except (
        LockVerificationError,
        OSError,
        tomllib.TOMLDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"requirements lock verification failed: {exc}", file=sys.stderr)
        return 1
    wheel_text = f", wheels={stats.wheels}" if stats.wheels is not None else ""
    print(f"verified packages={stats.packages}, hashes={stats.hashes}{wheel_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
