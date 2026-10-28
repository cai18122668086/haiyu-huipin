"""Prepare a private Compose overlay; never start, restart or deploy a service.

Input is resolved production Compose JSON. Output contains inherited secrets and
must remain on the target server outside Git, readable only by its owner.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_PROVIDER_KEYS = {
    "POLARIS_ANALYSIS_BASE_URL",
    "POLARIS_ANALYSIS_MODEL",
    "POLARIS_ANALYSIS_API_KEY",
}


def overlay(
    base: dict[str, Any],
    *,
    backend_image: str,
    frontend_image: str,
    revision: str,
    caddy_file: str,
    provider: dict[str, str],
    native_creation: bool = True,
    worker_concurrency: int = 6,
) -> dict[str, Any]:
    if base.get("name") != "polaris-production":
        raise ValueError("Expected the existing production Compose project")
    if not _IMAGE_ID.fullmatch(backend_image) or not _IMAGE_ID.fullmatch(
        frontend_image
    ):
        raise ValueError("Both release images must be immutable local SHA256 image IDs")
    if not _REVISION.fullmatch(revision):
        raise ValueError("The release must name its complete Git revision")
    if (
        caddy_file
        != f"/opt/polaris/releases/{revision}/deploy/Caddyfile.domestic-detail-native"
    ):
        raise ValueError("Use the reviewed Caddyfile inside this exact release")
    original = base["services"]
    if isinstance(worker_concurrency, bool) or not 1 <= worker_concurrency <= 32:
        raise ValueError("Detail worker concurrency must be from 1 through 32")
    for name in ("api", "worker", "dispatcher", "frontend", "caddy", "migrate"):
        if name not in original:
            raise ValueError("A required original production service is missing")
    # Match one existing account with its own endpoint and model. The website
    # may keep its working text-planning accounts in the fallback pool while
    # the legacy primary token belongs to an image-only account.
    primary = original["api"]["environment"]
    accounts = [{key: primary.get(key) for key in _PROVIDER_KEYS}]
    if str(primary.get("POLARIS_PROVIDER_FAILOVER_ENABLED", "false")).lower() == "true":
        accounts.extend(
            {
                "POLARIS_ANALYSIS_BASE_URL": primary.get(
                    "POLARIS_PROVIDER_FAILOVER_BASE_URL"
                ),
                "POLARIS_ANALYSIS_MODEL": primary.get(
                    "POLARIS_PROVIDER_FAILOVER_MODEL"
                ),
                "POLARIS_ANALYSIS_API_KEY": key,
            }
            for key in re.split(
                r"[\s,]+", primary.get("POLARIS_PROVIDER_FAILOVER_API_KEY", "").strip()
            )
            if key
        )
    if (
        set(provider) != _PROVIDER_KEYS
        or any(
            not isinstance(provider[key], str) or not provider[key].strip()
            for key in _PROVIDER_KEYS
        )
        or re.search(r"[\s,]", provider["POLARIS_ANALYSIS_API_KEY"])
        or provider not in accounts
    ):
        raise ValueError(
            "Detail analysis must match one existing configured planning account"
        )
    api = deepcopy(original["api"])
    worker = deepcopy(original["worker"])
    for role, service in (("api", api), ("worker", worker)):
        service.pop("build", None)
        service.pop("container_name", None)
        service.pop("ports", None)
        service["image"] = backend_image
        service["pull_policy"] = "never"
        service.setdefault("labels", {}).update(
            {
                "com.polaris.release.source": revision,
                "com.polaris.domestic-detail.role": role,
            }
        )
        environment = service["environment"]
        environment.update(provider)
        environment.update(
            {
                "POLARIS_LEGACY_DETAIL_API_URL": "http://api:8000",
                "POLARIS_DOMESTIC_DETAIL_NATIVE_CREATION_ENABLED": str(
                    native_creation
                ).lower(),
                "POLARIS_ECOMMERCE_STARTUP_RECOVERY_ENABLED": "false",
                "POLARIS_ECOMMERCE_LOGIN_ENABLED": "false",
                "POLARIS_PROVIDER_FAILOVER_ENABLED": "false",
                "POLARIS_PROVIDER_ROTATION_ORDER": "primary",
                "POLARIS_AUTO_PROCESS_JOBS": "false",
                "POLARIS_ANALYSIS_TIMEOUT_SECONDS": "1200",
            }
        )
        # This process has no browser role or original browser-profile authority.
        service["volumes"] = [
            v
            for v in service.get("volumes", [])
            if v.get("target") == "/app/.data/aurora-main-image"
        ]
        if len(service["volumes"]) != 1:
            raise ValueError("The existing shared native Harness volume is required")
    api["mem_limit"] = "512m"
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
        "--queues=domestic_detail_v431",
        "--hostname=domestic-detail@%h",
    ]
    caddy_volumes = deepcopy(original["caddy"].get("volumes", []))
    matches = [v for v in caddy_volumes if v.get("target") == "/etc/caddy/Caddyfile"]
    if len(matches) != 1 or matches[0].get("type") != "bind":
        raise ValueError("Expected exactly one original Caddyfile bind mount")
    matches[0].update(source=caddy_file, read_only=True)
    return {
        "name": base["name"],
        "services": {
            # Keep the original API image, but stop its older outbox publisher.
            # Otherwise a retry can enter the old worker despite its pipeline.
            "api": {"environment": {"POLARIS_AUTO_PROCESS_JOBS": "false"}},
            "detail-api": api,
            "detail-worker": worker,
            "dispatcher": {"image": backend_image, "pull_policy": "never"},
            "migrate": {"image": backend_image, "pull_policy": "never"},
            "frontend": {"image": frontend_image, "pull_policy": "never"},
            "caddy": {"volumes": caddy_volumes},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-image", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--caddy-file", required=True)
    parser.add_argument("--provider-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disable-new-native-creation", action="store_true")
    parser.add_argument("--worker-concurrency", type=int, default=6)
    args = parser.parse_args()
    result = overlay(
        json.load(sys.stdin),
        backend_image=args.backend_image,
        frontend_image=args.frontend_image,
        revision=args.revision,
        caddy_file=args.caddy_file,
        provider=json.loads(args.provider_json.read_bytes()),
        native_creation=not args.disable_new_native_creation,
        worker_concurrency=args.worker_concurrency,
    )
    if not args.output.is_absolute() or args.output.is_symlink():
        parser.error("The private server output must be an absolute, new regular file")
    raw = (json.dumps(result, indent=2) + "\n").encode()
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    # Never print the resolved source, private provider values or output body.
    print(
        json.dumps(
            {
                "prepared": True,
                "revision": args.revision,
                "native_creation": not args.disable_new_native_creation,
                "services": sorted(result["services"]),
                "deployed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
