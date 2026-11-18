"""Package the planner-only change over the current production backend image."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "huipin-red-gold/backend:31f74afe867348bdf2695e458653193e412c205d"
CHANGED = "backend/app/xinghe_single_image_provider.py"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def main():
    assert not git("status", "--porcelain"), "Commit reviewed sources first"
    assert git("remote", "get-url", "origin") == "https://github.com/cai18122668086/haiyu-huipin.git"
    revision = git("rev-parse", "HEAD")
    paths = git("ls-files", "backend/app").splitlines()
    manifest = {
        "revision": revision,
        "base_image": BASE,
        "image": "huipin-planner-retry/backend:" + revision,
        "changed": CHANGED.removeprefix("backend/"),
        "sources": {
            p.removeprefix("backend/"): hashlib.sha256(
                (ROOT / p).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for p in paths if p.endswith(".py")
        },
    }
    output = ROOT / "release-package" / ("planner-retry-20261118-" + revision[:12])
    output.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for p in [CHANGED, *git("ls-files", "backend/tests").splitlines()]:
            archive.add(ROOT / p, arcname=p)
        archive.add(Path(__file__).with_name("release.py"), arcname="release.py")
        for name, content in {
            "manifest.json": json.dumps(manifest, indent=2),
            "backend.Dockerfile": (
                f"FROM {BASE}\nUSER root\nCOPY {CHANGED} /app/{manifest['changed']}\n"
                "COPY backend/tests /app/tests\nUSER 10001:10001\n"
            ),
        }.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    print(json.dumps({"archive": str(output / "source.tar.gz"), "revision": revision}))


if __name__ == "__main__":
    main()
