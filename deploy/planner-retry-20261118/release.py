"""Backend-only release with isolated tests, task drain, runtime checks and rollback.

Run from the uploaded private /srv/huipin-planner-retry-20261118-<revision>
directory on haiyu-website. Never copy private baselines off the server.
"""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import yaml

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
IMAGE = MANIFEST["image"]
SERVICES = ["api", "worker", "dispatcher"]
OVERRIDE = Path("/srv/huipin-membership-20261028/billing.override.yml")
RUNTIME = Path("/srv/huipin-prelaunch/runtime")
COMPOSE = ["docker", "compose", "--project-name", "huipin-prelaunch", "--env-file",
           "/etc/huipin-prelaunch/prelaunch.env", "-f", str(RUNTIME / "compose.yml"),
           "-f", str(OVERRIDE)]
PROTECTED = [RUNTIME / "compose.yml", RUNTIME / "Caddyfile.prelaunch",
             Path("/etc/huipin-prelaunch/prelaunch.env"),
             Path("/srv/huipin-membership-20261028/secrets/payments.env")]
SOURCE_PROBE = """import hashlib,json
from pathlib import Path
print(json.dumps({str(p.relative_to('/app')):hashlib.sha256(p.read_bytes().replace(bytes([13,10]),bytes([10]))).hexdigest() for p in Path('/app/app').rglob('*.py')}))
"""
STATE_PROBE = """import json
from app.config import Settings
from sqlalchemy import create_engine,text
with create_engine(Settings().database_url).connect() as c:
 print(json.dumps({'jobs':c.execute(text("select count(*) from jobs where status in ('queued','planning','processing','cancelling')")).scalar(),'replicas':c.execute(text("select count(*) from replica_documents where status in ('fetching','analysing')")).scalar(),'migration':c.execute(text('select version_num from alembic_version')).scalar(),'campaigns':[list(r) for r in c.execute(text('select id, enabled, starts_at::text from promotion_campaigns order by id'))]}))
"""
TESTS = [
    "tests/test_xinghe_single_image_provider.py", "tests/test_haiyu_transport.py",
    "tests/test_haiyu_prompt.py", "tests/test_execution_reliability.py",
    "tests/test_customer_credit_billing.py", "tests/test_new_job_contracts.py",
    "tests/test_replica_unknown_retry.py", "tests/test_credit_payments.py",
    "tests/test_replica_fast.py",
    "tests/test_credit_memberships.py", "tests/test_credit_refunds.py",
]


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def write(name, value):
    p = ROOT / name
    p.write_text(json.dumps(value, indent=2))
    p.chmod(0o600)


def inner(code, container="huipin-prelaunch-api-1"):
    return json.loads(subprocess.check_output(
        ["docker", "exec", "-i", container, "python", "-"], input=code.encode()))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config():
    return json.loads(subprocess.check_output(COMPOSE + ["config", "--format", "json"], cwd=RUNTIME))


def baseline():
    return json.loads((ROOT / "baseline.private.json").read_text())


def unchanged():
    snap = baseline()
    assert digest(OVERRIDE) == snap["override_hash"], "Production config changed"
    assert {str(p): digest(p) for p in PROTECTED} == snap["protected"]
    for name, item in snap["containers"].items():
        assert inspect(name)["Id"] == item["Id"], "Production container changed: " + name
    return snap


def capture():
    os.chmod(ROOT, 0o700)
    assert not (ROOT / "baseline.private.json").exists()
    names = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines()
    containers = {n: inspect(n) for n in names}
    for s in SERVICES:
        item = containers["huipin-prelaunch-" + s + "-1"]
        assert item["Config"]["Image"] == MANIFEST["base_image"]
        assert item["Image"] == inspect(MANIFEST["base_image"])["Id"]
    sources = inner(SOURCE_PROBE)
    changed = sorted(k for k in sources.keys() | MANIFEST["sources"].keys()
                     if sources.get(k) != MANIFEST["sources"].get(k))
    assert changed == [MANIFEST["changed"]], changed
    write("baseline.private.json", {"containers": containers,
          "override_hash": digest(OVERRIDE), "protected": {str(p): digest(p) for p in PROTECTED},
          "state": inner(STATE_PROBE)})
    saved = ROOT / "override-before.private.yml"
    saved.write_bytes(OVERRIDE.read_bytes())
    saved.chmod(0o600)
    print(json.dumps({"status": "captured", "changed": changed, "sources": len(sources)}))


