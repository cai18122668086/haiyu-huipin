"""Prepare a scoped, private release on the production host. Does not deploy.

Only replica-api, replica-worker, frontend and dispatcher are represented.
Read credentials from existing containers; never print or export their values.
"""

import argparse
import json
import os
import re
import subprocess
from copy import deepcopy
from pathlib import Path


def inspect(role):
    return json.loads(
        subprocess.check_output(
            ["docker", "inspect", "polaris-production-" + role + "-1"]
        )
    )[0]


def private_json(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def escape(value):
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, dict):
        return {k: escape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [escape(v) for v in value]
    return value


def prepare(
    revision, backend_image, frontend_image, dispatcher_image, output,
    *, worker_concurrency: int = 4,
):
    if type(worker_concurrency) is not int or not 1 <= worker_concurrency <= 16:
        raise ValueError("Replica worker concurrency must be from 1 through 16")
    assert re.fullmatch("[0-9a-f]{40}", revision)
    for image in (backend_image, frontend_image, dispatcher_image):
        assert re.fullmatch("sha256:[0-9a-f]{64}", image)
        subprocess.run(
            ["docker", "image", "inspect", image], check=True, stdout=subprocess.DEVNULL
        )
    live = {
        r: inspect(r)
        for r in (
            "api",
            "worker",
            "frontend",
            "dispatcher",
            "detail-api",
            "detail-worker",
            "caddy",
        )
    }
    services, networks, volumes = {}, {}, {}
    for role in ("api", "worker", "frontend", "dispatcher"):
        cfg = json.loads(
            Path(
                live[role]["Config"]["Labels"][
                    "com.docker.compose.project.config_files"
                ]
            ).read_text()
        )
        svc = deepcopy(cfg["services"][role])
        for key in ("build", "depends_on", "container_name", "ports"):
            svc.pop(key, None)
        svc["image"] = live[role]["Image"]
        svc["pull_policy"] = "never"
        svc["environment"] = dict(
            v.split("=", 1) for v in live[role]["Config"]["Env"] if "=" in v
        )
        for key, item in cfg.get("networks", {}).items():
            networks[key] = {
                "external": True,
                "name": item.get("name", "polaris-production_" + key),
            }
        for key, item in cfg.get("volumes", {}).items():
            volumes[key] = {
                "external": True,
                "name": item.get("name", "polaris-production_" + key),
            }
        services[role] = svc
    api = services.pop("api")
    worker = services.pop("worker")
    existing = api["environment"]
    assert existing["POLARIS_ENVIRONMENT"] == "production"
    assert existing["POLARIS_AUTH_MODE"] == "external"
    assert existing.get("POLARIS_PROVIDER_QUOTA_ENABLED", "true") == "true"
    assert existing["POLARIS_ANALYSIS_BASE_URL"] == "https://api.tengliapi.xyz"
    private = dict(
        v.split("=", 1)
        for v in Path("/etc/polaris/replica-bench-20261022.env")
        .read_text()
        .splitlines()
        if "=" in v
    )
    assert private["POLARIS_JUSTONE_API_TOKEN"]
    settings = {
        "POLARIS_TASK_RUNNER": "tengli",
        "POLARIS_AUTO_PROCESS_JOBS": "false",
        "POLARIS_ECOMMERCE_STARTUP_RECOVERY_ENABLED": "false",
        "POLARIS_ECOMMERCE_LOGIN_ENABLED": "false",
        "POLARIS_RETENTION_ENABLED": "false",
        "POLARIS_PROVIDER_FAILOVER_ENABLED": "true",
        "POLARIS_PROVIDER_FAILOVER_API_KEY": existing["POLARIS_PROVIDER_FAILOVER_API_KEY"],
        "POLARIS_PROVIDER_FAILOVER_BASE_URL": existing["POLARIS_PROVIDER_FAILOVER_BASE_URL"],
        "POLARIS_PROVIDER_FAILOVER_MODEL": existing["POLARIS_PROVIDER_FAILOVER_MODEL"],
        "POLARIS_REPLICA_IMAGE_FAILOVER_ENABLED": "true",
        "POLARIS_PROVIDER_ROTATION_ORDER": existing["POLARIS_PROVIDER_ROTATION_ORDER"],
        "POLARIS_REFERENCE_REMIX_PROVIDER_ENABLED": "true",
        "POLARIS_ANALYSIS_BASE_URL": existing["POLARIS_ANALYSIS_BASE_URL"],
        "POLARIS_ANALYSIS_API_KEY": existing["POLARIS_ANALYSIS_API_KEY"],
        "POLARIS_ANALYSIS_MODEL": existing["POLARIS_ANALYSIS_MODEL"],
        "POLARIS_IMAGE_BASE_URL": existing["POLARIS_IMAGE_BASE_URL"],
        "POLARIS_IMAGE_API_KEY": existing["POLARIS_IMAGE_API_KEY"],
        "POLARIS_IMAGE_MODEL": existing["POLARIS_IMAGE_MODEL"],
        "POLARIS_TENGLI_IMAGE_MODEL": existing.get("POLARIS_TENGLI_IMAGE_MODEL", "gpt-image-2"),
        "POLARIS_TENGLI_BASE_URL": "https://api.tengliapi.xyz",
        "POLARIS_TENGLI_API_KEY": existing["POLARIS_TENGLI_API_KEY"],
        "POLARIS_TENGLI_VISION_MODEL": existing["POLARIS_ANALYSIS_MODEL"],
        "POLARIS_DATABASE_POOL_SIZE": "2",
        "POLARIS_DATABASE_MAX_OVERFLOW": "1",
    }
    for role, svc in (("replica-api", api), ("replica-worker", worker)):
        svc["image"] = backend_image
        svc["environment"].update(settings)
        svc["environment"]["POLARIS_JUSTONE_API_TOKEN"] = (
            private["POLARIS_JUSTONE_API_TOKEN"] if role == "replica-api" else ""
        )
        svc["volumes"] = [
            v
            for v in svc.get("volumes", [])
            if v["target"] == "/app/.data/aurora-main-image"
        ]
        assert len(svc["volumes"]) == 1
        for value in svc.get("networks", {}).values():
            if isinstance(value, dict):
                value.pop("aliases", None)
        svc.setdefault("labels", {})["com.polaris.replica.revision"] = revision
        svc["mem_limit"] = "1g"
        svc["cpus"] = "1.0"
        services[role] = svc
    worker["mem_limit"] = "2g"
    worker["command"] = [
        "python",
        "-m",
        "celery",
        "-A",
        "app.celery_app:celery_app",
        "worker",
        "--loglevel=INFO",
        f"--concurrency={worker_concurrency}",
        "--prefetch-multiplier=1",
        "--queues=one_click_replica",
        "--hostname=one-click-replica@%h",
    ]
    worker["healthcheck"]["test"] = [
        "CMD-SHELL",
        "celery -A app.celery_app:celery_app inspect ping --destination=one-click-replica@$HOSTNAME --timeout 5 >/dev/null 2>&1",
    ]
    services["dispatcher"]["image"] = dispatcher_image
    services["frontend"]["image"] = frontend_image
    result = {
        "name": "polaris-production",
        "services": services,
        "networks": networks,
        "volumes": volumes,
    }
    assert set(services) == {"replica-api", "replica-worker", "frontend", "dispatcher"}
    assert all(not s.get("ports") and s.get("read_only") for s in services.values())
    assert settings["POLARIS_IMAGE_BASE_URL"] == "https://api.artflow.vip"
    assert settings["POLARIS_PROVIDER_FAILOVER_BASE_URL"] == "https://api.ooapi.cc"
    assert settings["POLARIS_PROVIDER_FAILOVER_API_KEY"]
    assert settings["POLARIS_IMAGE_API_KEY"]
    private_json(output, escape(result))
    private_json(
        output.with_name("baseline.private.json"),
        {r: {"id": d["Id"], "image": d["Image"]} for r, d in live.items()},
    )
    print(
        json.dumps(
            {
                "prepared": True,
                "revision": revision,
                "services": sorted(services),
                "credentials_exported": False,
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("revision", "backend-image", "frontend-image", "dispatcher-image"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--worker-concurrency", type=int, default=4)
    a = p.parse_args()
    prepare(a.revision, a.backend_image, a.frontend_image, a.dispatcher_image, a.output,
            worker_concurrency=a.worker_concurrency)
