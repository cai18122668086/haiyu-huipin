"""Newcomer reminder release; preserve schema, runtime and accounting."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import release_base as base
import seo_check
from test_candidate import run as test_candidate

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "release-manifest.json").read_text())
base.SOURCE_PROBE = """import hashlib,json
from pathlib import Path
files = list(Path('/app/app').rglob('*.py'))
files += [Path('/app') / name for name in ASSETS]
print(json.dumps({str(p.relative_to('/app')): hashlib.sha256(p.read_bytes().replace(bytes([13,10]),bytes([10]))).hexdigest() for p in files}))
""".replace(
    "ASSETS", repr([p for p in MANIFEST["backend_sources"] if not p.endswith(".py")])
)
base.ROOT = ROOT
base.BASE_BACKEND = MANIFEST["base_backend"]
base.BASE_FRONTEND = MANIFEST["base_frontend"]
base.BACKEND = BACKEND = "huipin-ci-consolidation/backend:" + MANIFEST["revision"]
base.FRONTEND = FRONTEND = "huipin-ci-consolidation/frontend:" + MANIFEST["revision"]
inspect, inner, write_json = base.inspect, base.inner, base.write_json
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    "huipin-prelaunch",
    "--env-file",
    "/etc/huipin-prelaunch/prelaunch.env",
    "-f",
    str(base.RUNTIME / "compose.yml"),
    "-f",
    str(base.OVERRIDE),
]
PROTECTED = [*base.PROTECTED_FILES, base.RUNTIME / "Caddyfile.prelaunch"]


def verify_frontend(container, expected):
    # Keep UTF-8 BOM bytes, matching both the package and inherited build probe.
    # Only CRLF is normalized; no source content is silently discarded.
    script = r"""const fs=require('fs'),crypto=require('crypto');
