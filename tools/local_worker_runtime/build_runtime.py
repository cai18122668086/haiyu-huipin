"""Build a pinned, independent local runtime without contacting a control plane."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_closure(repo, ref):
    pending = ["ecommerce_local_worker", "__init__"]
    sources = {}
    while pending:
        name = pending.pop()
        if name in sources:
            continue
        path = f"backend/app/{name}.py"
        content = subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=repo)
        sources[name] = content
        for node in ast.walk(ast.parse(content)):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module:
                    pending.append(node.module.split(".")[0])
                else:
                    pending.extend(alias.name for alias in node.names)
    return sources


def build(args):
    destination = args.destination.resolve()
    if destination.exists():
        raise RuntimeError("Destination already exists; never overwrite a runtime")
    source_ref = (
        subprocess.check_output(
            ["git", "rev-parse", args.source_ref], cwd=args.source_repo
        )
        .decode()
        .strip()
    )
    sources = source_closure(args.source_repo, source_ref)
    seed_python = args.seed_env / "Scripts/python.exe"
    probe = subprocess.check_output(
        [
            str(seed_python),
            "-B",
            "-c",
            'import sys,json,importlib.metadata;print(json.dumps({"python":sys.version,'
            '"base":sys._base_executable,"packages":[{"name":d.metadata["Name"],'
            '"version":d.version} for d in importlib.metadata.distributions()]}))',
        ]
    )
    environment = json.loads(probe)
    seed_site = args.seed_env / "Lib/site-packages"
    # Refuse unexpected links: hard links would couple later dependency changes.
    for path in seed_site.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise RuntimeError("Seed contains a link; inspect it before copying")
    free = shutil.disk_usage(destination.parent).free
    needed = sum(p.stat().st_size for p in seed_site.rglob("*") if p.is_file())
    if free < needed + 512 * 1024 * 1024:
        raise RuntimeError("Insufficient space for an independent copy plus reserve")
    host_source = Path(__file__).parent
    release_id = (
        "observed-" + source_ref[:8] + "-" + digest(host_source / "worker_host.py")[:8]
    )
    release = destination / "releases" / release_id
    (release / "backend/app").mkdir(parents=True)
    (destination / "bin").mkdir()
    source_rows = []
    for name, content in sorted(sources.items()):
        relative = f"backend/app/{name}.py"
        working = args.source_repo / relative
        if args.preserve_working_bytes:
            raw = working.read_bytes()
            if raw.replace(b"\r\n", b"\n") != content:
                raise RuntimeError(
                    f"Working file differs from pinned Git source: {relative}"
                )
            content = raw
        path = release / relative
        path.write_bytes(content)
        source_rows.append({"path": relative, "sha256": digest(path)})
    shutil.copy2(host_source / "worker_host.py", release / "worker_host.py")
    for name in (
        "run_worker.ps1",
        "control_runtime.py",
        "install_task.ps1",
        "bootstrap_runtime.ps1",
    ):
        shutil.copy2(host_source / name, destination / "bin" / name)
    manifest = {
        "format": 1,
        "release_id": release_id,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "application_source_commit": source_ref,
        "source_working_bytes_preserved": args.preserve_working_bytes,
        "python": environment["python"],
        "python_base": environment["base"],
        "files": source_rows
        + [{"path": "worker_host.py", "sha256": digest(release / "worker_host.py")}],
        "credentials_included": False,
        "profiles_included": False,
    }
    (release / "runtime-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    packages = sorted(
        (
            p
            for p in environment["packages"]
            if p["name"].casefold().replace("_", "-") != "polaris-commerce-backend"
        ),
        key=lambda p: p["name"].casefold(),
    )
    (release / "requirements.lock").write_text(
        "".join(f"{p['name']}=={p['version']}\n" for p in packages), encoding="utf-8"
    )
    subprocess.run(
        [
            environment["base"],
            "-m",
            "venv",
            "--without-pip",
            str(destination / ".venv"),
        ],
        check=True,
    )
    target_site = destination / ".venv/Lib/site-packages"

    def ignored(_directory, names):
        return [
            name
            for name in names
            if name == "__pycache__"
            or name.endswith(".pyc")
            or name.startswith("__editable__.polaris_commerce_backend")
            or name.startswith("__editable___polaris_commerce_backend")
            or name.startswith("polaris_commerce_backend-")
            and name.endswith(".dist-info")
        ]

    shutil.copytree(seed_site, target_site, dirs_exist_ok=True, ignore=ignored)
    for path in target_site.glob("*.pth"):
        text = path.read_text(encoding="utf-8").casefold().replace("\\", "/")
        if (
            str(args.source_repo).casefold().replace("\\", "/") in text
            or "__editable__" in text
        ):
            raise RuntimeError(
                "Copied environment still points at a development source"
            )
    rows = []
    for path in sorted(target_site.rglob("*")):
        if path.is_file():
            relative = path.relative_to(target_site).as_posix()
            seed = seed_site / relative
            sha = digest(path)
            if sha != digest(seed):
                raise RuntimeError(f"Dependency copy changed bytes: {relative}")
            rows.append({"path": relative, "sha256": sha, "bytes": path.stat().st_size})
    (destination / "environment-files.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    (destination / "environment.json").write_text(
        json.dumps(environment | {"packages": packages}, indent=2), encoding="utf-8"
    )
    (destination / "current.json").write_text(
        json.dumps({"release_id": release_id}, indent=2), encoding="utf-8"
    )
    args.portable_bundle.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        args.portable_bundle, "x", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for folder in (destination / "releases", destination / "bin"):
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(destination).as_posix())
        for name in ("current.json", "environment.json", "environment-files.json"):
            archive.write(destination / name, name)
    print(
        json.dumps(
            {
                "runtime": str(destination),
                "release_id": release_id,
                "source_modules": len(sources),
                "dependency_files": len(rows),
                "copied_dependency_bytes": sum(r["bytes"] for r in rows),
                "package_count": len(packages),
                "bundle": str(args.portable_bundle),
                "bundle_sha256": digest(args.portable_bundle),
                "worker_started": False,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--seed-env", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--portable-bundle", type=Path, required=True)
    parser.add_argument("--preserve-working-bytes", action="store_true")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
