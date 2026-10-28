"""Scoped native-Skill release; credentials and Compose stay on the user's server.

Prepare captures a fresh baseline and database backup. Backend/frontend phases
replace the two replica roles, frontend and inspiration-preview API overlay;
no proxy, dispatcher, detail, main-worker or schema edits.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path("/etc/polaris/replica-native-20261022")
PREVIOUS = Path("/etc/polaris/one-click-replica-20261022/active.compose.private.json")
ROLES = ("api", "worker", "detail-api", "detail-worker", "dispatcher", "caddy",
         "replica-api", "replica-worker", "frontend")
PRESERVE = ("worker", "detail-api", "detail-worker", "dispatcher", "caddy")
SELECTED = ("api", "replica-api", "replica-worker", "frontend")
CADDY = Path("/opt/polaris/releases/888409d869a8459f0c4fe5086ff45eceae6efcc9/deploy/Caddyfile.domestic-detail-native")
ENV = {**os.environ, "COMPOSE_IGNORE_ORPHANS": "true"}


def inspect(role):
    return json.loads(subprocess.check_output([
        "docker", "inspect", "polaris-production-" + role + "-1"
    ]))[0]


def private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, indent=2)


def escaped(value):
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [escaped(row) for row in value]
    if isinstance(value, dict):
        return {key: escaped(row) for key, row in value.items()}
    return value


def preserve_runtime_fields(service, live):
    """Escape observed Docker values once; canonical Compose is already escaped."""
    source = live["Config"]
    service["environment"] = escaped(dict(row.split("=", 1) for row in source["Env"]))
    service["command"] = escaped(source.get("Cmd"))
    service["entrypoint"] = escaped(source.get("Entrypoint"))
    health = source.get("Healthcheck")
    if health:
        probe = {"test": escaped(health["Test"])}
        for original, target in (("Interval", "interval"), ("Timeout", "timeout"),
                                 ("StartPeriod", "start_period"), ("StartInterval", "start_interval")):
            if health.get(original):
                probe[target] = str(health[original]) + "ns"
        if "Retries" in health:
            probe["retries"] = health["Retries"]
        service["healthcheck"] = probe
    else:
        service.pop("healthcheck", None)


def idle():
    sql = """SELECT
      (SELECT count(*) FROM jobs WHERE configuration ? 'replica_session_id'
       AND status NOT IN ('partial','completed','failed','cancelled')) +
      (SELECT count(*) FROM replica_documents WHERE status IN ('fetching','analysing'))
    """
    count = subprocess.check_output([
        "docker", "exec", "-i", "polaris-production-postgres-1", "psql",
        "-U", "polaris", "-d", "polaris", "-At"
    ], input=sql.encode()).decode().strip()
    assert count == "0", "Replica work is active; defer cutover"


def unchanged(*roles):
    baseline = json.loads((ROOT / "baseline.json").read_text())
    for role in roles:
        current = inspect(role)
        assert {"id": current["Id"], "image": current["Image"]} == baseline["roles"][role], role + " changed since prepare"
    assert hashlib.sha256(CADDY.read_bytes()).hexdigest() == baseline["caddy_sha256"], "Proxy changed since prepare"


def health(role):
    for _ in range(40):
        current = inspect(role)
        if current["State"].get("Health", {}).get("Status") == "healthy":
            return
        assert current["State"]["Status"] not in {"exited", "dead"}, role + " stopped"
        time.sleep(2)
    raise RuntimeError(role + " health timeout")


def compose(path, *arguments):
    subprocess.run(["docker", "compose", "-f", str(path), *arguments],
                   check=True, env=ENV)


def prepare(revision, backend, frontend, api):
    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    for image in (backend, frontend, api):
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", image)
        subprocess.run(["docker", "image", "inspect", image], check=True,
                       stdout=subprocess.DEVNULL)
    assert not ROOT.exists(), "Prepared release already exists; inspect it"
    live = {role: inspect(role) for role in ROLES}
    for role in ROLES:
        health(role)
    idle()
    assert live["replica-api"]["Image"] == "sha256:bc2ea0f48b18fe25cb5fe4571d714b1a840e2a0f3c92d9448657a3380b93a918", "Replica base changed; rebase the overlay"
    assert live["frontend"]["Image"] == "sha256:a3cafa0564c3c40a8b68190fa9f5e369e490446c3420e3ad235af660b6f16faf", "Frontend base changed; rebase the overlay"
    assert live["api"]["Image"] == "sha256:985a8d0349919155da7017923a4795b08b1014ec08729fffae8412f0f79c68b1", "Main API base changed; rebase the overlay"
    config = json.loads(subprocess.check_output([
        "docker", "compose", "-f", str(PREVIOUS), "config", "--format", "json"
    ]))
    config["services"] = {
        role: deepcopy(config["services"][role])
        for role in ("replica-api", "replica-worker", "frontend")
    }
    add_main_api(config, live["api"])
    previous = deepcopy(config)
    for role, service in config["services"].items():
        assert not service.get("ports") and service.get("read_only")
        assert "ALL" in service.get("cap_drop", [])
        preserve_runtime_fields(service, live[role])
        preserve_runtime_fields(previous["services"][role], live[role])
        previous["services"][role]["image"] = live[role]["Image"]
        service["image"] = frontend if role == "frontend" else api if role == "api" else backend
        service["pull_policy"] = "never"
        service.setdefault("labels", {})["com.polaris.replica.native_revision"] = revision
    ROOT.mkdir(mode=0o700)
    private(ROOT / "rollback.compose.private.json", previous)
    private(ROOT / "active.compose.private.json", config)
    private(ROOT / "baseline.json", {
        "revision": revision,
        "roles": {role: {"id": value["Id"], "image": value["Image"]} for role, value in live.items()},
        "caddy_sha256": hashlib.sha256(CADDY.read_bytes()).hexdigest(),
    })
    compose(ROOT / "active.compose.private.json", "config", "--quiet")
    backup = Path("/opt/polaris/backups/20261022-replica-native")
    backup.mkdir(mode=0o700, exist_ok=False)
    with (backup / "postgres.dump").open("xb") as stream:
        subprocess.run(["docker", "exec", "polaris-production-postgres-1",
                        "pg_dump", "-U", "polaris", "-d", "polaris", "-Fc"],
                       stdout=stream, check=True)
    os.chmod(backup / "postgres.dump", 0o600)
    (backup / "README.txt").write_text("No schema migration or object deletion in this release. Existing immutable objects remain in place. Restore is not part of application rollback.\n")
    print(json.dumps({"prepared": True, "revision": revision, "roles": list(config["services"]), "credentials_exported": False}), flush=True)


def add_main_api(config, live):
    """Read the live API's canonical Compose on-server; never export credentials."""
    source = live["Config"]["Labels"]["com.docker.compose.project.config_files"]
    assert "," not in source and source.startswith("/etc/polaris/")
    original = json.loads(subprocess.check_output([
        "docker", "compose", "-f", source, "config", "--format", "json"
    ]))
    service = deepcopy(original["services"]["api"])
    # Dependencies already run; this overlay must not manage or redeclare them.
    service.pop("depends_on", None)
    config["services"]["api"] = service
    references = {
        "networks": list(service.get("networks", {})),
        "volumes": [row["source"] for row in service.get("volumes", []) if row["type"] == "volume"],
        "secrets": [row["source"] for row in service.get("secrets", [])],
        "configs": [row["source"] for row in service.get("configs", [])],
    }
    for section, keys in references.items():
        for key in keys:
            value = deepcopy(original[section][key])
            current = config.setdefault(section, {})
            if section in {"networks", "volumes"}:
                name = value["name"]
                subprocess.run(["docker", "network" if section == "networks" else "volume", "inspect", name],
                               check=True, stdout=subprocess.DEVNULL)
                assert key not in current or current[key]["name"] == name, section + " target differs"
                # Reuse existing resources without reconciling their definitions.
                value = {"name": name, "external": True}
            else:
                assert key not in current or current[key] == value, section + " definition differs"
            current[key] = value


