"""Additive rewards release; retain compatible credit accounting after activation."""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

import release_base as base
import seo_check
import yaml
from runtime_policy import environment_values
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
base.BACKEND = BACKEND = "huipin-growth/backend:" + MANIFEST["revision"]
base.FRONTEND = FRONTEND = "huipin-growth/frontend:" + MANIFEST["revision"]
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
    return json.loads(
        subprocess.check_output(
            ["docker", "exec", "-i", container, "node", "-e", script],
            input=json.dumps(expected).encode(),
        )
    )


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
    assert state["migration"] == "20261106_0038", state
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
    name = "huipin-growth-frontend-test-" + MANIFEST["revision"][:12]
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
 print(json.dumps(dict(c.execute(text("select enabled, starts_at::text from promotion_campaigns where id='replica-welcome-v1'")).mappings().one())))
""")


def campaign(enable):
    return inner(
        """import json
from app.config import Settings
from sqlalchemy import create_engine,text
with create_engine(Settings().database_url).begin() as c:
 c.execute(text(SQL))
 print(json.dumps([dict(row) for row in c.execute(text("select id, enabled, starts_at::text from promotion_campaigns where id in ('daily-checkin-v1','referral-credits-v1') order by id")).mappings()]))
""".replace(
            "SQL",
            repr(
                "UPDATE promotion_campaigns SET enabled=true, starts_at=CURRENT_TIMESTAMP WHERE id in ('daily-checkin-v1','referral-credits-v1') AND enabled=false"
                if enable
                else "UPDATE promotion_campaigns SET enabled=false WHERE id in ('daily-checkin-v1','referral-credits-v1')"
            ),
        )
    )


def activate():
    lock = (ROOT / "activation.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    snapshot = verify_baseline()
    built = json.loads((ROOT / "build-result.json").read_text())
    tested = json.loads((ROOT / "test-result.json").read_text())
    frontend_test = json.loads((ROOT / "frontend-test-result.json").read_text())
    assert (
        tested["exit_code"] == 0
        and tested["image_id"] == built["backend_id"] == inspect(BACKEND)["Id"]
    )
    assert (
        frontend_test["status"] == "passed"
        and frontend_test["image_id"] == built["frontend_id"] == inspect(FRONTEND)["Id"]
    )
    expected = {"jobs": 0, "replicas": 0, "migration": "20261106_0038"}
    assert inner(base.STATUS_PROBE) == expected, (
        "Production is busy; wait for current tasks"
    )
    before = snapshot["containers"]
    names = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}"], text=True
    ).splitlines()
    others = {
        n: inspect(n)["Id"]
        for n in names
        if n not in {"huipin-prelaunch-" + s + "-1" for s in base.SERVICES}
    }
    original = base.OVERRIDE.read_bytes()
    original_config = json.loads(
        subprocess.check_output(
            COMPOSE + ["config", "--format", "json"], cwd=base.RUNTIME
        )
    )
    uri = urlsplit(base.environment(before["api"])["POLARIS_DATABASE_URL"])
    dump = ROOT / "before-growth.private.dump"
    assert not dump.exists(), "Never overwrite a database backup"
    with dump.open("wb") as output:
        subprocess.run(
            [
                "docker",
                "exec",
                "huipin-prelaunch-postgres-1",
                "pg_dump",
                "-U",
                unquote(uri.username or "polaris"),
                "-d",
                unquote(uri.path.lstrip("/")),
                "--format=custom",
            ],
            stdout=output,
            check=True,
        )
    dump.chmod(0o600)
    subprocess.run(
        ["docker", "exec", "-i", "huipin-prelaunch-postgres-1", "pg_restore", "--list"],
        input=dump.read_bytes(),
        stdout=subprocess.DEVNULL,
        check=True,
    )
    updated = yaml.safe_load(original)
    for s in ["api", "worker", "dispatcher", "migrate"]:
        updated["services"][s]["image"] = BACKEND
    updated["services"]["frontend"]["image"] = FRONTEND
    migrated = False
    campaign_started = False
    try:
        write_config(yaml.safe_dump(updated, sort_keys=False).encode())
        effective = json.loads(
            subprocess.check_output(
                COMPOSE + ["config", "--format", "json"], cwd=base.RUNTIME
            )
        )
        expected_config = json.loads(json.dumps(original_config))
        for s in ["api", "worker", "dispatcher", "migrate"]:
            expected_config["services"][s]["image"] = BACKEND
        expected_config["services"]["frontend"]["image"] = FRONTEND
        assert effective == expected_config, "Only five image references may change"
        assert inner(base.STATUS_PROBE) == expected
        with (ROOT / "migration.log").open("w") as log:
            subprocess.run(
                COMPOSE + ["run", "--rm", "--no-deps", "migrate"],
                cwd=base.RUNTIME,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        migrated = True
        assert inner(base.STATUS_PROBE) == {**expected, "migration": "20261106_0039"}
        assert welcome_campaign() == snapshot["welcome_campaign"]
        subprocess.run(
            COMPOSE + ["up", "-d", "--no-deps", *base.SERVICES],
            cwd=base.RUNTIME,
            check=True,
        )
        for _ in range(90):
            states = [
                inspect("huipin-prelaunch-" + s + "-1")["State"] for s in base.SERVICES
            ]
            if all(s.get("Health", {}).get("Status") == "healthy" for s in states):
                break
            assert all(s["Running"] for s in states)
            time.sleep(1)
        else:
            raise RuntimeError("Rewards services not healthy")
        for service, previous in before.items():
            current = inspect("huipin-prelaunch-" + service + "-1")
            assert environment_values(current["Config"]["Env"]) == environment_values(
                previous["Config"]["Env"]
            )
            for key in ["Cmd", "Entrypoint", "WorkingDir", "User"]:
                assert current["Config"].get(key) == previous["Config"].get(key), (
                    service,
                    key,
                )
            for key in ["Memory", "NanoCpus", "PidsLimit", "Ulimits", "PortBindings"]:
                assert current["HostConfig"].get(key) == previous["HostConfig"].get(
                    key
                ), (service, key)
            assert sorted(current["HostConfig"].get("Binds") or []) == sorted(
                previous["HostConfig"].get("Binds") or []
            )
            if service != "frontend":
                code = ["docker", "exec", "-i", current["Id"], "python", "-"]
                assert (
                    json.loads(
                        subprocess.check_output(code, input=base.SOURCE_PROBE.encode())
                    )
                    == MANIFEST["backend_sources"]
                )
        assert {n: inspect(n)["Id"] for n in others} == others
        assert {str(p): base.digest(p) for p in PROTECTED} == snapshot["protected"]
        proof = seo_check.verify_sources(
            "huipin-prelaunch-frontend-1", MANIFEST["frontend_sources"]
        )
        checks = seo_check.http_check("huipin-prelaunch-frontend-1")
        for path in [
            "/",
            "/style-replica",
            "/account",
            "/api/v1/public/promotions/replica-welcome",
        ]:
            with urlopen(
                "https://huipin.haiyushuke.com" + path, timeout=30
            ) as response:
                assert response.status == 200
                response.read()
        with urlopen(
            "https://huipin.haiyushuke.com/api/v1/public/promotions/replica-welcome",
            timeout=30,
        ) as response:
            assert json.load(response) == {"enabled": True}
        assert welcome_campaign() == snapshot["welcome_campaign"]
        campaign_started = True
        enabled = campaign(True)
        assert len(enabled) == 2 and all(row["enabled"] is True for row in enabled)
    except BaseException:
        if campaign_started:
            campaign(False)
        rollback = yaml.safe_load(original)
        if migrated:
            rollback["services"]["migrate"]["image"] = BACKEND
        if campaign_started:
            # Rewards may already exist. Keep the verified accounting hooks for
            # future grants/refunds; disable the promotion and roll back the UI.
            for service in ["api", "worker", "dispatcher"]:
                rollback["services"][service]["image"] = BACKEND
        write_config(
            yaml.safe_dump(rollback, sort_keys=False).encode() if migrated else original
        )
        subprocess.run(
            COMPOSE + ["up", "-d", "--no-deps", *base.SERVICES],
            cwd=base.RUNTIME,
            check=True,
        )
        write_json(
            "activation.json",
            {
                "status": "rolled_back",
                "additive_migration_retained": migrated,
                "campaign_enabled": False,
                "compatible_accounting_retained": campaign_started,
            },
        )
        raise
    result = {
        "status": "activated",
        "revision": MANIFEST["revision"],
        "backend_image": BACKEND,
        "frontend_image": FRONTEND,
        "migration": "20261106_0039",
        "campaign": enabled,
        "welcome_campaign_preserved": True,
        "backup_bytes": dump.stat().st_size,
        "backup_sha256": base.digest(dump),
        "backup_catalog_verified": True,
        "runtime_environment_preserved": True,
        "other_services_preserved": True,
        "customer_tasks_restarted": False,
        **proof,
        **checks,
    }
    write_json("activation.json", result)
    print(json.dumps(result))


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
