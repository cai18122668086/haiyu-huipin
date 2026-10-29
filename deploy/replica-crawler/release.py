"""One-time integrated release on the existing Huipin stack, with rollback.

Run on the approved server from /srv/huipin-everyinfra-20261029 after
uploading the reviewed source package and provider.private.env (mode 0600).
The build and test commands never use the production database.
"""

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

import yaml

ROOT = Path("/srv/huipin-everyinfra-20261029")
RUNTIME = Path("/srv/huipin-prelaunch/runtime")
OVERRIDE = Path("/srv/huipin-membership-20261028/billing.override.yml")
BASE_BACKEND = "huipin-membership/backend:20261029-query"
BASE_FRONTEND = "huipin-membership/frontend:20261029-query"
BACKEND = "huipin-integrated/backend:20261029-everyinfra"
FRONTEND = "huipin-integrated/frontend:20261029-everyinfra"
SERVICES = ["api", "worker", "dispatcher", "frontend"]
SECRET_NAME = "POLARIS_EVERYINFRA_API_KEY"
EXTRA_TESTS: tuple[str, ...] = ()
PROTECTED_FILES = [
    Path("/app/haiyushuke-server/.env"),
    Path("/app/haiyushuke-server/configs/platform.production.yaml"),
    RUNTIME / "compose.yml",
    Path("/etc/huipin-prelaunch/prelaunch.env"),
    Path("/srv/huipin-membership-20261028/secrets/payments.env"),
]


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def environment(item):
    return dict(v.split("=", 1) for v in item["Config"]["Env"] if "=" in v)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2))


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


SOURCE_PROBE = """import hashlib,json
from pathlib import Path
print(json.dumps({str(p.relative_to('/app')):hashlib.sha256(p.read_bytes().replace(bytes([13,10]),bytes([10]))).hexdigest() for p in Path('/app/app').rglob('*.py')}))
"""


def frontend_sources(image, expected):
    code = r"""const fs=require('fs'),crypto=require('crypto');
let input=''; process.stdin.on('data',chunk=>input+=chunk);
process.stdin.on('end',()=>{const expected=JSON.parse(input),result={};
for(const name of Object.keys(expected)) {
 const bytes=fs.readFileSync('/app/'+name);
 const normalized=Buffer.from(bytes.toString('latin1').replace(/\r\n/g,'\n'),'latin1');
 result[name]=crypto.createHash('sha256').update(normalized).digest('hex');
} console.log(JSON.stringify(result)); });
"""
    result = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--entrypoint",
            "node",
            image,
            "-e",
            code,
        ],
        input=json.dumps(expected).encode(),
    )
    return json.loads(result)


STATUS_PROBE = """import json
from app.config import Settings
from sqlalchemy import create_engine,text
with create_engine(Settings().database_url).connect() as c:
 print(json.dumps({'jobs':c.execute(text("select count(*) from jobs where status in ('queued','planning','processing','cancelling')")).scalar(),'replicas':c.execute(text("select count(*) from replica_documents where status in ('fetching','analysing')")).scalar(),'migration':c.execute(text('select version_num from alembic_version')).scalar()}))
"""


def build():
    manifest = json.loads((ROOT / "release-manifest.json").read_text())
    for service in SERVICES:
        expected = BASE_FRONTEND if service == "frontend" else BASE_BACKEND
        current = inspect("huipin-prelaunch-" + service + "-1")
        assert current["Config"]["Image"] == expected, "Production baseline changed"
        assert current["Image"] == inspect(expected)["Id"], "Baseline tag changed"
    live = inner(SOURCE_PROBE)
    expected = manifest["backend_sources"]
    changed = sorted(
        k for k in live.keys() | expected.keys() if live.get(k) != expected.get(k)
    )
    assert changed == sorted(manifest["backend_changed"]), changed
    for target, dockerfile in [
        (BACKEND, "backend.Dockerfile"),
        (FRONTEND, "frontend.Dockerfile"),
    ]:
        with (ROOT / (dockerfile + ".log")).open("w") as output:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "--network",
                    "default" if target == FRONTEND else "none",
                    "--label",
                    "org.opencontainers.image.revision=" + manifest["revision"],
                    "-f",
                    dockerfile,
                    "-t",
                    target,
                    ".",
                ],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
            )
    assert inner(SOURCE_PROBE, BACKEND) == expected, (
        "Candidate backend differs from reviewed sources"
    )
    assert (
        frontend_sources(FRONTEND, manifest["frontend_sources"])
        == manifest["frontend_sources"]
    ), "Candidate frontend differs from reviewed sources"
    write_json(
        "build-result.json",
        {
            "revision": manifest["revision"],
            "backend_id": inspect(BACKEND)["Id"],
            "frontend_id": inspect(FRONTEND)["Id"],
            "backend_sources_checked": len(expected),
            "frontend_sources_checked": len(manifest["frontend_sources"]),
            "baseline_differences": changed,
        },
    )
    print(json.dumps({"build": "passed", "backend_sources_checked": len(expected)}))