def phase(name):
    unchanged(*PRESERVE)
    if name in {"backend", "rollback"}:
        idle()
    if name == "backend":
        unchanged(*SELECTED)
        selected = ("api", "replica-api", "replica-worker")
    elif name == "frontend":
        unchanged("frontend")
        health("replica-api")
        health("replica-worker")
        health("api")
        selected = ("frontend",)
    elif name == "rollback":
        selected = SELECTED
    else:
        selected = ()
    if selected:
        compose(ROOT / ("rollback.compose.private.json" if name == "rollback" else "active.compose.private.json"),
                "up", "-d", "--no-deps", "--no-build", "--pull", "never", *selected)
    for role in SELECTED:
        health(role)
    unchanged(*PRESERVE)
    evidence = {"phase": name, "preserved_roles": list(PRESERVE),
                "roles": {role: {"id": inspect(role)["Id"], "image": inspect(role)["Image"]} for role in ROLES}}
    (ROOT / (name + "-evidence.json")).write_text(json.dumps(evidence, indent=2))
    print(json.dumps({"phase": name, "healthy": True, "unrelated_services_unchanged": True}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "backend", "frontend", "verify", "rollback"])
    parser.add_argument("--revision")
    parser.add_argument("--backend-image")
    parser.add_argument("--frontend-image")
    parser.add_argument("--api-image")
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare(args.revision, args.backend_image, args.frontend_image, args.api_image)
    else:
        phase(args.phase)