let input='';process.stdin.on('data',d=>input+=d);process.stdin.on('end',()=>{
 const expected=JSON.parse(input);
 for(const [name,hash] of Object.entries(expected)){
  const value=fs.readFileSync('/app/'+name).toString('utf8').replace(/\r\n/g,'\n');
  if(crypto.createHash('sha256').update(value).digest('hex')!==hash)throw new Error('Source mismatch: '+name);
 }
 console.log(JSON.stringify({matched_files:Object.keys(expected).length}));
});"""
    proof = json.loads(
        subprocess.check_output(
            ["docker", "exec", "-i", container, "node", "-e", script],
            input=json.dumps(expected).encode(),
        )
    )
    if expected == MANIFEST["frontend_sources"]:
        assets = MANIFEST["public_assets"]
        binary_probe = "const fs=require('fs'),c=require('crypto');let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{for(const [p,h] of Object.entries(JSON.parse(s))){if(c.createHash('sha256').update(fs.readFileSync('/app/'+p)).digest('hex')!==h)throw new Error('Asset mismatch: '+p)}})"
        subprocess.run(["docker", "exec", "-i", container, "node", "-e", binary_probe], input=json.dumps(assets).encode(), check=True)
        proof["matched_public_assets"] = len(assets)
    return proof


seo_check.verify_sources = verify_frontend


def saved():
    return json.loads((ROOT / "baseline.private.json").read_text())


def verify_baseline():
    snapshot = saved()
    for service, previous in snapshot["containers"].items():
        assert inspect("huipin-prelaunch-" + service + "-1")["Id"] == previous["Id"], (
            "Production changed: " + service
        )
    assert {str(p): base.digest(p) for p in PROTECTED} == snapshot["protected"]
    assert base.digest(base.OVERRIDE) == snapshot["override_sha256"]
    assert welcome_campaign() == snapshot["welcome_campaign"], (
        "Existing welcome campaign changed"
    )
    return snapshot


def capture():
    assert not (ROOT / "baseline.private.json").exists(), "Never overwrite a baseline"
    containers = {s: inspect("huipin-prelaunch-" + s + "-1") for s in base.SERVICES}
    for s, item in containers.items():
        assert item["Config"]["Image"] == (
            base.BASE_FRONTEND if s == "frontend" else base.BASE_BACKEND
        )
        assert item["Image"] == inspect(item["Config"]["Image"])["Id"]
    assert inner(base.SOURCE_PROBE) == MANIFEST["backend_before"]
    seo_check.verify_sources("huipin-prelaunch-frontend-1", MANIFEST["frontend_before"])
    state = inner(base.STATUS_PROBE)
    assert state["migration"] == "20261106_0040", state
    snapshot = {
        "containers": containers,
        "protected": {str(p): base.digest(p) for p in PROTECTED},
        "override_sha256": base.digest(base.OVERRIDE),
        "welcome_campaign": welcome_campaign(),
    }
    write_json("baseline.private.json", snapshot)
    (ROOT / "baseline.private.json").chmod(0o600)
    (ROOT / "billing-before.private.yml").write_bytes(base.OVERRIDE.read_bytes())
    (ROOT / "billing-before.private.yml").chmod(0o600)
    print(
        json.dumps(
            {
                "status": "captured",
                "backend_sources": len(MANIFEST["backend_before"]),
                "frontend_sources": len(MANIFEST["frontend_before"]),
                **state,
            }
        )
    )


def build():
    verify_baseline()
    base.build()


def test():
    verify_baseline()
    test_candidate(sys.modules[__name__])
    test_frontend()


def test_frontend():
    verify_baseline()
    item = saved()["containers"]["frontend"]
    name = "huipin-ci-consolidation-frontend-test-" + MANIFEST["revision"][:12]
    env = ROOT / "frontend-candidate.private.env"
    env.write_text("\n".join(item["Config"]["Env"]) + "\n")
    env.chmod(0o600)
    network = next(iter(item["NetworkSettings"]["Networks"]))
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                network,
                "--env-file",
                str(env),
                "--health-cmd",
                "node -e \"fetch('http://127.0.0.1:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))\"",
                "--health-interval",
                "2s",
                "--health-timeout",
                "5s",
                "--health-start-period",
                "5s",
                FRONTEND,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        for _ in range(60):
            state = inspect(name)["State"]
            assert state["Running"]
            if state.get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Frontend candidate not healthy")
        subprocess.run(
            [
                "docker",
                "exec",
                name,
                "sh",
                "-c",
                "node --experimental-strip-types --test tests/newcomer-countdown.test.mjs tests/newcomer-offer-policy.test.mjs tests/replica-progress.test.mjs tests/replica-unknown-retry.test.mjs tests/replica-fast-results.test.mjs",
            ],
            check=True,
            stdout=(ROOT / "frontend-tests.log").open("w"),
            stderr=subprocess.STDOUT,
        )
        proof = seo_check.verify_sources(name, MANIFEST["frontend_sources"])
        write_json(
            "frontend-test-result.json",
            {
                "image_id": inspect(FRONTEND)["Id"],
                "status": "passed",
                **seo_check.http_check(name),
                **proof,
            },
        )
        print(json.dumps({"frontend": "passed", **proof}))
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL
        )
        env.unlink(missing_ok=True)


def write_config(data):
    temporary = base.OVERRIDE.with_suffix(".growth-pending.yml")
    temporary.write_bytes(data)
    temporary.chmod(0o600)
    os.replace(temporary, base.OVERRIDE)


def welcome_campaign():
    return inner("""import json
from app.config import Settings
from sqlalchemy import create_engine,text
with create_engine(Settings().database_url).connect() as c:
 print(json.dumps([dict(row) for row in c.execute(text("select id, enabled, starts_at::text from promotion_campaigns where id in ('replica-welcome-v1','daily-checkin-v1','referral-credits-v1','newcomer-reminder-v1') order by id")).mappings()]))
""")


def campaign(enable):
    return inner(
        """import json
from app.config import Settings
from sqlalchemy import create_engine,text
with create_engine(Settings().database_url).begin() as c:
 c.execute(text(SQL))
 print(json.dumps([dict(row) for row in c.execute(text("select id, enabled, starts_at::text from promotion_campaigns where id in ('newcomer-reminder-v1') order by id")).mappings()]))
""".replace(
            "SQL",
            repr(
                "UPDATE promotion_campaigns SET enabled=true, starts_at=CURRENT_TIMESTAMP WHERE id in ('newcomer-reminder-v1') AND enabled=false"
                if enable
                else "UPDATE promotion_campaigns SET enabled=false WHERE id in ('newcomer-reminder-v1')"
            ),
        )
    )


def activate():
    from activate import run

    run(sys.modules[__name__])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["capture", "build", "test", "test-frontend", "activate"]
    )
    {
        "capture": capture,
        "build": build,
        "test": test,
        "test-frontend": test_frontend,
        "activate": activate,
    }[parser.parse_args().action]()