def build():
    unchanged()
    with (ROOT / "build.log").open("w") as log:
        subprocess.run(["docker", "build", "--network", "none", "--pull=false",
                        "--label", "org.opencontainers.image.revision=" + MANIFEST["revision"],
                        "-f", "backend.Dockerfile", "-t", IMAGE, "."],
                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    write("build.json", {"image_id": inspect(IMAGE)["Id"]})
    print(json.dumps({"status": "built", "image": IMAGE}))


def test():
    unchanged()
    name = "huipin-planner-test-" + MANIFEST["revision"][:12]
    subprocess.run(["docker", "run", "-d", "--name", name, "--network", "none",
                    "--memory", "256m", "--tmpfs", "/var/lib/postgresql/data",
                    "-e", "POSTGRES_PASSWORD=synthetic-test-password", "-e",
                    "POSTGRES_DB=planner_test", "postgres:16-alpine"], check=True,
                   stdout=subprocess.DEVNULL)
    try:
        for _ in range(30):
            if subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"], capture_output=True).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Isolated PostgreSQL unavailable")
        command = (
            "PIP_REQUIRE_HASHES=0 python -m pip install --no-index --find-links=/testwheels --target=/tmp/testdeps pytest >/tmp/install.log 2>&1 && "
            "mkdir -p /tmp/candidate/public && cp -R /app /tmp/candidate/backend && cp /app/test-fixtures/og.png /tmp/candidate/public/og.png && "
            "cd /tmp/candidate/backend && PYTHONPATH=/tmp/testdeps:/tmp/candidate/backend "
            "POLARIS_TEST_DATABASE_URL=postgresql+psycopg://postgres:synthetic-test-password@127.0.0.1:5432/planner_test "
            "python -m pytest -o addopts='' -q --tb=short --maxfail=1 " + " ".join(TESTS))
        with (ROOT / "candidate-test.log").open("w") as log:
            result = subprocess.run(["docker", "run", "--rm", "--network", "container:" + name,
                                     "--memory", "2g", "--cpus", "2", "-v",
                                     "/srv/huipin-membership-20261028/release:/testwheels:ro",
                                     "--entrypoint", "sh", IMAGE, "-c", command],
                                    stdout=log, stderr=subprocess.STDOUT)
        write("test.json", {"exit_code": result.returncode, "image_id": inspect(IMAGE)["Id"],
                            "isolated_database": True, "tests": TESTS})
        print((ROOT / "candidate-test.log").read_text()[-4500:])
        assert result.returncode == 0, "Candidate tests failed"
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL)


def write_config(data):
    pending = OVERRIDE.with_suffix(".planner-pending.yml")
    pending.write_bytes(data)
    pending.chmod(0o600)
    os.replace(pending, OVERRIDE)


def wait_healthy():
    for _ in range(90):
        states = [inspect("huipin-prelaunch-" + s + "-1")["State"] for s in SERVICES]
        assert all(s["Running"] for s in states)
        if all(s.get("Health", {}).get("Status") == "healthy" for s in states):
            return
        time.sleep(1)
    raise RuntimeError("Backend health check failed")


