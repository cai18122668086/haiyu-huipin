"""Stage, verify and release only the Huipin SEO frontend and scoped CSP."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

import yaml
from caddy_policy import scope_worker_bridge

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "release-manifest.json").read_text())
FRONTEND = "huipin-prelaunch-frontend-1"
CADDY = "huipin-prelaunch-caddy-1"
RUNTIME = Path("/srv/huipin-prelaunch/runtime")
OVERRIDE = Path("/srv/huipin-membership-20261028/billing.override.yml")
CADDY_FILE = RUNTIME / "Caddyfile.prelaunch"
COMPOSE = ["docker", "compose", "--project-name", "huipin-prelaunch",
           "--env-file", "/etc/huipin-prelaunch/prelaunch.env",
           "-f", str(RUNTIME / "compose.yml"), "-f", str(OVERRIDE)]
IMAGE = "huipin-seo/frontend:" + MANIFEST["revision"]


def command(args, **kwargs):
    return subprocess.check_output(args, **kwargs)


def inspect(name):
    return json.loads(command(["docker", "inspect", name]))[0]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(name, data, private=False):
    path = ROOT / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    if private:
        path.chmod(0o600)


def baseline():
    return json.loads((ROOT / "baseline.private.json").read_text())


def verify_baseline():
    saved = baseline()
    current = inspect(FRONTEND)
    assert current["Id"] == saved["frontend"]["Id"], "Frontend changed during preparation"
    assert digest(CADDY_FILE) == saved["caddy_sha256"], "Caddy policy changed during preparation"
    assert digest(OVERRIDE) == saved["override_sha256"], "Compose override changed during preparation"
    return current


def verify_sources(container, expected):
    script = """const fs=require('fs'),c=require('crypto');let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{const m=JSON.parse(s);for(const [p,h] of Object.entries(m)){const b=fs.readFileSync('/app/'+p);const n=b.toString('utf8').replace(/\\r\\n/g,'\\n').replace(/^\\uFEFF/,'');if(c.createHash('sha256').update(n).digest('hex')!==h)throw new Error('Source mismatch: '+p)}console.log(JSON.stringify({matched_files:Object.keys(m).length}))})"""
    args = ["docker", "exec", "-i", container, "node", "-e", script]
    return json.loads(command(args, input=json.dumps(expected).encode()))


def capture():
    assert not (ROOT / "baseline.private.json").exists(), "Baseline already captured"
    current = inspect(FRONTEND)
    labels = current["Config"]["Labels"]
    assert labels["com.docker.compose.project"] == "huipin-prelaunch"
    assert labels["com.docker.compose.project.working_dir"] == str(RUNTIME)
    assert current["Config"]["Image"] == MANIFEST["base_image"], "Reviewed image changed"
    verify_sources(FRONTEND, MANIFEST["base_sources"])
    saved = {"frontend": current, "caddy_sha256": digest(CADDY_FILE),
             "override_sha256": digest(OVERRIDE)}
    write_json("baseline.private.json", saved, private=True)
    (ROOT / "caddy-before.private").write_bytes(CADDY_FILE.read_bytes())
    (ROOT / "caddy-before.private").chmod(0o600)
    (ROOT / "billing-before.private.yml").write_bytes(OVERRIDE.read_bytes())
    (ROOT / "billing-before.private.yml").chmod(0o600)
    staged = ROOT / "Caddyfile.seo-candidate"
    staged.write_text(scope_worker_bridge(CADDY_FILE.read_text()))
    command(["docker", "cp", str(staged), CADDY + ":/tmp/huipin-seo-candidate"])
    subprocess.run(["docker", "exec", CADDY, "caddy", "validate", "--config",
                    "/tmp/huipin-seo-candidate", "--adapter", "caddyfile"], check=True)
    print(json.dumps({"status": "baseline_captured", "base_image": MANIFEST["base_image"],
                      "base_source_files_verified": len(MANIFEST["base_sources"]),
                      "caddy_candidate_validated": True}))


def build():
    verify_baseline()
    with (ROOT / "build.log").open("w") as log:
        subprocess.run(["docker", "build", "--network", "host", "-f",
                        str(ROOT / "frontend.Dockerfile"), "-t", IMAGE, str(ROOT)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    write_json("build-result.json", {"image": IMAGE, "image_id": inspect(IMAGE)["Id"]})
    print(json.dumps({"status": "built", "image": IMAGE}))


def http_check(container):
    script = """
