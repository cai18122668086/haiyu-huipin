"""Build the patch and exercise real Celery/Redis in disposable containers."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/srv/huipin-concurrency-20261028")
IMAGE = "huipin-concurrency/backend:20261028"
NETWORK = "huipin-capacity-test-20261028"
REDIS = "huipin-capacity-test-redis"
WORKER = "huipin-capacity-test-worker"


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


base = inspect("huipin-prelaunch-worker-1")
baseline = json.loads((ROOT / "baseline.json").read_text())
check = """import hashlib,json
from pathlib import Path
expected=EXPECTED
for name,digest in expected.items():
 p=Path('/app')/name
 if digest is None:
  assert not p.exists(), ('New file already exists',name)
 else:
  assert hashlib.sha256(p.read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest()==digest, ('Source baseline changed',name)
print('Production source baseline verified')
""".replace("EXPECTED", repr(baseline))
run(
    ["docker", "exec", "-i", "huipin-prelaunch-worker-1", "python", "-"],
    input=check.encode(),
)
(ROOT / "Dockerfile").write_text(
    "FROM " + base["Config"]["Image"] + "\nCOPY patch/app /app/app\n"
)
run(["docker", "build", "--pull=false", "-t", IMAGE, str(ROOT)])
redis_image = inspect("huipin-prelaunch-redis-1")["Config"]["Image"]
run(["docker", "network", "create", "--internal", NETWORK])
try:
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            REDIS,
            "--network",
            NETWORK,
            "--network-alias",
            "redis",
            "--memory",
            "128m",
            redis_image,
            "redis-server",
            "--save",
            "",
            "--appendonly",
            "no",
        ]
    )
    common = [
        "docker",
        "run",
        "--rm",
        "--network",
        NETWORK,
        "-v",
        str(ROOT / "tools") + ":/probe:ro",
        "-v",
        str(ROOT / "tests") + ":/tests:ro",
        "-e",
        "PYTHONPATH=/app:/probe",
        "-e",
        "POLARIS_IMAGE_CAPACITY_REDIS_URL=redis://redis:6379/0",
    ]
    run(common + [IMAGE, "python", "/probe/verify_redis_capacity.py"])
    focused = "--focused-regression" in sys.argv
    if focused:
        assert "363 passed" in (ROOT / "regression.log").read_text()
    log_name = "regression-final.log" if focused else "regression.log"
    tests = (
        "/tests/test_provider_capacity.py /tests/test_task_runner_selection.py "
        "/tests/test_haiyu_transport.py /tests/test_p0_gates.py "
    )
    if not focused:
        tests += (
            "/tests/test_xinghe_agent.py /tests/test_xinghe_single_image_provider.py "
            "/tests/test_execution_reliability.py /tests/test_replica_analysis_attempt.py "
            "/tests/test_replica_analysis_recovery.py"
        )
    with (ROOT / log_name).open("w") as output:
        result = subprocess.run(
            common
            + [
                "-v",
                "/srv/huipin-membership-20261028/release:/testwheels:ro",
                IMAGE,
                "sh",
                "-c",
                "PIP_REQUIRE_HASHES=0 python -m pip install --no-index --find-links=/testwheels "
                "--target=/tmp/testdeps pytest >/tmp/test-install.log 2>&1 && "
                "PYTHONPATH=/tmp/testdeps:/app:/probe python -m pytest -q --tb=short "
                + tests,
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    assert result.returncode == 0, "Regression failed; read regression.log"
    worker = [
        "docker",
        "run",
        "-d",
        "--name",
        WORKER,
        "--network",
        NETWORK,
        "--memory",
        "3g",
        "--pids-limit",
        "4096",
        "--ulimit",
        "nofile=32768:32768",
        "-v",
        str(ROOT / "tools") + ":/probe:ro",
        "-e",
        "PYTHONPATH=/app:/probe",
        "-e",
        "POLARIS_CELERY_BROKER_URL=redis://redis:6379/0",
        "-e",
        "POLARIS_CELERY_RESULT_BACKEND=redis://redis:6379/1",
        "-e",
        "POLARIS_IMAGE_CAPACITY_REDIS_URL=redis://redis:6379/0",
        "-e",
        "POLARIS_WORKER_ASYNC_RUNTIME_ENABLED=true",
        "-e",
        "POLARIS_PROVIDER_ASYNC_TRANSPORT_ENABLED=true",
        IMAGE,
        "python",
        "-m",
        "celery",
        "-A",
        "celery_capacity_app:celery_app",
        "worker",
        "--pool=threads",
        "--concurrency=3000",
        "--prefetch-multiplier=1",
        "--queues=capacity_probe",
        "--loglevel=WARNING",
        "--hostname=capacity-test@%h",
    ]
    run(worker)
    script = """import json,time
from celery_capacity_app import celery_app
for _ in range(60):
 if celery_app.control.inspect(timeout=1).ping(): break
 time.sleep(1)
else: raise RuntimeError('Synthetic worker not ready')
start=time.monotonic()
results=[celery_app.send_task('polaris.process_job',args=[f'capacity_{i}'],queue='capacity_probe') for i in range(3000)]
import redis
client=redis.Redis.from_url('redis://redis:6379/1')
keys=['celery-task-meta-'+result.id for result in results]
for _ in range(120):
 payloads=client.mget(keys)
 if all(payloads) and all(json.loads(p)['status'] in ('SUCCESS','FAILURE') for p in payloads): break
 time.sleep(2)
else: raise RuntimeError('Synthetic result collection timed out')
assert all(json.loads(p)['status']=='SUCCESS' and json.loads(p)['result']['status']=='completed' for p in payloads)
stats=celery_app.send_task('polaris.capacity_stats',queue='capacity_probe').get(timeout=30)
assert stats['peak']==3000 and stats['completed']==3000,stats
stats.update({'celery_tasks':3000,'elapsed_seconds':round(time.monotonic()-start,2),'paid_requests':0,'production_database_used':False})
print(json.dumps(stats))
"""
    with (ROOT / "celery-capacity.log").open("w") as output:
        run(
            ["docker", "exec", "-i", WORKER, "python", "-"],
            input=script.encode(),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    report = {
        "image": IMAGE,
        "image_id": inspect(IMAGE)["Id"],
        "isolated_regression": True,
        "celery": json.loads(
            (ROOT / "celery-capacity.log").read_text().splitlines()[-1]
        ),
    }
    (ROOT / "capacity-result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
finally:
    for name in (WORKER, REDIS):
        if (
            subprocess.run(["docker", "inspect", name], capture_output=True).returncode
            == 0
        ):
            if name == WORKER:
                with (ROOT / "synthetic-worker.log").open("w") as output:
                    subprocess.run(
                        ["docker", "logs", name],
                        stdout=output,
                        stderr=subprocess.STDOUT,
                    )
            subprocess.run(["docker", "rm", "-f", name], check=True)
    subprocess.run(["docker", "network", "rm", NETWORK], check=True)
