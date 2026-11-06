"""Package the free-trial restart fix over the current production images."""

import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_BACKEND_REV = "5adf9fea248a35486816ba67f0fa67c6be912dea"
BASE_FRONTEND_REV = BASE_BACKEND_REV
BASE_BACKEND = "huipin-provider-failure/backend:" + BASE_BACKEND_REV
BASE_FRONTEND = "huipin-provider-failure/frontend:" + BASE_FRONTEND_REV


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def digest(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    assert not git("status", "--porcelain").strip(), "Commit reviewed sources first"
    assert (
        git("remote", "get-url", "origin").decode().strip()
        == "https://github.com/cai18122668086/haiyu-huipin.git"
    )
    rev = git("rev-parse", "HEAD").decode().strip()
    backend = [
        p
        for p in git("diff", "--name-only", BASE_BACKEND_REV, "HEAD")
        .decode()
        .splitlines()
        if p.startswith(("backend/app/", "backend/migrations/"))
    ]
    frontend = [
        p
        for p in git("diff", "--name-only", BASE_FRONTEND_REV, "HEAD")
        .decode()
        .splitlines()
        if p.startswith(("app/", "components/", "features/", "lib/"))
    ]
    assert backend and frontend
    assert not any(p.startswith("backend/migrations/") for p in backend)
    be = git("ls-files", "backend/app").decode().splitlines()
    fe = (
        git("ls-files", "app", "components", "features", "lib", "next.config.ts")
        .decode()
        .splitlines()
    )

    snapshots = {}

    def sources(paths, base=None, strip=""):
        if base and base not in snapshots:
            with tarfile.open(
                fileobj=io.BytesIO(
                    git(
                        "archive",
                        base,
                        "backend/app",
                        "app",
                        "components",
                        "features",
                        "lib",
                        "next.config.ts",
                    )
                )
            ) as archive:
                snapshots[base] = {
                    entry.name: archive.extractfile(entry).read()
                    for entry in archive.getmembers()
                    if entry.isfile()
                }
        result = {}
        for p in paths:
            if base:
                if p not in snapshots[base]:
                    continue
                data = snapshots[base][p]
            else:
                data = (ROOT / p).read_bytes()
            result[p.removeprefix(strip)] = digest(data)
        return result

    manifest = {
        "revision": rev,
        "base_backend": BASE_BACKEND,
        "base_frontend": BASE_FRONTEND,
        "backend_before": sources(be, BASE_BACKEND_REV, "backend/"),
        "backend_sources": sources(be, strip="backend/"),
        "frontend_before": sources(fe, BASE_FRONTEND_REV),
        "frontend_sources": sources(fe),
        "pages": json.loads(
            re.search(
                r"SEO_PAGES = (\[.*?\]) as const;",
                (ROOT / "lib/seo/pages.ts").read_text(encoding="utf-8"),
                re.DOTALL,
            ).group(1)
        ),
        "examples": json.loads(
            (ROOT / "docs/seo-tool-examples-20261104.json").read_text(encoding="utf-8")
        )["assets"],
    }
    manifest["backend_changed"] = sorted(
        k
        for k in manifest["backend_sources"].keys() | manifest["backend_before"].keys()
        if manifest["backend_sources"].get(k) != manifest["backend_before"].get(k)
    )
    docker_be = ["FROM " + BASE_BACKEND, "USER root"] + [
        f"COPY {p} /app/{p.removeprefix('backend/')}" for p in backend
    ]
    docker_be += ["COPY backend/tests /app/tests", "USER 10001:10001"]
    docker_fe = ["FROM " + BASE_FRONTEND, "USER root"] + [
        f"COPY --chown=1000:1000 {p} /app/{p}" for p in frontend
    ]
    docker_fe += [
        "USER 1000:1000",
        "RUN cp -a /app/dist/client/_polaris-static /tmp/previous-static && npm run build && cp -an /tmp/previous-static/. /app/dist/client/_polaris-static/",
    ]
    output = ROOT / "release-package" / ("free-restart-20261106-" + rev[:12])
    output.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for p in (
            backend + frontend + git("ls-files", "backend/tests").decode().splitlines()
        ):
            assert not Path(p).name.startswith(".env")
            archive.add(ROOT / p, arcname=p)
        for p, name in [
            (ROOT / "deploy/replica-crawler/release.py", "release_base.py"),
            (ROOT / "deploy/seo-20261104/release.py", "seo_check.py"),
            (ROOT / "deploy/seo-20261104/caddy_policy.py", "caddy_policy.py"),
            (ROOT / "deploy/seo-20261104/runtime_policy.py", "runtime_policy.py"),
        ]:
            archive.add(p, arcname=name)
        for name in ["release.py", "test_candidate.py", "activate.py"]:
            archive.add(Path(__file__).with_name(name), arcname=name)
        for name, data in [
            ("backend.Dockerfile", "\n".join(docker_be)),
            ("frontend.Dockerfile", "\n".join(docker_fe)),
            (
                "release-manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            ),
        ]:
            content = data.encode()
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    print(
        json.dumps(
            {
                "archive": str(output / "source.tar.gz"),
                "revision": rev,
                "backend_changes": len(backend),
                "frontend_changes": len(frontend),
            }
        )
    )


if __name__ == "__main__":
    main()
