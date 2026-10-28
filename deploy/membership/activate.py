"""Deploy only Huipin services; preserve the shared Haiyu platform and gateway."""

import json
import os
import subprocess
from urllib.parse import unquote, urlsplit
from pathlib import Path

import yaml

ROOT = Path("/srv/huipin-membership-20261028")
RUNTIME = Path("/srv/huipin-prelaunch/runtime")
ENVFILE = "/etc/huipin-prelaunch/prelaunch.env"
BASE = RUNTIME / "compose.yml"
OVERRIDE = ROOT / "billing.override.yml"
BACKEND = "huipin-membership/backend:20261028"
FRONTEND = "huipin-membership/frontend:20261028"
services = ["api", "worker", "dispatcher", "frontend"]
before = {}
for name in services:
    original = json.loads(subprocess.check_output(["docker", "inspect", f"huipin-prelaunch-{name}-1"]))[0]
    before[name] = original
receipt = ROOT / "containers-before.private.json"
receipt.write_text(json.dumps(before, indent=2))
os.chmod(receipt, 0o600)
assert before["api"]["Image"].startswith("sha256:d4a90b21f41c"), "API changed during preparation"
assert before["frontend"]["Image"].startswith("sha256:9c447a4ef6c2"), "Frontend changed during preparation"
tests = json.loads((ROOT / "postgres-test-result.json").read_text())
assert tests["exit_code"] == 0, "Financial tests must pass before activation"
probe = json.loads((ROOT / "merchant-probe.json").read_text())
assert probe["response_signature_valid"] and probe["sub_code"] == "ACQ.TRADE_NOT_EXIST"
overrides = {"services": {
    "api": {"image": BACKEND, "env_file": [str(ROOT / "secrets/payments.env")],
            "environment": {"POLARIS_WORKSPACE_CREDIT_ENFORCEMENT_ENABLED": "true",
                            "POLARIS_CREDIT_PAYMENTS_ENABLED": "true"},
            "volumes": [str(ROOT / "secrets") + ":/run/huipin-payments:ro"]},
    "worker": {"image": BACKEND, "environment": {"POLARIS_WORKSPACE_CREDIT_ENFORCEMENT_ENABLED": "true"}},
    "dispatcher": {"image": BACKEND, "environment": {"POLARIS_WORKSPACE_CREDIT_ENFORCEMENT_ENABLED": "true"}},
    "migrate": {"image": BACKEND},
    "frontend": {"image": FRONTEND},
}}
OVERRIDE.write_text(yaml.safe_dump(overrides, sort_keys=False))
compose = ["docker", "compose", "--project-name", "huipin-prelaunch", "--env-file", ENVFILE,
           "-f", str(BASE), "-f", str(OVERRIDE)]
# Parse the merged deployment in memory; never print its credentials.
merged = json.loads(subprocess.check_output(compose + ["config", "--format", "json"], cwd=RUNTIME))
assert merged["services"]["api"]["environment"]["POLARIS_CREDIT_ALIPAY_MODE"] == "cert"
assert merged["services"]["api"]["environment"]["POLARIS_CREDIT_WECHAT_API_V3_KEY"] == ""
assert "--concurrency=6" in before["worker"]["Config"]["Cmd"]
assert merged["services"]["worker"]["command"] == before["worker"]["Config"]["Cmd"]
check = """from app.config import Settings
from sqlalchemy import create_engine,text
from pathlib import Path
import json
s=Settings(); e=create_engine(s.database_url)
with e.connect() as c:
 active=c.execute(text("select count(*) from jobs where status in ('queued','processing','planning','cancelling')")).scalar()
 if active: raise SystemExit('Generation work is active; activation postponed')
 print(json.dumps({'active_jobs':active,'migration':c.execute(text('select version_num from alembic_version')).scalar()}))
"""
subprocess.run(["docker", "exec", "-i", "huipin-prelaunch-api-1", "python", "-"], input=check.encode(), check=True)
backup = ROOT / "huipin-database-before.dump"
database_uri = urlsplit(merged["services"]["api"]["environment"]["POLARIS_DATABASE_URL"])
database_name = unquote(database_uri.path.lstrip("/"))
database_user = unquote(database_uri.username or "polaris")
with backup.open("wb") as output:
    subprocess.run(["docker", "exec", "huipin-prelaunch-postgres-1", "pg_dump", "-U", database_user,
                    "-d", database_name, "--format=custom"], check=True, stdout=output)
os.chmod(backup, 0o600)
subprocess.run(compose + ["run", "--rm", "--no-deps", "migrate"], cwd=RUNTIME, check=True)
subprocess.run(compose + ["up", "-d", "--no-deps", *services], cwd=RUNTIME, check=True)
report = {"services_updated": services, "payment_channels_enabled": ["alipay"],
          "wechat_enabled": False, "original_platform_changed": False,
          "gateway_configuration_changed": False, "other_chat_sources_changed": False,
          "worker_concurrency": 6, "override": str(OVERRIDE), "backup": str(backup),
          "verification_pending": True}
(ROOT / "activation.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
