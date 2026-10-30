"""Package this scoped release without unrelated local work or credentials."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "release-package/experience-20261030"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().splitlines()


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main():
    assert not git("status", "--porcelain"), "Commit the scoped release first"
    baseline = json.loads(Path(__file__).with_name("production-baseline.json").read_text())
    changed = git("diff", "--name-only", "76ac039", "HEAD")
    backend = [p for p in changed if p.startswith(("backend/app/", "backend/migrations/"))]
    frontend = [p for p in changed if p.startswith(("app/", "components/", "lib/", "features/"))]
    assert "app/page.tsx" not in frontend
    frontend_files = list(baseline["frontend_sources"])
    frontend_files = sorted(set(frontend_files) | set(frontend))
    manifest = {
        "revision": git("rev-parse", "HEAD")[0],
        "baseline": baseline,
        "backend_sources": {p.removeprefix("backend/"): digest(ROOT / p)
                            for p in git("ls-files", "backend/app") if p.endswith(".py")},
        "frontend_sources": {p: digest(ROOT / p) for p in frontend_files},
        "fast_assets": baseline["fast_assets"],
    }
    manifest["backend_changed"] = sorted(
        p for p in manifest["backend_sources"].keys() | baseline["backend_sources"].keys()
        if manifest["backend_sources"].get(p) != baseline["backend_sources"].get(p)
    )
    for name, expected in manifest["fast_assets"].items():
        assert digest(ROOT / "backend" / name) == expected, name
    backend_docker = ["FROM " + baseline["image"], "USER root"]
    backend_docker += [f"COPY {p} /app/{p.removeprefix('backend/')}" for p in backend]
    backend_docker += ["COPY backend/tests /app/tests", "USER 10001:10001"]
    frontend_docker = ["FROM " + baseline["frontend_image"], "USER root"]
    frontend_docker += [f"COPY --chown=1000:1000 {p} /app/{p}" for p in frontend]
    frontend_docker += ["USER 1000:1000", "RUN cp -a /app/dist/client/_polaris-static /tmp/previous-static && npm run build && cp -an /tmp/previous-static/. /app/dist/client/_polaris-static/"]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tarfile.open(OUTPUT / "source.tar.gz", "w:gz") as archive:
        for p in backend + frontend + git("ls-files", "backend/tests"):
            assert not Path(p).name.startswith(".env"), p
            archive.add(ROOT / p, arcname=p)
        archive.add(ROOT / "deploy/replica-crawler/release.py", arcname="release_base.py")
        for name in ("release.py", "test_candidate.py", "runtime_probe.py"):
            archive.add(Path(__file__).with_name(name), arcname=name)
        archive.add(Path(__file__).with_name("probe_public.py"), arcname="probe_public.py")
        for name, value in (("backend.Dockerfile", "\n".join(backend_docker)),
                            ("frontend.Dockerfile", "\n".join(frontend_docker)),
                            ("release-manifest.json", json.dumps(manifest, indent=2))):
            data = value.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    print(json.dumps({"archive": str(OUTPUT / "source.tar.gz"), "revision": manifest["revision"]}))


if __name__ == "__main__":
    main()
