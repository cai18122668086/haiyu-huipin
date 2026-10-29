"""Narrow backend overlay release; unchanged environment, no migrations, rollback."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

import yaml

ROOT = Path("/srv/huipin-segmentation-footer-20261029")
OVERRIDE = Path("/srv/huipin-membership-20261028/billing.override.yml")
BASE = "huipin-integrated/backend:20261029-preview"
IMAGE = "huipin-integrated/backend:20261029-segmentation-v16"
SERVICES = ("api", "worker", "dispatcher")
STATUS = """
import json
from sqlalchemy import create_engine,text
from app.config import Settings
with create_engine(Settings().database_url).connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 print(json.dumps({
  'jobs':c.execute(text("select count(*) from jobs where status in ('queued','planning','processing','cancelling')")).scalar(),
  'analyses':c.execute(text("select count(*) from replica_documents where status='analysing'")).scalar(),
  'migration':c.execute(text('select version_num from alembic_version')).scalar()}))
"""
SOURCES = """
import hashlib,json
from pathlib import Path
print(json.dumps({str(p.relative_to('/app')):hashlib.sha256(p.read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest() for p in Path('/app/app').rglob('*') if p.is_file() and p.suffix in {'.py','.md'} and '__pycache__' not in p.parts}))
"""


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def inner(code, image=None):
    command = (
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--entrypoint",
            "python",
            image,
            "-",
        ]
        if image
        else ["docker", "exec", "-i", "huipin-prelaunch-api-1", "python", "-"]
    )
    return json.loads(subprocess.check_output(command, input=code.encode()))


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))


def build():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    baseline = inner(SOURCES, BASE)
    assert all(baseline.get(n) == h for n, h in manifest["before"].items())
    base_id = inspect(BASE)["Id"]
    (ROOT / "Dockerfile").write_text(
        f"FROM {BASE}\nCOPY --chown=polaris:polaris app/ /app/app/\n"
    )
    subprocess.run(
        ["docker", "build", "--network=none", "-t", IMAGE, "."], cwd=ROOT, check=True
    )
    assert inspect(BASE)["Id"] == base_id
    expected = {**baseline, **manifest["after"]}
    assert inner(SOURCES, IMAGE) == expected, "Unexpected source changes"
    proof = inner(
        """
import json,sys
from app.replica_exact_compact import protocol_binding,run_class
from app.replica_direct_delivery import V15_BINDING
old=run_class('exact',V15_BINDING)
assert sys.modules[old.__module__].protocol_binding()==V15_BINDING
print(json.dumps({'current':protocol_binding(),'frozen_v15':V15_BINDING}))
""",
        IMAGE,
    )
    assert proof["current"] == manifest["protocol"]
    save(
        "build.json",
        {
            "image_id": inspect(IMAGE)["Id"],
            "protocols": proof,
            "only_expected_sources_changed": True,
        },
    )


def activate():
    proof = json.loads((ROOT / "build.json").read_text())
    assert inspect(IMAGE)["Id"] == proof["image_id"]
    assert inner(STATUS) == {"jobs": 0, "analyses": 0, "migration": "20261029_0034"}
    before = {s: inspect("huipin-prelaunch-" + s + "-1") for s in SERVICES}
    assert all(v["Config"]["Image"] == BASE for v in before.values())
    names = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}"], text=True
    ).splitlines()
    protected = {
        n: inspect(n)["Id"]
        for n in names
        if n not in {v["Name"].lstrip("/") for v in before.values()}
    }
    original = OVERRIDE.read_bytes()
    backup = ROOT / "billing-before.private.yml"
    if backup.exists():
        assert backup.read_bytes() == original, (
            "Preserve original rollback configuration"
        )
        assert (
            json.loads((ROOT / "activation.json").read_text())["status"]
            == "rolled_back"
        )
    else:
        backup.write_bytes(original)
        backup.chmod(0o600)
    uri = urlsplit(
        dict(v.split("=", 1) for v in before["api"]["Config"]["Env"])[
            "POLARIS_DATABASE_URL"
        ]
    )
    dump = ROOT / f"before-{time.time_ns()}.dump"
    with dump.open("wb") as output:
        subprocess.run(
            [
                "docker",
                "exec",
                "huipin-prelaunch-postgres-1",
                "pg_dump",
                "-U",
                unquote(uri.username),
                "-d",
                unquote(uri.path.lstrip("/")),
                "--format=custom",
            ],
            stdout=output,
            check=True,
        )
    dump.chmod(0o600)
    config = yaml.safe_load(original)
    for service in (*SERVICES, "migrate"):
        config["services"][service]["image"] = IMAGE
    compose = [
        "docker",
        "compose",
        "--project-name",
        "huipin-prelaunch",
        "--env-file",
        "/etc/huipin-prelaunch/prelaunch.env",
        "-f",
        "/srv/huipin-prelaunch/runtime/compose.yml",
        "-f",
        str(OVERRIDE),
    ]

    def write(content):
        pending = ROOT / "override.pending.private.yml"
        pending.write_bytes(content)
        pending.chmod(0o600)
        os.replace(pending, OVERRIDE)

    def verify_config():
        merged = json.loads(
            subprocess.check_output(compose + ["config", "--format", "json"])
        )
        for service, old in before.items():
            current = merged["services"][service]
            env = current.get("environment", {})
            for entry in old["Config"]["Env"]:
                key, value = entry.split("=", 1)
                if key.startswith("POLARIS_"):
                    assert str(env.get(key, "")) == value, (service, key)
            command = current.get("command", inspect(IMAGE)["Config"]["Cmd"])
            assert command == old["Config"]["Cmd"]

    write(yaml.safe_dump(config, sort_keys=False).encode())
    try:
        verify_config()
        assert inner(STATUS) == {"jobs": 0, "analyses": 0, "migration": "20261029_0034"}
        subprocess.run(compose + ["up", "-d", "--no-deps", *SERVICES], check=True)
        for _ in range(40):
            states = [
                inspect("huipin-prelaunch-" + s + "-1")["State"] for s in SERVICES
            ]
            if all(s.get("Health", {}).get("Status") == "healthy" for s in states):
                break
            time.sleep(2)
        else:
            raise RuntimeError("Backend health check failed")
        for s, old in before.items():
            new = inspect("huipin-prelaunch-" + s + "-1")
            # Compose can reorder the equivalent environment entries.
            assert dict(v.split("=", 1) for v in new["Config"]["Env"]) == dict(
                v.split("=", 1) for v in old["Config"]["Env"]
            )
            assert new["Config"]["Cmd"] == old["Config"]["Cmd"]
            for k in ("Memory", "NanoCpus", "PidsLimit", "Ulimits"):
                assert new["HostConfig"].get(k) == old["HostConfig"].get(k)
        assert {n: inspect(n)["Id"] for n in protected} == protected
        with urlopen(
            "https://huipin.haiyushuke.com/style-replica", timeout=30
        ) as response:
            assert response.status == 200
        assert (
            inner(
                "import json;from app.replica_exact_compact import protocol_binding;print(json.dumps(protocol_binding()))"
            )
            == proof["protocols"]["current"]
        )
    except BaseException:
        write(original)
        subprocess.run(compose + ["up", "-d", "--no-deps", *SERVICES], check=True)
        save("activation.json", {"status": "rolled_back"})
        raise
    save(
        "activation.json",
        {
            "status": "activated",
            "backend_image": IMAGE,
            "protocol": proof["protocols"]["current"],
            "other_containers_unchanged": True,
            "environment_and_commands_unchanged": True,
            "migration_unchanged": True,
            "production_jobs_restarted": 0,
        },
    )
    print((ROOT / "activation.json").read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("build", "activate"))
    globals()[parser.parse_args().action]()
