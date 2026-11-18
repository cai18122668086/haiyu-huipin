"""Package the tested security update over the current frontend sources."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "huipin-online-support/frontend:1953f622abd4431985e55c37c238ece8b67f8bc4"
NODE = "node@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def digest(p):
    data = p.read_bytes()
    if p.suffix != ".tgz":
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def main():
    assert not git("status", "--porcelain"), "Commit reviewed sources first"
    assert git("remote", "get-url", "origin") == "https://github.com/cai18122668086/haiyu-huipin.git"
    revision = git("rev-parse", "HEAD")
    code = "import subprocess,json; c=json.loads(subprocess.check_output(['docker','inspect','huipin-prelaunch-frontend-1']))[0]; print(json.dumps({'id':c['Id'],'image_id':c['Image'],'image':c['Config']['Image']}))"
    baseline = json.loads(subprocess.check_output(["ssh", "haiyu-website", "python3", "-"], input=code.encode()))
    assert baseline["image"] == BASE
    app = git("ls-files", "app", "components", "features", "lib", "next.config.ts").splitlines()
    copied = git("ls-files", "build", "vendor", "tests", "package.json", "package-lock.json", "eslint.config.mjs").splitlines()
    smoke = ["/images/cases/olay-20261030/replica-01.png", "/images/cases/olay-20261030/replica-07.png",
             "/images/cases/olay-20261030/product-01.webp", "/images/cases/platform-v3/background-result.png",
             "/images/cases/platform-v3/color-result.png", "/images/cases/platform-v2/try-on-result.png"]
    manifest = {"revision": revision, "baseline": baseline,
                "sources": {p: digest(ROOT / p) for p in [*app, *copied]},
                "unchanged_app_sources": {p: digest(ROOT / p) for p in app},
                "public_smoke_assets": {p: digest(ROOT / "public" / p.lstrip("/")) for p in smoke},
                "node_base": NODE}
    # Fresh runtime layers contain the current dependency tree only. Previous
    # source/assets are retained, while npm ci replaces every installed package.
    docker = f"""FROM {BASE} AS previous
FROM {NODE} AS build
WORKDIR /app
COPY --from=previous /app /app
COPY package.json package-lock.json eslint.config.mjs ./
COPY build build
COPY vendor vendor
COPY tests tests
RUN npm ci --ignore-scripts --no-audit --fund=false
RUN npm run audit:security
RUN cp -a /app/dist/client/_polaris-static /tmp/previous-static && npm run build && cp -an /tmp/previous-static/. /app/dist/client/_polaris-static/
FROM {NODE} AS runtime
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build --chown=1000:1000 /app /app
USER 1000:1000
EXPOSE 3000
CMD ["npm","run","start","--","--hostname","0.0.0.0","--port","3000"]
"""
    output = ROOT / "release-package" / ("frontend-security-20261118-" + revision[:12])
    output.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for p in copied:
            archive.add(ROOT / p, arcname=p)
        archive.add(Path(__file__).with_name("release.py"), arcname="release.py")
        for name, content in {"frontend.Dockerfile": docker, "release-manifest.json": json.dumps(manifest, indent=2)}.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    print(json.dumps({"revision": revision, "archive": str(output / "source.tar.gz"), "sources": len(manifest["sources"])}))


if __name__ == "__main__":
    main()