def activate():
    with (ROOT / "activation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        snap = unchanged()
        tested = json.loads((ROOT / "test.json").read_text())
        assert tested["exit_code"] == 0
        assert tested["image_id"] == json.loads((ROOT / "build.json").read_text())["image_id"] == inspect(IMAGE)["Id"]
        state = inner(STATE_PROBE)
        assert state["jobs"] == state["replicas"] == 0, "Wait for active customer tasks"
        assert state["migration"] == snap["state"]["migration"]
        assert state["campaigns"] == snap["state"]["campaigns"]
        original = OVERRIDE.read_bytes()
        before_config = config()
        expected_config = json.loads(json.dumps(before_config))
        updated = yaml.safe_load(original)
        for service in [*SERVICES, "migrate"]:
            updated["services"][service]["image"] = IMAGE
            expected_config["services"][service]["image"] = IMAGE
        dump = ROOT / "before-planner-retry.private.dump"
        with dump.open("xb") as output:
            subprocess.run(["docker", "exec", "huipin-prelaunch-postgres-1", "pg_dump",
                            "-U", "polaris", "-d", "huipin_prelaunch", "--format=custom"], stdout=output, check=True)
        dump.chmod(0o600)
        subprocess.run(["docker", "exec", "-i", "huipin-prelaunch-postgres-1", "pg_restore", "--list"],
                       input=dump.read_bytes(), stdout=subprocess.DEVNULL, check=True)
        try:
            # Check again after backup, before changing any service.
            assert inner(STATE_PROBE)["jobs"] == inner(STATE_PROBE)["replicas"] == 0
            write_config(yaml.safe_dump(updated, sort_keys=False).encode())
            assert config() == expected_config, "Non-image runtime drift"
            subprocess.run(COMPOSE + ["up", "-d", "--no-deps", *SERVICES], cwd=RUNTIME, check=True)
            wait_healthy()
            for s in SERVICES:
                name = "huipin-prelaunch-" + s + "-1"
                before, after = snap["containers"][name], inspect(name)
                assert after["Image"] == tested["image_id"]
                assert inner(SOURCE_PROBE, name) == MANIFEST["sources"]
                assert dict(v.split("=", 1) for v in before["Config"]["Env"]) == dict(v.split("=", 1) for v in after["Config"]["Env"])
                for key in ["Cmd", "Entrypoint", "WorkingDir", "User"]:
                    assert before["Config"].get(key) == after["Config"].get(key), (s, key)
                for key in ["Memory", "NanoCpus", "PidsLimit", "Ulimits", "PortBindings"]:
                    assert before["HostConfig"].get(key) == after["HostConfig"].get(key), (s, key)
                assert sorted(before["HostConfig"].get("Binds") or []) == sorted(after["HostConfig"].get("Binds") or [])
            changed_names = {"huipin-prelaunch-" + s + "-1" for s in SERVICES}
            for name, before in snap["containers"].items():
                if name not in changed_names:
                    assert inspect(name)["Id"] == before["Id"], name
            assert {str(p): digest(p) for p in PROTECTED} == snap["protected"]
            final = inner(STATE_PROBE)
            assert final["migration"] == state["migration"] and final["campaigns"] == state["campaigns"]
            for path in ["/", "/account", "/style-replica", "/api/v1/public/promotions/replica-welcome"]:
                with urlopen("https://huipin.haiyushuke.com" + path, timeout=30) as response:
                    assert response.status == 200
                    response.read()
        except BaseException:
            write_config(original)
            subprocess.run(COMPOSE + ["up", "-d", "--no-deps", *SERVICES], cwd=RUNTIME, check=True)
            wait_healthy()
            write("activation.json", {"status": "rolled_back"})
            raise
        write("activation.json", {"status": "activated", "revision": MANIFEST["revision"],
              "image": IMAGE, "source_files": len(MANIFEST["sources"]),
              "backup_bytes": dump.stat().st_size, "backup_sha256": digest(dump),
              "runtime_preserved": True, "schema_and_campaigns_preserved": True,
              "customer_tasks_restarted": False, "frontend_preserved": True})
        print((ROOT / "activation.json").read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["capture", "build", "test", "activate"])
    globals()[parser.parse_args().action]()
