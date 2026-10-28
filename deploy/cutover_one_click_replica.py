"""Execute one bounded release phase. Requires prepared, private server evidence."""

import argparse, hashlib, json, os, subprocess, time
from pathlib import Path

ROOT = Path("/etc/polaris/one-click-replica-20261022")
COMPOSE = ROOT / "active.compose.private.json"
BASELINE = json.loads((ROOT / "baseline.private.json").read_text())
ENV = {**os.environ, "COMPOSE_IGNORE_ORPHANS": "true"}


def inspect(role):
    return json.loads(
        subprocess.check_output(
            ["docker", "inspect", "polaris-production-" + role + "-1"]
        )
    )[0]


def run(args, **kwargs):
    return subprocess.run(args, check=True, env=ENV, **kwargs)


def compose(*args, **kwargs):
    return run(["docker", "compose", "-f", str(COMPOSE), *args], **kwargs)


def unchanged(*roles):
    for role in roles:
        now = inspect(role)
        assert (
            now["Id"] == BASELINE[role]["id"]
            and now["Image"] == BASELINE[role]["image"]
        ), role + " baseline changed"


def health(role):
    for n in range(40):
        d = inspect(role)
        if d["State"].get("Health", {}).get("Status") == "healthy":
            return
        if d["State"]["Status"] in ("exited", "dead"):
            raise RuntimeError(role + " exited")
        time.sleep(2)
    raise RuntimeError(role + " health timeout")


def proxy_file():
    before = json.loads((ROOT / "caddy-baseline.json").read_text())
    p = Path(before["path"])
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before["sha256"], (
        "Active Caddy configuration changed"
    )
    return p


def execute(phase):
    unchanged("detail-api", "detail-worker", "api", "worker")
    backup = Path("/opt/polaris/backups/20261022-one-click-replica")
    assert json.loads((backup / "manifest.json").read_text())[
        "detail_containers_unchanged"
    ]
    assert json.loads((backup / "mock-smoke.json").read_text())["mock_job_completed"]
    if phase == "migrate":
        unchanged("dispatcher", "frontend", "caddy")
        proxy_file()
        compose(
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "PGOPTIONS=-c lock_timeout=5000 -c statement_timeout=30000",
            "replica-api",
            "python",
            "-m",
            "alembic",
            "upgrade",
            "head",
        )
    elif phase == "services":
        unchanged("dispatcher", "frontend", "caddy")
        proxy_file()
        compose(
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--pull",
            "never",
            "replica-api",
            "replica-worker",
        )
        health("replica-api")
        health("replica-worker")
        run(
            [
                "docker",
                "exec",
                "polaris-production-replica-worker-1",
                "sh",
                "-c",
                "celery -A app.celery_app:celery_app inspect ping --destination=one-click-replica@$HOSTNAME --timeout 5",
            ]
        )
        compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", "dispatcher")
        health("dispatcher")
    elif phase == "proxy":
        unchanged("frontend", "caddy")
        p = proxy_file()
        health("replica-api")
        health("replica-worker")
        # Existing generation calls must be idle before the shared proxy cutover.
        sql = "SELECT count(*) FROM jobs WHERE status IN ('running','queued','analysing','planning','generating')"
        count = (
            subprocess.check_output(
                [
                    "docker",
                    "exec",
                    "-i",
                    "polaris-production-postgres-1",
                    "psql",
                    "-U",
                    "polaris",
                    "-d",
                    "polaris",
                    "-At",
                ],
                input=sql.encode(),
            )
            .decode()
            .strip()
        )
        assert count == "0", "Generation started; postpone proxy cutover"
        candidate = (ROOT / "Caddyfile.candidate").read_bytes()
        run(
            [
                "docker", "exec", "-i", "polaris-production-caddy-1",
                "caddy", "validate", "--config", "-", "--adapter", "caddyfile",
            ],
            input=candidate,
        )
        with p.open("r+b") as f:
            f.write(candidate)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
        try:
            run(["docker", "restart", "--time", "10", "polaris-production-caddy-1"])
            health("caddy")
        except BaseException:
            with p.open("r+b") as f:
                f.write((ROOT / "Caddyfile.before").read_bytes())
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
            run(["docker", "restart", "--time", "10", "polaris-production-caddy-1"])
            raise
    elif phase == "frontend":
        unchanged("frontend")
        health("replica-api")
        health("replica-worker")
        health("dispatcher")
        health("caddy")
        compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", "frontend")
        health("frontend")
    else:
        raise ValueError("Unknown release phase")
    unchanged("detail-api", "detail-worker", "api", "worker")
    print(
        json.dumps({"phase": phase, "passed": True, "detail_services_unchanged": True}),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=["migrate", "services", "proxy", "frontend"])
    execute(p.parse_args().phase)
