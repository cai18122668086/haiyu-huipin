"""Construct the real runner with production settings, with networking disabled."""

import json
import subprocess
from pathlib import Path

root = Path("/srv/huipin-concurrency-20261028")
api = json.loads(
    subprocess.check_output(["docker", "inspect", "huipin-prelaunch-api-1"])
)[0]
env = dict(value.split("=", 1) for value in api["Config"]["Env"] if "=" in value)
env.update(
    {
        "POLARIS_XINGHE_CONCURRENCY": "3000",
        "POLARIS_REPLICA_ANALYSIS_CONCURRENCY": "0",
        "POLARIS_IMAGE_CAPACITY_REDIS_URL": env["POLARIS_CELERY_BROKER_URL"],
        "POLARIS_WORKER_ASYNC_RUNTIME_ENABLED": "true",
        "POLARIS_PROVIDER_ASYNC_TRANSPORT_ENABLED": "true",
    }
)
code = """import json,os,sys
os.environ.update(json.load(sys.stdin))
from app.tasks import _process_runner,settings
assert settings.task_runner=='tengli'
runner=_process_runner()
assert runner.coordinator.max_concurrency==3000
print(json.dumps({'real_runner_startup':True,'concurrency':3000,'network':'none','paid_calls':0}))
"""
result = subprocess.check_output(
    [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "huipin-concurrency/backend:20261028",
        "python",
        "-c",
        code,
    ],
    input=json.dumps(
        {k: v for k, v in env.items() if k.startswith("POLARIS_")}
    ).encode(),
)
report = json.loads(result)
(root / "real-startup-result.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
