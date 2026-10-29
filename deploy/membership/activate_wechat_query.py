"""Upgrade Huipin's existing release; retain merchant configuration and queues."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path("/srv/huipin-wechat-query-20261029")
RUNTIME = Path("/srv/huipin-prelaunch/runtime")
OVERRIDE = Path("/srv/huipin-membership-20261028/billing.override.yml")
BACKEND = "huipin-membership/backend:20261029-query"
FRONTEND = "huipin-membership/frontend:20261029-query"
SERVICES = ["api", "worker", "dispatcher", "frontend"]
PROTECTED = [
    name
    for name in subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}"], text=True
    ).splitlines()
    if name not in {"huipin-prelaunch-" + service + "-1" for service in SERVICES}
]
PRODUCTION_FILES = [
    Path("/app/haiyushuke-server/.env"),
    Path("/app/haiyushuke-server/configs/platform.production.yaml"),
]
production_hashes = {
    str(path): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in PRODUCTION_FILES
}


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


assert json.loads((ROOT / "postgres-test-result.json").read_text())["exit_code"] == 0


def fingerprint(name):
    item = inspect(name)
    return {
        "id": item["Id"],
        "image": item["Image"],
        "config_hash": hashlib.sha256(
            json.dumps(item["Config"], sort_keys=True).encode()
        ).hexdigest(),
    }


protected = {name: fingerprint(name) for name in PROTECTED}
probe = json.loads((ROOT / "wechat-native-query-probe.json").read_text())
assert (
    probe["signed_query_verified"]
    and probe["native_qr_created"]
    and probe["probe_order_closed"]
)
assert probe["api_v3_key_used"] is False
before = {name: inspect("huipin-prelaunch-" + name + "-1") for name in SERVICES}
assert before["api"]["Config"]["Image"] == "huipin-concurrency/backend:20261028"
assert (
    before["api"]["Image"]
    == "sha256:9f174ef1ca9f334a3e75628d5a10ce8ed1125315f0152016fa2765dea9a854aa"
)
assert (
    before["frontend"]["Config"]["Image"]
    == "huipin-membership/frontend:20261028-refunds"
)
worker_command = before["worker"]["Config"]["Cmd"]
private = ROOT / "containers-before.private.json"
private.write_text(json.dumps(before))
os.chmod(private, 0o600)
probe = """from app.config import Settings
from sqlalchemy import create_engine,text
import json
import hashlib
with create_engine(Settings().database_url).connect() as c:
 print(json.dumps({'active_jobs':c.execute(text("SELECT count(*) FROM jobs WHERE status IN ('queued','processing','planning','cancelling')")).scalar(),'migration':c.execute(text('SELECT version_num FROM alembic_version')).scalar()}))
"""
status = json.loads(
    subprocess.check_output(
        ["docker", "exec", "-i", "huipin-prelaunch-api-1", "python", "-"],
        input=probe.encode(),
    )
)
assert status == {"active_jobs": 0, "migration": "20261028_0033"}, status
env = dict(
    value.split("=", 1) for value in before["api"]["Config"]["Env"] if "=" in value
)
uri = urlsplit(env["POLARIS_DATABASE_URL"])
backup = ROOT / "huipin-before-wechat-query.dump"
assert not backup.exists(), "Keep the original pre-upgrade backup"
with backup.open("wb") as output:
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
os.chmod(backup, 0o600)
shutil.copy2(OVERRIDE, ROOT / "billing-before.yml")
content = yaml.safe_load(OVERRIDE.read_text())
for service in ["api", "worker", "dispatcher", "migrate"]:
    content["services"][service]["image"] = BACKEND
content["services"]["frontend"]["image"] = FRONTEND
api_environment = content["services"]["api"].setdefault("environment", {})
api_environment["POLARIS_CREDIT_WECHAT_CONFIRMATION_MODE"] = "query"
api_environment["POLARIS_CREDIT_PAYMENT_RECONCILIATION_ENABLED"] = "true"
OVERRIDE.write_text(yaml.safe_dump(content, sort_keys=False))
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
merged = json.loads(
    subprocess.check_output(compose + ["config", "--format", "json"], cwd=RUNTIME)
)
assert merged["services"]["worker"]["command"] == worker_command
for service in ["api", "worker", "dispatcher"]:
    previous = dict(
        value.split("=", 1)
        for value in before[service]["Config"]["Env"]
        if "=" in value
    )
    updated = merged["services"][service]["environment"]
    for key, value in previous.items():
        if not key.startswith("POLARIS_") or (
            service == "api"
            and key
            in {
                "POLARIS_CREDIT_WECHAT_CONFIRMATION_MODE",
                "POLARIS_CREDIT_PAYMENT_RECONCILIATION_ENABLED",
            }
        ):
            continue
        assert updated.get(key, "") == value, (service, key)
subprocess.run(
    compose + ["run", "--rm", "--no-deps", "migrate"], check=True, cwd=RUNTIME
)
subprocess.run(compose + ["up", "-d", "--no-deps", *SERVICES], check=True, cwd=RUNTIME)
assert {name: fingerprint(name) for name in PROTECTED} == protected
assert {
    str(path): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in PRODUCTION_FILES
} == production_hashes
assert inspect("huipin-prelaunch-worker-1")["Config"]["Cmd"] == worker_command
report = {
    "services_updated": SERVICES,
    "backend_image": BACKEND,
    "frontend_image": FRONTEND,
    "database_backup": str(backup),
    "migration_target": "20261029_0034",
    "original_platform_unchanged": True,
    "all_other_running_services_unchanged": True,
    "haiyu_production_files_unchanged": True,
    "wechat_confirmation_mode": "query",
    "background_reconciliation_enabled": True,
    "merchant_configuration_unchanged": True,
    "real_refunds_executed": 0,
    "verification_pending": True,
}
(ROOT / "activation.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
