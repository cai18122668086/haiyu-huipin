"""Package committed SEO sources as a layer over the reviewed live frontend."""
from pathlib import Path
import argparse
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


def normalized_hash(data, name):
    if Path(name).suffix.lower() in {".webp", ".png", ".jpg", ".jpeg", ".avif"}:
        return hashlib.sha256(data).hexdigest()
    return hashlib.sha256(data.decode("utf-8-sig").replace("\r\n", "\n").encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-revision", default=BASE_REVISION)
    parser.add_argument("--base-image", default=BASE_IMAGE)
    parser.add_argument("--preserve-caddy", action="store_true", help="Use the reviewed installed CSP unchanged")
    args = parser.parse_args()
    base_revision, base_image = args.base_revision, args.base_image
    assert not git("status", "--porcelain").strip(), "Commit the scoped work before packaging"
    remote = git("remote", "get-url", "origin").decode().strip()
    assert remote == "https://github.com/cai18122668086/haiyu-huipin.git"
    revision = git("rev-parse", "HEAD").decode().strip()
    changed = git("diff", "--name-only", base_revision, "HEAD").decode().splitlines()
    sources = [name for name in changed if name.startswith(("app/", "components/", "features/", "lib/", "public/images/seo-tool-examples/")) or name == "next.config.ts"]
    assert sources and not any(name.startswith("backend/") for name in changed)
    base_sources = {}
    for name in sources:
        check = subprocess.run(["git", "cat-file", "-e", base_revision + ":" + name], cwd=ROOT, capture_output=True)
        if check.returncode == 0:
            base_sources[name] = normalized_hash(git("show", base_revision + ":" + name), name)
    text = (ROOT / "lib/seo/pages.ts").read_text(encoding="utf-8")
    pages = json.loads(re.search(r"SEO_PAGES = (\[.*?\]) as const;", text, re.S).group(1))
    illustrations = ROOT / "docs/seo-tool-examples-20261104.json"
    examples = json.loads(illustrations.read_text(encoding="utf-8"))["assets"] if illustrations.exists() else []
    if examples:
        assert len(examples) == 7
        for example in examples:
            assert example["output"].startswith("/images/seo-tool-examples/")
            asset = ROOT / "public" / example["output"].lstrip("/")
            assert normalized_hash(asset.read_bytes(), asset.name) == example["sha256"], example["id"]
    manifest = {"revision": revision, "base_revision": base_revision, "base_image": base_image,
                "base_sources": base_sources,
                "sources": {name: normalized_hash((ROOT / name).read_bytes(), name) for name in sources},
                "examples": examples, "preserve_caddy": args.preserve_caddy,
                "pages": pages}
    out = ROOT / "release-package" / ("seo-20261104-" + revision[:12])
    out.mkdir(parents=True, exist_ok=True)
    dockerfile = (
        "FROM " + base_image + "\nUSER root\nCOPY --chown=1000:1000 source/ /app/\nUSER 1000:1000\n"
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