const fs=require('fs');
(async()=>{
 const m=JSON.parse(fs.readFileSync('/tmp/seo-manifest.json','utf8'));
 for(const p of m.pages){
  const r=await fetch('http://127.0.0.1:3000'+p.path);const s=await r.text();
  if(r.status!==200||!s.includes('<title>'+p.title+'</title>')||!s.includes('name="description" content="'+p.description+'"')||!s.includes('rel="canonical" href="https://huipin.haiyushuke.com'+p.path+'"'))throw new Error('Metadata mismatch: '+p.path);
  if(p.tool&&(!s.includes('data-public-tool=')||s.includes('正在确认登录状态')))throw new Error('Tool intro missing: '+p.path);
 }
 for(const p of ['/robots.txt','/sitemap.xml']){
  const r=await fetch('http://127.0.0.1:3000'+p);const s=await r.text();
  if(r.status!==200)throw new Error('Missing '+p);
  if(p.endsWith('.xml')&&(s.match(/<loc>/g)||[]).length!==20)throw new Error('Sitemap URL count');
 }
 for(const p of ['/account','/jobs','/assets','/auth/login']){
  const r=await fetch('http://127.0.0.1:3000'+p);const s=await r.text();
  if(!s.includes('noindex')||!(r.headers.get('cache-control')||'').includes('no-store'))throw new Error('Private page policy: '+p);
 }
 for(const p of ['/','/tools','/inspiration','/style-replica']){
  let cache='';
  for(let i=0;i<4;i++){const r=await fetch('http://127.0.0.1:3000'+p);await r.text();cache=r.headers.get('cache-control')||'';if(cache.includes('s-maxage=86400'))break;}
  if(!cache.includes('s-maxage=86400')||!cache.includes('stale-while-revalidate=3600'))throw new Error('Public cache policy: '+p);
 }
 console.log(JSON.stringify({public_pages:20,public_tools:16,private_pages:4,crawl_files:2,cache_checks:4}));
})().catch(e=>{console.error(e.message);process.exit(1)})
"""
    command(["docker", "cp", str(ROOT / "release-manifest.json"), container + ":/tmp/seo-manifest.json"])
    return json.loads(command(["docker", "exec", container, "node", "-e", script]))


def test():
    before = verify_baseline()
    built = json.loads((ROOT / "build-result.json").read_text())
    assert built["image_id"] == inspect(IMAGE)["Id"]
    candidate = "huipin-seo-candidate-" + MANIFEST["revision"][:12]
    env = ROOT / "candidate.private.env"
    env.write_text("\n".join(before["Config"]["Env"]) + "\n")
    env.chmod(0o600)
    network = next(iter(before["NetworkSettings"]["Networks"]))
    subprocess.run(["docker", "run", "-d", "--name", candidate, "--network", network,
                    "--env-file", str(env), IMAGE], check=True, stdout=subprocess.DEVNULL)
    try:
        for _ in range(40):
            state = inspect(candidate)["State"]
            assert state["Running"], "Candidate did not start"
            if state.get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(1)
        proof = verify_sources(candidate, MANIFEST["sources"])
        checks = http_check(candidate)
        result = {"status": "passed", "image_id": built["image_id"], **proof, **checks}
        write_json("test-result.json", result)
        print(json.dumps(result))
    finally:
        subprocess.run(["docker", "rm", "-f", candidate], check=True, stdout=subprocess.DEVNULL)
        env.unlink(missing_ok=True)


def write_in_place(path, data):
    # The Caddyfile is a bind mount: keep its inode stable for caddy reload.
    with Path(path).open("wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def reload_caddy():
    subprocess.run(["docker", "exec", CADDY, "caddy", "reload", "--config",
                    "/etc/caddy/Caddyfile", "--adapter", "caddyfile"], check=True)


def activate():
    lock = (ROOT / "activation.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = verify_baseline()
    built = json.loads((ROOT / "build-result.json").read_text())
    tested = json.loads((ROOT / "test-result.json").read_text())
    assert tested["status"] == "passed" and tested["image_id"] == built["image_id"] == inspect(IMAGE)["Id"]
    names = command(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines()
    others = {name: inspect(name)["Id"] for name in names if name != FRONTEND}
    old_config = json.loads(command(COMPOSE + ["config", "--format", "json"], cwd=RUNTIME))
    original = OVERRIDE.read_bytes()
    original_caddy = CADDY_FILE.read_bytes()
    updated = yaml.safe_load(original)
    updated["services"]["frontend"]["image"] = IMAGE
    try:
        write_in_place(OVERRIDE, yaml.safe_dump(updated, sort_keys=False).encode())
        current = json.loads(command(COMPOSE + ["config", "--format", "json"], cwd=RUNTIME))
        expected = json.loads(json.dumps(old_config))
        expected["services"]["frontend"]["image"] = IMAGE
        assert current == expected, "Effective Compose change must be frontend.image only"
        subprocess.run(COMPOSE + ["up", "-d", "--no-deps", "frontend"], cwd=RUNTIME, check=True)
        for _ in range(60):
            after = inspect(FRONTEND)
            assert after["State"]["Running"]
            if after["State"].get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Frontend did not become healthy")
        assert after["Image"] == built["image_id"]
        for key in ["Env", "Cmd", "Entrypoint", "WorkingDir"]:
            assert after["Config"].get(key) == before["Config"].get(key), key
        for key in ["Memory", "NanoCpus", "PidsLimit", "Ulimits", "PortBindings"]:
            assert after["HostConfig"].get(key) == before["HostConfig"].get(key), key
        assert sorted(after["HostConfig"].get("Binds") or []) == sorted(before["HostConfig"].get("Binds") or [])
        checks = http_check(FRONTEND)
        proof = verify_sources(FRONTEND, MANIFEST["sources"])
        assert digest(CADDY_FILE) == baseline()["caddy_sha256"]
        write_in_place(CADDY_FILE, (ROOT / "Caddyfile.seo-candidate").read_bytes())
        reload_caddy()
        for path in ["/", "/style-replica", "/admin/accounts"]:
            with urlopen("https://huipin.haiyushuke.com" + path, timeout=30) as response:
                policy = response.headers.get("Content-Security-Policy", "")
                assert ("localhost:8765" in policy) == (path == "/admin/accounts"), path
                assert ("127.0.0.1:8765" in policy) == (path == "/admin/accounts"), path
                response.read()
        assert {name: inspect(name)["Id"] for name in others} == others, "Other containers changed"
    except BaseException:
        write_in_place(OVERRIDE, original)
        write_in_place(CADDY_FILE, original_caddy)
        reload_caddy()
        subprocess.run(COMPOSE + ["up", "-d", "--no-deps", "frontend"], cwd=RUNTIME, check=True)
        write_json("activation.json", {"status": "rolled_back", "backend_restarted": False})
        raise
    result = {"status": "activated", "revision": MANIFEST["revision"], "image": IMAGE,
              "previous_image": before["Config"]["Image"], "backend_restarted": False,
              "database_changed": False, "other_containers_preserved": True,
              "runtime_environment_preserved": True, "csp_scoped_to_admin_accounts": True,
              **checks, **proof}
    write_json("activation.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["capture", "build", "test", "activate"])
    action = parser.parse_args().action
    {"capture": capture, "build": build, "test": test, "activate": activate}[action]()
