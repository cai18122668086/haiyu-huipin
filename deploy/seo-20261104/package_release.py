"""Package committed SEO sources as a layer over the reviewed live frontend."""
from pathlib import Path
import hashlib
import io
import json
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
BASE_REVISION = "bb9a1d3"
BASE_IMAGE = "huipin-replica-fix/frontend:60ea63cf3fad55237f9169a5b74cbfcfc18a1a74"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def normalized_hash(data):
    return hashlib.sha256(data.decode("utf-8-sig").replace("\r\n", "\n").encode()).hexdigest()


def main():
    assert not git("status", "--porcelain").strip(), "Commit the scoped work before packaging"
    remote = git("remote", "get-url", "origin").decode().strip()
    assert remote == "https://github.com/cai18122668086/haiyu-huipin.git"
    revision = git("rev-parse", "HEAD").decode().strip()
    changed = git("diff", "--name-only", BASE_REVISION, "HEAD").decode().splitlines()
    sources = [name for name in changed if name.startswith(("app/", "components/", "features/", "lib/")) or name == "next.config.ts"]
    assert sources and not any(name.startswith("backend/") for name in changed)
    base_sources = {}
    for name in sources:
        check = subprocess.run(["git", "cat-file", "-e", BASE_REVISION + ":" + name], cwd=ROOT, capture_output=True)
        if check.returncode == 0:
            base_sources[name] = normalized_hash(git("show", BASE_REVISION + ":" + name))
    text = (ROOT / "lib/seo/pages.ts").read_text()
    pages = json.loads(re.search(r"SEO_PAGES = (\[.*?\]) as const;", text, re.S).group(1))
    manifest = {"revision": revision, "base_revision": BASE_REVISION, "base_image": BASE_IMAGE,
                "base_sources": base_sources,
                "sources": {name: normalized_hash((ROOT / name).read_bytes()) for name in sources},
                "pages": pages}
    out = ROOT / "release-package" / ("seo-20261104-" + revision[:12])
    out.mkdir(parents=True, exist_ok=True)
    dockerfile = (
        "FROM " + BASE_IMAGE + "\nUSER root\nCOPY --chown=1000:1000 source/ /app/\nUSER 1000:1000\n"
        "RUN cp -a /app/dist/client/_polaris-static /tmp/previous-static && npm run build && "
        "cp -an /tmp/previous-static/. /app/dist/client/_polaris-static/\n"
    )
    with tarfile.open(out / "source.tar.gz", "w:gz") as archive:
        for name in sources:
            archive.add(ROOT / name, arcname="source/" + name)
        for name in ["release.py", "caddy_policy.py", "runtime_policy.py"]:
            archive.add(Path(__file__).parent / name, arcname=name)
        for name, value in [(".dockerignore", "**\n!source/\n!source/**\n!frontend.Dockerfile\n"), ("frontend.Dockerfile", dockerfile), ("release-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))]:
            data = value.encode()
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    (out / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"archive": str(out / "source.tar.gz"), "revision": revision,
                      "runtime_source_files": len(sources), "existing_base_files": len(base_sources),
                      "bytes": (out / "source.tar.gz").stat().st_size}))


if __name__ == "__main__":
    main()
