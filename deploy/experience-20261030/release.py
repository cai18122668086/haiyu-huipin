"""Publish approved membership, pricing and unified creation UI with backup and compatible rollback."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

import release_base as runner
import yaml
from test_candidate import run as test_candidate

runner.ROOT = Path(__file__).resolve().parent
manifest = json.loads((runner.ROOT / "release-manifest.json").read_text())
runner.BASE_BACKEND = manifest["baseline"]["image"]
runner.BASE_FRONTEND = manifest["baseline"]["frontend_image"]
runner.BACKEND = "huipin-experience/backend:" + manifest["revision"]
runner.FRONTEND = "huipin-experience/frontend:" + manifest["revision"]

STATUS = '''import json
from app.config import Settings
from sqlalchemy import create_engine,text
with create_engine(Settings().database_url).connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 print(json.dumps({'jobs':c.scalar(text("select count(*) from jobs where status in ('queued','planning','processing','cancelling')")), 'replicas':c.scalar(text("select count(*) from replica_documents where status in ('fetching','analysing')")), 'migration':c.scalar(text('select version_num from alembic_version')), 'plan_schema_length':c.scalar(text("select character_maximum_length from information_schema.columns where table_schema=current_schema() and table_name='job_plans' and column_name='schema_version'"))}))
'''


def verify_assets(image=None):
    expected = manifest["fast_assets"]
    code = '''import hashlib,json
from pathlib import Path
expected=EXPECTED
from app.new_detail_skill_provider import BundledSkillManifestValidator
from app.replica_fast import SKILLS,CONCURRENCY
for skill in [*SKILLS.values(), 'aurora-domestic-ecommerce-main-image-native-fast', 'xinghe-ecommerce-detail-domestic-4-fast', 'xinghe-amazon-visual-suite-fast']:
 BundledSkillManifestValidator().validate(skill)
assert CONCURRENCY==20
print(json.dumps({p:hashlib.sha256((Path('/app')/p).read_bytes().replace(bytes([13,10]),bytes([10]))).hexdigest() for p in expected}))
'''.replace("expected=EXPECTED", "expected=" + repr(expected))
    assert runner.inner(code, image) == expected


def build():
    runner.build()
    verify_assets(runner.BACKEND)


def activate():
    build_result = json.loads((runner.ROOT / "build-result.json").read_text())
    tests = json.loads((runner.ROOT / "test-result.json").read_text())
    assert tests["exit_code"] == 0
    assert tests["image_id"] == build_result["backend_id"] == runner.inspect(runner.BACKEND)["Id"]
    assert build_result["frontend_id"] == runner.inspect(runner.FRONTEND)["Id"]
    expected_before = {"jobs": 0, "replicas": 0, "migration": "20261030_0035", "plan_schema_length": 64}
    assert runner.inner(STATUS) == expected_before, "Production is busy or baseline changed"
    verify_assets(runner.BACKEND)
    before = {s: runner.inspect("huipin-prelaunch-" + s + "-1") for s in runner.SERVICES}
    for service, item in before.items():
        assert item["Config"]["Image"] == (runner.BASE_FRONTEND if service == "frontend" else runner.BASE_BACKEND)
    names = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines()
    protected = {n: runner.inspect(n)["Id"] for n in names
                 if n not in {"huipin-prelaunch-" + s + "-1" for s in runner.SERVICES}}
    hashes = {str(p): runner.digest(p) for p in runner.PROTECTED_FILES}
    original = runner.OVERRIDE.read_bytes()
    for name, content in (("billing-before.private.yml", original),
                          ("containers-before.private.json", json.dumps(before).encode())):
        path = runner.ROOT / name
        assert not path.exists(), "Never overwrite a rollback snapshot"
        path.write_bytes(content)
        path.chmod(0o600)
    uri = urlsplit(runner.environment(before["api"])["POLARIS_DATABASE_URL"])
    dump = runner.ROOT / "before-experience.dump"
    assert not dump.exists()
    with dump.open("wb") as output:
        subprocess.run(["docker", "exec", "huipin-prelaunch-postgres-1", "pg_dump",
                        "-U", unquote(uri.username or "polaris"), "-d", unquote(uri.path.lstrip("/")),
                        "--format=custom"], stdout=output, check=True)
    dump.chmod(0o600)
    subprocess.run(["docker", "exec", "-i", "huipin-prelaunch-postgres-1", "pg_restore", "--list"],
                   input=dump.read_bytes(), stdout=subprocess.DEVNULL, check=True)
    updated = yaml.safe_load(original)
    for s in ("api", "worker", "dispatcher", "migrate"):
        updated["services"][s]["image"] = runner.BACKEND
    updated["services"]["frontend"]["image"] = runner.FRONTEND
    compose = ["docker", "compose", "--project-name", "huipin-prelaunch", "--env-file",
               "/etc/huipin-prelaunch/prelaunch.env", "-f", str(runner.RUNTIME / "compose.yml"),
               "-f", str(runner.OVERRIDE)]

    def write_config(content):
        temporary = runner.OVERRIDE.with_suffix(".pending.yml")
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        os.replace(temporary, runner.OVERRIDE)

    write_config(yaml.safe_dump(updated, sort_keys=False).encode())
    try:
        merged = json.loads(subprocess.check_output(compose + ["config", "--format", "json"], cwd=runner.RUNTIME))
        for s, previous in before.items():
            current = merged["services"][s]
            for key, value in runner.environment(previous).items():
                if key.startswith(("POLARIS_", "NEXT_PUBLIC_", "VITE_")):
                    assert str(current.get("environment", {}).get(key, "")) == value, (s, key)
            command = current.get("command")
            if command is None:
                command = runner.inspect(current["image"])["Config"].get("Cmd")
            assert command == previous["Config"].get("Cmd"), s
        assert runner.inner(STATUS) == expected_before
        with (runner.ROOT / "migration.log").open("w") as log:
            subprocess.run(compose + ["run", "--rm", "--no-deps", "migrate"], cwd=runner.RUNTIME,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        after_migration = runner.inner(STATUS)
        assert after_migration["migration"] == "20261030_0036" and after_migration["plan_schema_length"] == 64
        assert after_migration["jobs"] == after_migration["replicas"] == 0
        subprocess.run(compose + ["up", "-d", "--no-deps", *runner.SERVICES], cwd=runner.RUNTIME, check=True)
        for _ in range(60):
            states = [runner.inspect("huipin-prelaunch-" + s + "-1")["State"] for s in runner.SERVICES]
            if all(s.get("Health", {}).get("Status") == "healthy" for s in states):
                break
            assert all(s["Running"] for s in states)
            time.sleep(2)
        else:
            raise RuntimeError("Candidate services did not become healthy")
        assert runner.inner(runner.SOURCE_PROBE) == manifest["backend_sources"]
        verify_assets()
        subprocess.run([sys.executable, str(runner.ROOT / "runtime_probe.py")], check=True)
        for path in ("/account", "/style-replica", "/workspace/domestic-main-image", "/workspace/detail-pack", "/workspace/amazon-visual-suite"):
            with urlopen("https://huipin.haiyushuke.com" + path, timeout=30) as response:
                assert response.status == 200
        assert {n: runner.inspect(n)["Id"] for n in protected} == protected
        assert {str(p): runner.digest(p) for p in runner.PROTECTED_FILES} == hashes
        for s in runner.SERVICES:
            after = runner.inspect("huipin-prelaunch-" + s + "-1")
            assert after["Config"]["Cmd"] == before[s]["Config"]["Cmd"]
            assert runner.environment(after) == runner.environment(before[s]), s
            assert after["HostConfig"].get("Binds") == before[s]["HostConfig"].get("Binds"), s
            for field in ("Memory", "NanoCpus", "PidsLimit", "Ulimits"):
                assert after["HostConfig"].get(field) == before[s]["HostConfig"].get(field)
    except BaseException:
        write_config(original)
        subprocess.run(compose + ["up", "-d", "--no-deps", *runner.SERVICES], cwd=runner.RUNTIME, check=True)
        runner.write_json("activation.json", {"status": "rolled_back", "database_downgraded": False})
        raise
    report = {"status": "activated", "revision": manifest["revision"],
              "backend_image": runner.BACKEND, "frontend_image": runner.FRONTEND,
              "migration": "20261030_0036", "plan_schema_length": 64,
              "backup_sha256": runner.digest(dump), "backup_bytes": dump.stat().st_size,
              "backup_catalog_verified": True, "payments_queues_capacity_preserved": True,
              "other_services_preserved": True, "customer_task_restarted": False}
    runner.write_json("activation.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    action = argparse.ArgumentParser()
    action.add_argument("action", choices=("build", "test", "activate"))
    {"build": build, "test": lambda: test_candidate(runner), "activate": activate}[action.parse_args().action]()
