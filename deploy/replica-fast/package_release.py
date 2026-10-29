"""Package tracked fast-replica changes on the current integrated base."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "release-package/replica-fast-20261029"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().splitlines()


def main():
    assert not git("status", "--porcelain", "--untracked-files=no"), (
        "Commit reviewed changes first"
    )
    changed = git("diff", "--name-only", "origin/main", "HEAD")
    backend = [
        p for p in changed if p.startswith(("backend/app/", "backend/skill_sources/"))
    ]
    public_template = "backend/skill_sources/xinghe-crossborder-detail-3/.env.example"
    assert public_template in git("ls-files", "--", public_template)
    assert "IMG_API_KEY=your-api-key" in (ROOT / public_template).read_text()
    backend.append(public_template)
    baseline = json.loads(
        Path(__file__)
        .with_name("production-baseline.json")
        .read_text(encoding="utf-8-sig")
    )
    frontend = [
        p for p in changed if p.startswith(("components/", "lib/", "app/", "features/"))
    ]
    assert backend and frontend
    files = git(
        "ls-files",
        "--",
        "app",
        "build",
        "components",
        "db",
        "drizzle",
        "features",
        "lib",
        "public",
        "worker",
        "package.json",
        "package-lock.json",
        "drizzle.config.ts",
        "eslint.config.mjs",
        "next.config.ts",
        "postcss.config.mjs",
        "tsconfig.json",
        "vite.config.ts",
    )
    manifest = {
        "revision": git("rev-parse", "HEAD")[0],
        "backend_sources": {
            p.removeprefix("backend/"): hashlib.sha256(
                (ROOT / p).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for p in git("ls-files", "backend/app")
            if p.endswith(".py")
        },
        "baseline": baseline,
        "frontend_sources": {
            p: hashlib.sha256(
                (ROOT / p).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for p in files
        },
        "fast_assets": {
            p.removeprefix("backend/"): hashlib.sha256(
                (ROOT / p).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for p in backend
            if not p.endswith(".py") or p.startswith("backend/skill_sources/")
        },
    }
    manifest["backend_changed"] = sorted(
        key
        for key in manifest["backend_sources"].keys()
        | baseline["backend_sources"].keys()
        if manifest["backend_sources"].get(key) != baseline["backend_sources"].get(key)
    )
    backend_docker = ["FROM " + baseline["image"], "USER root"]
    backend_docker += [f"COPY {p} /app/{p.removeprefix('backend/')}" for p in backend]
    backend_docker += ["COPY backend/tests /app/tests", "USER 10001:10001"]
    frontend_docker = ["FROM huipin-integrated/frontend:20261029-preview", "USER root"]
    frontend_docker += [f"COPY --chown=1000:1000 {p} /app/{p}" for p in frontend]
    frontend_docker += [
        "USER 1000:1000",
        "RUN cp -a /app/dist/client/_polaris-static /tmp/replica-fast-previous-static && npm run build && cp -an /tmp/replica-fast-previous-static/. /app/dist/client/_polaris-static/",
    ]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tarfile.open(OUTPUT / "source.tar.gz", "w:gz") as archive:
        for p in backend + frontend + git("ls-files", "backend/tests"):
            assert not Path(p).name.startswith(".env") or p == public_template
            archive.add(ROOT / p, arcname=p)
        archive.add(
            ROOT / "deploy/replica-crawler/release.py", arcname="release_base.py"
        )
        archive.add(
            Path(__file__).with_name("release_fast.py"), arcname="release_fast.py"
        )
        for name, value in (
            ("backend.Dockerfile", "\n".join(backend_docker) + "\n"),
            ("frontend.Dockerfile", "\n".join(frontend_docker) + "\n"),
            ("release-manifest.json", json.dumps(manifest, indent=2)),
        ):
            data = value.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    print(
        json.dumps(
            {
                "archive": str(OUTPUT / "source.tar.gz"),
                "revision": manifest["revision"],
                "backend_files": len(backend),
                "frontend_files": len(frontend),
                "bytes": (OUTPUT / "source.tar.gz").stat().st_size,
            }
        )
    )


if __name__ == "__main__":
    main()
