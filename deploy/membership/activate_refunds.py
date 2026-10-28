"""Upgrade Huipin's existing release; retain merchant configuration and queues."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path("/srv/huipin-refunds-20261028")
RUNTIME = Path("/srv/huipin-prelaunch/runtime")
OVERRIDE = Path("/srv/huipin-membership-20261028/billing.override.yml")
BACKEND = "huipin-membership/backend:20261028-refunds"
FRONTEND = "huipin-membership/frontend:20261028-refunds"
SERVICES = ["api", "worker", "dispatcher", "frontend"]
PROTECTED = ["haiyushuke-platform", "haiyushuke-website", "haiyushuke-gateway"]


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


assert json.loads((ROOT / "postgres-test-result.json").read_text())["exit_code"] == 0
protected = {name: inspect(name)["Id"] for name in PROTECTED}
before = {name: inspect("huipin-prelaunch-" + name + "-1") for name in SERVICES}
assert before["api"]["Config"]["Image"] == "huipin-membership/backend:20261028"
assert before["frontend"]["Config"]["Image"] == "huipin-membership/frontend:20261028-operator"
worker_command = before["worker"]["Config"]["Cmd"]
private = ROOT / "containers-before.private.json"
private.write_text(json.dumps(before))
os.chmod(private, 0o600)
probe = '''from app.config import Settings
from sqlalchemy import create_engine,text
import json
with create_engine(Settings().database_url).connect() as c:
 print(json.dumps({'active_jobs':c.execute(text("SELECT count(*) FROM jobs WHERE status IN ('queued','processing','planning','cancelling')")).scalar(),'migration':c.execute(text('SELECT version_num FROM alembic_version')).scalar()}))
'''
status = json.loads(subprocess.check_output(
    ["docker", "exec", "-i", "huipin-prelaunch-api-1", "python", "-"], input=probe.encode()))
assert status == {"active_jobs": 0, "migration": "20261028_0031"}, status
env = dict(value.split("=", 1) for value in before["api"]["Config"]["Env"] if "=" in value)
uri = urlsplit(env["POLARIS_DATABASE_URL"])
backup = ROOT / "huipin-before-refunds.dump"
assert not backup.exists(), "Keep the original pre-upgrade backup"
with backup.open("wb") as output:
    subprocess.run(["docker", "exec", "huipin-prelaunch-postgres-1", "pg_dump",
                    "-U", unquote(uri.username or "polaris"),
                    "-d", unquote(uri.path.lstrip("/")), "--format=custom"],
                   stdout=output, check=True)
os.chmod(backup, 0o600)
shutil.copy2(OVERRIDE, ROOT / "billing-before.yml")
content = yaml.safe_load(OVERRIDE.read_text())
for service in ["api", "worker", "dispatcher", "migrate"]:
    content["services"][service]["image"] = BACKEND
content["services"]["frontend"]["image"] = FRONTEND
OVERRIDE.write_text(yaml.safe_dump(content, sort_keys=False))
compose = ["docker", "compose", "--project-name", "huipin-prelaunch", "--env-file",
           "/etc/huipin-prelaunch/prelaunch.env", "-f", str(RUNTIME / "compose.yml"),
           "-f", str(OVERRIDE)]
merged = json.loads(subprocess.check_output(compose + ["config", "--format", "json"], cwd=RUNTIME))
assert merged["services"]["worker"]["command"] == worker_command
for key in ["POLARIS_CREDIT_ALIPAY_APP_ID", "POLARIS_CREDIT_ALIPAY_SELLER_ID",
            "POLARIS_CREDIT_WECHAT_MCH_ID", "POLARIS_CREDIT_WECHAT_API_V3_KEY"]:
    assert merged["services"]["api"]["environment"].get(key, "") == env.get(key, "")
subprocess.run(compose + ["run", "--rm", "--no-deps", "migrate"], check=True, cwd=RUNTIME)
subprocess.run(compose + ["up", "-d", "--no-deps", *SERVICES], check=True, cwd=RUNTIME)
assert {name: inspect(name)["Id"] for name in PROTECTED} == protected
assert inspect("huipin-prelaunch-worker-1")["Config"]["Cmd"] == worker_command
report = {"services_updated": SERVICES, "backend_image": BACKEND, "frontend_image": FRONTEND,
          "database_backup": str(backup), "migration_target": "20261028_0033",
          "original_platform_unchanged": True, "merchant_configuration_unchanged": True,
          "real_refunds_executed": 0, "verification_pending": True}
(ROOT / "activation.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
