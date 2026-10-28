"""Activate verified concurrency changes through the current billing override."""

import json
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import yaml

ROOT = Path("/srv/huipin-concurrency-20261028")
RUNTIME = Path("/srv/huipin-prelaunch/runtime")
OVERRIDE = Path("/srv/huipin-membership-20261028/billing.override.yml")
IMAGE = "huipin-concurrency/backend:20261028"
SERVICES = ["api", "worker", "dispatcher"]


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def inner(code):
    return json.loads(
        subprocess.check_output(
            ["docker", "exec", "-i", "huipin-prelaunch-api-1", "python", "-"],
            input=code.encode(),
        )
    )


result = json.loads((ROOT / "capacity-result.json").read_text())
assert result["celery"]["peak"] == 3000 and result["celery"]["completed"] == 3000
assert result["isolated_regression"]
assert result["image_id"] == inspect(IMAGE)["Id"], "Candidate changed after tests"
assert json.loads((ROOT / "real-startup-result.json").read_text())[
    "real_runner_startup"
]
all_names = (
    subprocess.check_output(["docker", "ps", "--format", "{{.Names}} "])
    .decode()
    .split()
)
all_before = {name: inspect(name)["Id"] for name in all_names}
before = {role: inspect("huipin-prelaunch-" + role + "-1") for role in SERVICES}
env = dict(
    value.split("=", 1) for value in before["api"]["Config"]["Env"] if "=" in value
)
assert before["api"]["Config"]["Image"] == "huipin-membership/backend:20261028-refunds"
status_code = """import json
from app.config import Settings
from sqlalchemy import create_engine,text
with create_engine(Settings().database_url).connect() as c:
 print(json.dumps({'active':c.execute(text("select count(*) from jobs where status in ('queued','planning','processing','cancelling')")).scalar(),
 'migration':c.execute(text('select version_num from alembic_version')).scalar()}))
"""
assert inner(status_code) == {"active": 0, "migration": "20261028_0033"}, (
    "Wait for active work to drain"
)
original = OVERRIDE.read_bytes()
backup = ROOT / "billing-before-final.private.yml"
assert not backup.exists(), "Preserve the original rollback file"
backup.write_bytes(original)
backup.chmod(0o600)
config = yaml.safe_load(original)
for role in SERVICES + ["migrate"]:
    service = config["services"].setdefault(role, {})
    service["image"] = IMAGE
    if role != "migrate":
        service.setdefault("environment", {}).update(
            {
                "POLARIS_XINGHE_CONCURRENCY": "3000",
                "POLARIS_IMAGE_GLOBAL_CONCURRENCY": "3000",
                "POLARIS_IMAGE_CAPACITY_REDIS_URL": env["POLARIS_CELERY_BROKER_URL"],
                "POLARIS_REPLICA_ANALYSIS_CONCURRENCY": "0",
                "POLARIS_PROVIDER_ASYNC_TRANSPORT_ENABLED": "true",
                "POLARIS_WORKER_ASYNC_RUNTIME_ENABLED": "true",
                "POLARIS_OUTBOX_BATCH_SIZE": "200",
            }
        )
worker = config["services"]["worker"]
command = list(before["worker"]["Config"]["Cmd"])
assert "--concurrency=6" in command
command[command.index("--concurrency=6")] = "--concurrency=3000"
command.append("--pool=threads")
worker.update(
    {
        "command": command,
        "mem_limit": "8192m",
        "cpus": "2.00",
        "pids_limit": 4096,
        "stop_grace_period": "1800s",
        "ulimits": {"nofile": {"soft": 32768, "hard": 32768}},
    }
)
config["services"]["api"]["mem_limit"] = "2g"
config["services"]["dispatcher"]["mem_limit"] = "512m"
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
OVERRIDE.write_text(yaml.safe_dump(config, sort_keys=False))
OVERRIDE.chmod(0o600)
workspace_before = None
try:
    merged = json.loads(
        subprocess.check_output(compose + ["config", "--format", "json"], cwd=RUNTIME)
    )
    for key, value in env.items():
        if key.startswith(("POLARIS_CREDIT_", "POLARIS_WORKSPACE_CREDIT_")):
            assert merged["services"]["api"]["environment"].get(key) == value, (
                "Billing config changed",
                key,
            )
    assert next(arg for arg in command if arg.startswith("--queues=")) == next(
        arg for arg in before["worker"]["Config"]["Cmd"] if arg.startswith("--queues=")
    )
    subprocess.run(
        compose + ["up", "-d", "--no-deps", *SERVICES], cwd=RUNTIME, check=True
    )
    for _ in range(90):
        states = [
            inspect("huipin-prelaunch-" + role + "-1")["State"] for role in SERVICES
        ]
        if all(state.get("Health", {}).get("Status") == "healthy" for state in states):
            break
        assert all(state["Running"] for state in states), "A new service stopped"
        time.sleep(2)
    else:
        raise RuntimeError("New services did not become healthy")
    # Only rows carrying the old defaults are upgraded; retain custom limits.
    workspace_code = """import json
from app.config import Settings
from sqlalchemy import create_engine,text
with create_engine(Settings().database_url).begin() as c:
 rows=[dict(r._mapping) for r in c.execute(text('select workspace_id,max_active_jobs,max_concurrent_jobs from workspace_limits where max_active_jobs=20 and max_concurrent_jobs=4 for update'))]
 c.execute(text('update workspace_limits set max_active_jobs=3000,max_concurrent_jobs=3000 where max_active_jobs=20 and max_concurrent_jobs=4'))
 print(json.dumps(rows))
"""
    workspace_before = inner(workspace_code)
    private = ROOT / "workspace-limits-before.private.json"
    private.write_text(json.dumps(workspace_before))
    private.chmod(0o600)
    with urlopen("https://huipin.haiyushuke.com/account", timeout=30) as response:
        assert response.status == 200
    changed_names = {"huipin-prelaunch-" + role + "-1" for role in SERVICES}
    assert all(
        inspect(name)["Id"] == identity
        for name, identity in all_before.items()
        if name not in changed_names
    )
    assert inner(status_code)["migration"] == "20261028_0033"
except BaseException:
    if workspace_before:
        restore = """import json
from app.config import Settings
from sqlalchemy import create_engine,text
rows=ROWS
with create_engine(Settings().database_url).begin() as c:
 for row in rows:
  c.execute(text('update workspace_limits set max_active_jobs=:max_active_jobs,max_concurrent_jobs=:max_concurrent_jobs where workspace_id=:workspace_id and max_active_jobs=3000 and max_concurrent_jobs=3000'),row)
print(json.dumps({'restored':len(rows)}))
""".replace("ROWS", repr(workspace_before))
        inner(restore)
    OVERRIDE.write_bytes(original)
    subprocess.run(
        compose + ["up", "-d", "--no-deps", *SERVICES], cwd=RUNTIME, check=True
    )
    raise
report = {
    "status": "ACTIVATED",
    "backend_image": IMAGE,
    "image_global_concurrency": 3000,
    "worker_task_slots": 3000,
    "worker_pool": "threads",
    "analysis_business_limit": 0,
    "workspace_default_limits_updated": len(workspace_before),
    "billing_configuration_preserved": True,
    "unrelated_containers_preserved": True,
    "database_migration": "20261028_0033",
    "paid_load_tests": False,
}
(ROOT / "activation.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