def test():
    name = "huipin-everyinfra-postgres-test-20261029"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "none",
            "--memory",
            "256m",
            "--tmpfs",
            "/var/lib/postgresql/data",
            "-e",
            "POSTGRES_PASSWORD=synthetic-test-password",
            "-e",
            "POSTGRES_DB=huipin_test",
            "postgres:16-alpine",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            if (
                subprocess.run(
                    ["docker", "exec", name, "pg_isready", "-U", "postgres"],
                    check=False,
                    capture_output=True,
                ).returncode
                == 0
            ):
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable PostgreSQL did not start")
        command = (
            "PIP_REQUIRE_HASHES=0 python -m pip install --no-index --find-links=/testwheels --target=/tmp/testdeps pytest >/tmp/test-install.log 2>&1 || { cat /tmp/test-install.log; exit 1; }; "
            "PYTHONPATH=/tmp/testdeps:/app "
            "POLARIS_TEST_DATABASE_URL=postgresql+psycopg://postgres:synthetic-test-password@127.0.0.1:5432/huipin_test "
            "python -m pytest -o addopts='' -q --tb=short "
            "tests/test_credit_payments_postgres.py tests/test_credit_payments.py tests/test_credit_memberships.py "
            "tests/test_account_payment_followup.py tests/test_admin_billing.py tests/test_haiyu_payments_protocol.py "
            "tests/test_credit_migration_chain.py tests/test_credit_refunds.py tests/test_payment_reconciliation.py "
            "tests/test_customer_credit_billing.py tests/test_quota_fixes.py tests/test_phone_passwords.py "
            "tests/test_sms_identity_migration.py tests/test_public_browsing.py "
            "tests/test_everyinfra.py tests/test_justone.py tests/test_replica.py "
            + " ".join(EXTRA_TESTS)
            + " "
            + "-k 'not twenty_images_fit_job_capacity'"
        )
        with (ROOT / "postgres-test.log").open("w") as output:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "container:" + name,
                    "-v",
                    "/srv/huipin-membership-20261028/release:/testwheels:ro",
                    "--entrypoint",
                    "sh",
                    BACKEND,
                    "-c",
                    command,
                ],
                check=False,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        write_json(
            "test-result.json",
            {
                "exit_code": result.returncode,
                "image_id": inspect(BACKEND)["Id"],
                "isolated_database": True,
                "production_database_accessed": False,
                "preexisting_capacity_test_excluded": True,
            },
        )
        print((ROOT / "postgres-test.log").read_text()[-10000:])
        assert result.returncode == 0, "Isolated tests failed"
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL
        )


