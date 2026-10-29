"""Package reviewed sources only; exclude credentials and local runtime data."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "release-package/everyinfra-20261029"
BASE = "origin/feat/wechat-query-20261029"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().splitlines()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    changed = git("diff", "--name-only", BASE, "HEAD", "--", "backend/app")
    frontend_dirs = [
        "app",
        "build",
        "components",
        "db",
        "drizzle",
        "features",
        "lib",
        "public",
        "worker",
    ]
    frontend = git(
        "ls-files",
        "--",
        *frontend_dirs,
        "package.json",
        "package-lock.json",
        "drizzle.config.ts",
        "eslint.config.mjs",
        "next.config.ts",
        "postcss.config.mjs",
        "tsconfig.json",
        "vite.config.ts",
    )
    assert not any(Path(p).name.startswith(".env") for p in frontend)
    manifest = {
        "revision": git("rev-parse", "HEAD")[0],
        "backend_sources": {
            p.relative_to(ROOT / "backend").as_posix(): hashlib.sha256(
                p.read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for p in (ROOT / "backend/app").rglob("*.py")
        },
        "backend_changed": [p.removeprefix("backend/") for p in changed],
        "frontend_sources": {
            p: hashlib.sha256(
                (ROOT / p).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for p in frontend
        },
    }
    backend = ["FROM huipin-membership/backend:20261029-query", "USER root"]
    backend.extend("COPY " + p + " /app/" + p.removeprefix("backend/") for p in changed)
    backend.extend(["COPY backend/tests /app/tests", "USER 10001:10001"])
    frontend_docker = [
        "FROM huipin-membership/frontend:20261029-query",
        "USER root",
        "COPY --chown=1000:1000 frontend/ /app/",
        "USER 1000:1000",
        "RUN npm ci --include=dev --ignore-scripts --no-audit --fund=false",
        "RUN npm run build",
    ]
    with tarfile.open(OUTPUT / "source.tar.gz", "w:gz") as archive:
        for name in changed + git("ls-files", "backend/tests"):
            archive.add(ROOT / name, arcname=name)
        for name in frontend:
            archive.add(ROOT / name, arcname="frontend/" + name)
        archive.add(Path(__file__).with_name("release.py"), arcname="release.py")
        for name, content in [
            ("backend.Dockerfile", "\n".join(backend) + "\n"),
            ("frontend.Dockerfile", "\n".join(frontend_docker) + "\n"),
            ("release-manifest.json", json.dumps(manifest, indent=2)),
        ]:
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    print(
        json.dumps(
            {
                "archive": str(OUTPUT / "source.tar.gz"),
                "bytes": (OUTPUT / "source.tar.gz").stat().st_size,
                "backend_changed": changed,
                "frontend_files": len(frontend),
            }
        )
    )


if __name__ == "__main__":
    main()