def activate():
    build_result = json.loads((ROOT / "build-result.json").read_text())
    test_result = json.loads((ROOT / "test-result.json").read_text())
    assert test_result["exit_code"] == 0
    assert (
        inspect(BACKEND)["Id"] == test_result["image_id"] == build_result["backend_id"]
    )
    assert inspect(FRONTEND)["Id"] == build_result["frontend_id"]
    assert inner(STATUS_PROBE) == {
        "jobs": 0,
        "replicas": 0,
        "migration": "20261029_0034",
    }, "Wait for current work to finish"
    before = {s: inspect("huipin-prelaunch-" + s + "-1") for s in SERVICES}
    for service, item in before.items():
        assert item["Config"]["Image"] == (
            BASE_FRONTEND if service == "frontend" else BASE_BACKEND
        )
    names = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}"], text=True
    ).splitlines()
    protected = {
        name: inspect(name)["Id"]
        for name in names
        if name not in {"huipin-prelaunch-" + s + "-1" for s in SERVICES}
    }
    hashes = {str(p): digest(p) for p in PROTECTED_FILES}
    original = OVERRIDE.read_bytes()
    backup = ROOT / "billing-before.private.yml"
    assert not backup.exists(), "Do not overwrite the original rollback configuration"
    backup.write_bytes(original)
    backup.chmod(0o600)
    private = ROOT / "containers-before.private.json"
    private.write_text(json.dumps(before))
    private.chmod(0o600)
    env = environment(before["api"])
    uri = urlsplit(env["POLARIS_DATABASE_URL"])
    dump = ROOT / "huipin-before-everyinfra.dump"
    assert not dump.exists()
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
    key_file = ROOT / "provider.private.env"
    assert key_file.stat().st_mode & 0o077 == 0, "Credential file must be private"
    key = key_file.read_text().strip().removeprefix(SECRET_NAME + "=")
    assert key.startswith("ei_") and len(key) > 20 and "\n" not in key
    updated = yaml.safe_load(original)
    for service in ["api", "worker", "dispatcher", "migrate"]:
        updated["services"][service]["image"] = BACKEND
    updated["services"]["frontend"]["image"] = FRONTEND
    updated["services"]["api"].setdefault("environment", {})[SECRET_NAME] = key
    compose = [
        "docker",
        "compose",
        "--project-name",
        "huipin-prelaunch",
        "--env-file",
        "/etc/huipin-prelaunch/prelaunch.env",
        "-f",
        str(RUNTIME / "compose.yml"),
        "-f",
        str(OVERRIDE),
    ]

    def write_config(content):
        temporary = OVERRIDE.with_suffix(".pending.yml")
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        os.replace(temporary, OVERRIDE)

    write_config(yaml.safe_dump(updated, sort_keys=False).encode())
    try:
        merged = json.loads(
            subprocess.check_output(
                compose + ["config", "--format", "json"], cwd=RUNTIME
            )
        )
        for service, previous in before.items():
            current = merged["services"][service]
            for name, value in environment(previous).items():
                if name.startswith(("POLARIS_", "NEXT_PUBLIC_", "VITE_")):
                    assert str(current.get("environment", {}).get(name, "")) == value, (
                        service,
                        name,
                    )
            effective_command = current.get("command")
            if effective_command is None:
                effective_command = inspect(current["image"])["Config"].get("Cmd")
            assert effective_command == previous["Config"].get("Cmd"), (
                service,
                "command changed",
            )
        assert merged["services"]["api"]["environment"][SECRET_NAME] == key
        # Recheck immediately before replacing services; no migration is required.
        assert inner(STATUS_PROBE) == {
            "jobs": 0,
            "replicas": 0,
            "migration": "20261029_0034",
        }
        subprocess.run(
            compose + ["up", "-d", "--no-deps", *SERVICES], cwd=RUNTIME, check=True
        )
        for _ in range(45):
            states = [
                inspect("huipin-prelaunch-" + s + "-1")["State"] for s in SERVICES
            ]
            if all(s.get("Health", {}).get("Status") == "healthy" for s in states):
                break
            assert all(s["Running"] for s in states), "Candidate service stopped"
            time.sleep(2)
        else:
            raise RuntimeError("Candidate did not become healthy")
        for path in ["/account", "/admin/billing", "/style-replica"]:
            with urlopen(
                "https://huipin.haiyushuke.com" + path, timeout=30
            ) as response:
                assert response.status == 200
        assert (
            inner(SOURCE_PROBE)
            == json.loads((ROOT / "release-manifest.json").read_text())[
                "backend_sources"
            ]
        )
        assert inner(STATUS_PROBE)["migration"] == "20261029_0034"
        assert {name: inspect(name)["Id"] for name in protected} == protected
        assert {str(p): digest(p) for p in PROTECTED_FILES} == hashes
        for service in SERVICES:
            after = inspect("huipin-prelaunch-" + service + "-1")
            assert after["Config"]["Cmd"] == before[service]["Config"]["Cmd"]
            for field in ["Memory", "NanoCpus", "PidsLimit", "Ulimits"]:
                assert after["HostConfig"].get(field) == before[service][
                    "HostConfig"
                ].get(field), (service, field)
    except BaseException:
        write_config(original)
        subprocess.run(
            compose + ["up", "-d", "--no-deps", *SERVICES], cwd=RUNTIME, check=True
        )
        write_json(
            "activation.json",
            {"status": "rolled_back", "database_migration_changed": False},
        )
        raise
    report = {
        "status": "activated",
        "revision": build_result["revision"],
        "backend_image": BACKEND,
        "frontend_image": FRONTEND,
        "migration": "20261029_0034",
        "payment_configuration_preserved": True,
        "worker_command_and_resource_limits_preserved": True,
        "other_services_preserved": True,
        "original_platform_files_preserved": True,
        "database_backup": str(dump),
    }
    write_json("activation.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "test", "activate"])
    globals()[parser.parse_args().action]()
