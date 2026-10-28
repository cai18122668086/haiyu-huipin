#!/usr/bin/env python3
"""Fail-closed production-auth deployment preflight.

The checker reports variable names and structural failures only. It never prints
secret values or a rendered Compose configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path

from generate_production_env import (
    AUTHORIZATION_KEYS,
    IMAGE_KEYS,
    INFRA_SECRET_KEYS,
    PROVIDER_KEYS,
    load_env,
)

EXPECTED_SERVICES = {
    "postgres",
    "redis",
    "minio",
    "supertokens",
    "migrate",
    "api",
    "crawl-worker",
    "crawl-maintenance",
    "worker",
    "dispatcher",
    "frontend",
    "caddy",
}
EXPECTED_NETWORKS = {"edge", "backend", "ingress", "mail-egress", "provider-egress"}
EXPECTED_EXTERNAL_VOLUMES = {
    "postgres_data": "polaris-internal-staging_postgres_data",
    "redis_data": "polaris-internal-staging_redis_data",
    "minio_data": "polaris-internal-staging_minio_data",
}
EXPECTED_SERVICE_VOLUMES = {
    "postgres": "postgres_data:/var/lib/postgresql",
    "redis": "redis_data:/data",
    "minio": "minio_data:/data",
}
AURORA_WORK_VOLUME = "polaris_aurora_work_data:/app/.data/aurora-main-image"
AURORA_WORK_SERVICES = {"api", "worker", "dispatcher"}
EXPECTED_SERVICE_NETWORKS = {
    "postgres": {"backend"},
    "redis": {"backend"},
    "minio": {"backend"},
    "supertokens": {"backend"},
    "migrate": {"backend"},
    "api": {"edge", "backend", "mail-egress", "provider-egress"},
    "crawl-worker": {"backend", "provider-egress"},
    "crawl-maintenance": {"backend"},
    "worker": {"backend", "provider-egress"},
    "dispatcher": {"backend"},
    "frontend": {"edge"},
    "caddy": {"edge", "ingress"},
}
RELEASE_SHA = re.compile(r"^[0-9a-f]{40}$")
# Budget/quota values are per-environment cost policy, not release-correctness
# invariants: production intentionally caps tighter than the base env template.
# Drift on these keys is reported as a warning instead of blocking the deploy.
BUDGET_POLICY_KEYS = frozenset(
    {
        "POLARIS_TENGLI_SMOKE_BUDGET_USD",
        "POLARIS_TENGLI_DAILY_BUDGET_USD",
        "POLARIS_TENGLI_MONTHLY_BUDGET_USD",
        "POLARIS_TENGLI_DAILY_REQUEST_LIMIT",
    }
)
# Application execution mode is a reviewed release strategy, not a Provider
# credential or feature drift. It may change as part of a release.
DEPLOYMENT_STRATEGY_KEYS = frozenset({"POLARIS_AURORA_EXECUTION_MODE"})
ALLOWED_AURORA_EXECUTION_MODES = frozenset(
    {
        "sequential_v017",
        "sequential_v20",
        "parallel_v20",
        "parallel_v20_calibrated",
    }
)
DIGEST_REFERENCE = re.compile(
    r"^[a-z0-9][a-z0-9./_-]*(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$"
)
APP_REFERENCE = re.compile(r"^polaris/(backend|crawl-worker|frontend):([0-9a-f]{40})$")
MARKER = re.compile(r"<[^>]+>|replace-with|change-me|dev_only", re.IGNORECASE)
SUPERTOKENS_REFERENCE = (
    "supertokens/supertokens-postgresql:12.0.10@sha256:"
    "4516ec7c00b8fb2a773012694d8ebf03b54799d59cfc974160590f16d235ce72"
)
REQUIRED_ENV_KEYS = {
    "POLARIS_RELEASE_SHA",
    "POLARIS_BACKEND_IMAGE",
    "POLARIS_CRAWL_WORKER_IMAGE",
    "POLARIS_FRONTEND_IMAGE",
    *IMAGE_KEYS,
    "POLARIS_SUPERTOKENS_IMAGE",
    *INFRA_SECRET_KEYS,
    "POLARIS_SUPERTOKENS_API_KEY",
    "POLARIS_AUTH_SMTP_HOST",
    "POLARIS_AUTH_SMTP_PORT",
    "POLARIS_AUTH_SMTP_USERNAME",
    "POLARIS_AUTH_SMTP_PASSWORD",
    "POLARIS_AUTH_SMTP_SECURE",
    "POLARIS_AUTH_EMAIL_FROM_NAME",
    "POLARIS_AUTH_EMAIL_FROM_ADDRESS",
    "POLARIS_ACME_EMAIL",
    "POLARIS_S3_BUCKET",
    "POLARIS_AUTO_CREATE_SCHEMA",
    "POLARIS_STORAGE_BACKEND",
    "POLARIS_S3_AUTO_CREATE_BUCKET",
    "POLARIS_TASK_DISPATCHER",
    "POLARIS_ECOMMERCE_PROXY_ENABLED",
    *AUTHORIZATION_KEYS,
    *PROVIDER_KEYS,
}
SENSITIVE_MIN_LENGTH = {
    "POLARIS_POSTGRES_PASSWORD": 24,
    "POLARIS_REDIS_PASSWORD": 24,
    "POLARIS_MINIO_ROOT_PASSWORD": 24,
    "POLARIS_ACCESS_URL_SECRET": 32,
    "POLARIS_SUPERTOKENS_API_KEY": 32,
    "POLARIS_AUTH_SMTP_PASSWORD": 8,
}


def parse_mapping_blocks(text: str, heading: str) -> dict[str, str]:
    lines = text.splitlines()
    try:
        start = lines.index(f"{heading}:") + 1
    except ValueError:
        return {}
    result: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start:]:
        if line and not line.startswith(" "):
            break
        match = re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", line)
        if match:
            current = match.group(1)
            result[current] = [line]
        elif current is not None:
            result[current].append(line)
    return {name: "\n".join(block) for name, block in result.items()}


def service_networks(block: str) -> set[str]:
    inline = re.search(r"^    networks: \[([^]]+)\]$", block, re.MULTILINE)
    if inline:
        return {item.strip() for item in inline.group(1).split(",")}
    lines = block.splitlines()
    try:
        start = lines.index("    networks:") + 1
    except ValueError:
        return set()
    result: set[str] = set()
    for line in lines[start:]:
        match = re.fullmatch(r"      - ([a-z][a-z0-9_-]*)", line)
        if match:
            result.add(match.group(1))
            continue
        if line and not line.startswith("      "):
            break
    return result


def service_queues(block: str) -> set[str]:
    """Return queue names explicitly passed to a Celery service command."""

    queues: set[str] = set()
    for match in re.findall(r"--queues(?:=|\s+)([a-z0-9_,-]+)", block):
        queues.update(queue for queue in match.split(",") if queue)
    return queues


def validate_queue_topology(
    compose: str, *, expected: dict[str, set[str]]
) -> list[str]:
    """Fail closed when worker queue assignments drift from the task routes."""

    services = parse_mapping_blocks(compose, "services")
    errors: list[str] = []
    for service, expected_queues in expected.items():
        if service not in services:
            errors.append(f"{service} service is missing for queue topology validation")
            continue
        actual_queues = service_queues(services[service])
        if actual_queues != expected_queues:
            expected_text = ", ".join(sorted(expected_queues)) or "none"
            actual_text = ", ".join(sorted(actual_queues)) or "none"
            errors.append(
                f"{service} must listen only to queue(s) {expected_text}; found {actual_text}"
            )
    return errors


def validate_env(
    env: dict[str, str],
    base: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    missing = sorted(key for key in REQUIRED_ENV_KEYS if key not in env)
    if missing:
        errors.append("missing environment variables: " + ", ".join(missing))
    for key in sorted(REQUIRED_ENV_KEYS & env.keys()):
        if MARKER.search(env[key]):
            errors.append(f"{key} still contains a template or development marker")
    sha = env.get("POLARIS_RELEASE_SHA", "")
    if not RELEASE_SHA.fullmatch(sha):
        errors.append("POLARIS_RELEASE_SHA must be a 40-character lowercase Git SHA")
    for key in (
        "POLARIS_BACKEND_IMAGE",
        "POLARIS_CRAWL_WORKER_IMAGE",
        "POLARIS_FRONTEND_IMAGE",
    ):
        value = env.get(key, "")
        match = APP_REFERENCE.fullmatch(value)
        if not match or match.group(2) != sha:
            errors.append(
                f"{key} must be an immutable application image matching POLARIS_RELEASE_SHA"
            )
    for key in (*IMAGE_KEYS, "POLARIS_SUPERTOKENS_IMAGE"):
        if not DIGEST_REFERENCE.fullmatch(env.get(key, "")):
            errors.append(f"{key} must be pinned by sha256 digest")
    if env.get("POLARIS_SUPERTOKENS_IMAGE") != SUPERTOKENS_REFERENCE:
        errors.append(
            "POLARIS_SUPERTOKENS_IMAGE must be the approved Core v12.0.10 linux/amd64 manifest"
        )
    for key, minimum in SENSITIVE_MIN_LENGTH.items():
        if len(env.get(key, "")) < minimum:
            errors.append(f"{key} is shorter than the production minimum")
    if not re.fullmatch(r"[A-Za-z0-9=-]+", env.get("POLARIS_SUPERTOKENS_API_KEY", "")):
        errors.append(
            "POLARIS_SUPERTOKENS_API_KEY contains characters rejected by Core v12.0.10"
        )
    if env.get("POLARIS_AUTH_EMAIL_FROM_ADDRESS") != "no-reply@mail.tengliapi.xyz":
        errors.append(
            "POLARIS_AUTH_EMAIL_FROM_ADDRESS must be no-reply@mail.tengliapi.xyz"
        )
    admin_emails_value = env.get("POLARIS_ANNOUNCEMENT_ADMIN_EMAILS", "")
    try:
        admin_emails = json.loads(admin_emails_value)
    except json.JSONDecodeError:
        admin_emails = None
    if not isinstance(admin_emails, list) or not admin_emails:
        errors.append(
            "POLARIS_ANNOUNCEMENT_ADMIN_EMAILS must be a non-empty JSON array"
        )
    elif any(
        not isinstance(email, str)
        or email != email.strip().casefold()
        or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is None
        for email in admin_emails
    ) or len(set(admin_emails)) != len(admin_emails):
        errors.append(
            "POLARIS_ANNOUNCEMENT_ADMIN_EMAILS must contain unique normalized emails"
        )
    if "@" not in env.get("POLARIS_ACME_EMAIL", ""):
        errors.append("POLARIS_ACME_EMAIL must be a valid contact email")
    try:
        port = int(env.get("POLARIS_AUTH_SMTP_PORT", ""))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        errors.append("POLARIS_AUTH_SMTP_PORT must be between 1 and 65535")
    if env.get("POLARIS_AUTH_SMTP_SECURE", "").lower() not in {"true", "false"}:
        errors.append("POLARIS_AUTH_SMTP_SECURE must be true or false")
    for key, expected in (
        ("POLARIS_AUTO_CREATE_SCHEMA", "false"),
        ("POLARIS_STORAGE_BACKEND", "s3"),
        ("POLARIS_S3_AUTO_CREATE_BUCKET", "false"),
        ("POLARIS_TASK_DISPATCHER", "celery"),
        ("POLARIS_ECOMMERCE_PROXY_ENABLED", "false"),
    ):
        if env.get(key) != expected:
            errors.append(f"{key} must be exactly {expected} for this release")
    if env.get("POLARIS_AURORA_EXECUTION_MODE") not in ALLOWED_AURORA_EXECUTION_MODES:
        errors.append(
            "POLARIS_AURORA_EXECUTION_MODE must be one of "
            + ", ".join(sorted(ALLOWED_AURORA_EXECUTION_MODES))
        )
    boolean_keys = (
        *(key for key in PROVIDER_KEYS if key.endswith("_PROVIDER_ENABLED")),
        "POLARIS_PROVIDER_QUOTA_ENABLED",
        "POLARIS_WORKSPACE_CREDIT_ENFORCEMENT_ENABLED",
    )
    for key in boolean_keys:
        if env.get(key, "") not in {"true", "false"}:
            errors.append(f"{key} must be exactly true or false")
    if base is not None:
        changed_authorization = [
            key
            for key in AUTHORIZATION_KEYS
            if key in base and env.get(key) != base.get(key)
        ]
        if changed_authorization:
            errors.append(
                "Authorization settings changed during deployment: "
                + ", ".join(changed_authorization)
            )
        changed = [
            key
            for key in PROVIDER_KEYS
            if key in base and env.get(key) != base.get(key)
        ]
        blocking = [
            key
            for key in changed
            if key not in BUDGET_POLICY_KEYS and key not in DEPLOYMENT_STRATEGY_KEYS
        ]
        if blocking:
            errors.append(
                "Provider settings changed during auth cutover: " + ", ".join(blocking)
            )
        budget_changed = [key for key in changed if key in BUDGET_POLICY_KEYS]
        if budget_changed and warnings is not None:
            warnings.append(
                "Budget policy keys differ from base env (per-environment cost "
                "policy, non-blocking): " + ", ".join(budget_changed)
            )
        strategy_changed = [
            key for key in changed if key in DEPLOYMENT_STRATEGY_KEYS
        ]
        if strategy_changed and warnings is not None:
            warnings.append(
                "Deployment strategy keys differ from base env (reviewed release "
                "change): " + ", ".join(strategy_changed)
            )
    return errors


def validate_compose(compose: str) -> list[str]:
    errors: list[str] = []
    services = parse_mapping_blocks(compose, "services")
    if set(services) != EXPECTED_SERVICES:
        errors.append("production Compose service set is not the approved set")
    networks = parse_mapping_blocks(compose, "networks")
    if set(networks) != EXPECTED_NETWORKS:
        errors.append("production Compose network set is not the approved set")
    for service, expected in EXPECTED_SERVICE_NETWORKS.items():
        if service in services and service_networks(services[service]) != expected:
            errors.append(f"{service} has an unexpected network attachment")
    for service, block in services.items():
        if service != "caddy" and re.search(r"^    ports:", block, re.MULTILINE):
            errors.append(f"{service} must not publish host ports")
    caddy = services.get("caddy", "")
    for port in ('"80:80"', '"443:443"', '"443:443/udp"'):
        if port not in caddy:
            errors.append(f"caddy is missing production port {port}")
    supertokens = services.get("supertokens", "")
    for required in (
        "API_KEYS:",
        "POSTGRESQL_CONNECTION_URI:",
        "http://127.0.0.1:3567/hello",
    ):
        if required not in supertokens:
            errors.append(f"supertokens service is missing {required}")
    required_literals = (
        'POLARIS_ENVIRONMENT: production',
        'POLARIS_AUTH_MODE: external',
        'POLARIS_AUTO_CREATE_SCHEMA: "false"',
        'POLARIS_STORAGE_BACKEND: s3',
        'POLARIS_S3_AUTO_CREATE_BUCKET: "false"',
        'POLARIS_TASK_DISPATCHER: celery',
        'POLARIS_ECOMMERCE_PROXY_ENABLED: "false"',
        'HTTP_PROXY: ""',
        'HTTPS_PROXY: ""',
        'ALL_PROXY: ""',
        'NO_PROXY: "*"',
    )
    if any(literal not in compose for literal in required_literals):
        errors.append(
            "production Compose must force production/external authentication"
        )
    if 'POLARIS_SUPERTOKENS_COOKIE_SECURE: "true"' not in compose:
        errors.append("production Compose must force Secure authentication cookies")
    if "POLARIS_CORS_ORIGINS: '[\"https://www.tengliapi.xyz\"]'" not in compose:
        errors.append(
            "production Compose must use the exact same-origin CORS allowlist"
        )
    if "POLARIS_ANNOUNCEMENT_ADMIN_EMAILS:" not in compose:
        errors.append(
            "production Compose must require the announcement admin allowlist"
        )
    errors.extend(
        validate_queue_topology(
            compose,
            expected={"worker": {"default"}, "crawl-worker": {"crawl"}},
        )
    )
    volumes = parse_mapping_blocks(compose, "volumes")
    for volume, expected_name in EXPECTED_EXTERNAL_VOLUMES.items():
        block = volumes.get(volume, "")
        if not re.search(
            rf"^    name: {re.escape(expected_name)}$", block, re.MULTILINE
        ):
            errors.append(f"{volume} must reuse the approved existing data volume")
        if not re.search(r"^    external: true$", block, re.MULTILINE):
            errors.append(f"{volume} must remain an external volume")
    for service, expected_mount in EXPECTED_SERVICE_VOLUMES.items():
        block = services.get(service, "")
        if not re.search(
            rf"^      - {re.escape(expected_mount)}$", block, re.MULTILINE
        ):
            errors.append(f"{service} must mount the approved existing data volume")
    if "polaris_aurora_work_data" not in volumes:
        errors.append("production Compose is missing the Aurora work volume")
    ecommerce_profiles = volumes.get("polaris_ecommerce_profiles", "")
    if not re.search(r"^    name: polaris-production_ecommerce_profiles$", ecommerce_profiles, re.MULTILINE):
        errors.append("production Compose must use the approved ecommerce profile volume")
    if not re.search(r"^    external: true$", ecommerce_profiles, re.MULTILINE):
        errors.append("ecommerce profile volume must be provisioned externally")
    for service in ("api", "crawl-worker", "crawl-maintenance"):
        block = services.get(service, "")
        if not re.search(r"^      - polaris_ecommerce_profiles:/app/.data/ecommerce-session$", block, re.MULTILINE):
            errors.append(f"{service} must mount the shared ecommerce profile volume")
    for service in AURORA_WORK_SERVICES:
        block = services.get(service, "")
        if not re.search(
            rf"^      - {re.escape(AURORA_WORK_VOLUME)}$", block, re.MULTILINE
        ):
            errors.append(f"{service} must mount the shared Aurora work volume")
    if "POLARIS_AURORA_WORK_ROOT: /app/.data/aurora-main-image" not in compose:
        errors.append("production Compose must configure the Aurora work root")
    return errors


def dockerfile_copy_sources(dockerfile: str) -> list[tuple[str, list[str], str]]:
    logical_lines: list[str] = []
    pending = ""
    for raw_line in dockerfile.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical_lines.append(pending)
        pending = ""
    if pending:
        logical_lines.append(pending)

    result: list[tuple[str, list[str], str]] = []
    for line in logical_lines:
        match = re.match(r"^(COPY|ADD)\s+(.+)$", line, re.IGNORECASE)
        if not match:
            continue
        instruction = match.group(1).upper()
        payload = match.group(2).strip()
        while payload.startswith("--"):
            flag, separator, remainder = payload.partition(" ")
            if not separator or "=" not in flag:
                break
            payload = remainder.lstrip()
        try:
            arguments = (
                json.loads(payload) if payload.startswith("[") else shlex.split(payload)
            )
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(arguments, list) or len(arguments) < 2:
            continue
        result.append(
            (instruction, [str(item) for item in arguments[:-1]], str(arguments[-1]))
        )
    return result


def normalize_docker_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/")


def validate_backend_dockerfile(dockerfile: str) -> list[str]:
    errors: list[str] = []
    owner_source = "backend/scripts/assign_workspace_owner.py"
    owner_destination = "scripts/assign_workspace_owner.py"
    owner_packaged = False
    for instruction, sources, destination in dockerfile_copy_sources(dockerfile):
        normalized_destination = normalize_docker_path(destination)
        for source in sources:
            normalized_source = normalize_docker_path(source)
            if (
                instruction == "COPY"
                and normalized_source == owner_source
                and normalized_destination == owner_destination
            ):
                owner_packaged = True
            if normalized_source == "backend/scripts" or (
                normalized_source.startswith("backend/scripts/")
                and normalized_source != owner_source
            ):
                errors.append("backend image must not package other operator scripts")
    if not owner_packaged:
        errors.append(
            "backend image must package the explicit workspace owner assignment script"
        )
    return errors


def validate_crawl_worker_dockerfile(dockerfile: str) -> list[str]:
    errors: list[str] = []
    required_literals = (
        "ARG POLARIS_PYTHON_BASE_IMAGE",
        "FROM ${POLARIS_PYTHON_BASE_IMAGE}",
        "requirements.browser.lock",
        "--require-hashes",
        "playwright install --with-deps chromium",
        "chromium.launch(headless=True)",
        "USER 10001:10001",
    )
    for literal in required_literals:
        if literal not in dockerfile:
            errors.append(f"crawl-worker Dockerfile is missing {literal}")
    if "FROM python:" in dockerfile or "pip install '.[browser]'" in dockerfile:
        errors.append("crawl-worker image must not use floating or unlocked browser dependencies")
    errors.extend(validate_backend_dockerfile(dockerfile))
    return errors


def validate_caddy(caddy: str) -> list[str]:
    errors: list[str] = []
    for required in (
        "www.tengliapi.xyz {",
        "reverse_proxy api:8000",
        "reverse_proxy frontend:3000",
        "Strict-Transport-Security",
    ):
        if required not in caddy:
            errors.append(f"production Caddyfile is missing {required}")
    if "auto_https off" in caddy:
        errors.append("production Caddyfile must not disable automatic HTTPS")
    if "\ntengliapi.xyz {" in caddy:
        errors.append(
            "production Caddyfile must not claim the apex domain before its DNS is approved"
        )
    if ":8080" in caddy:
        errors.append("production Caddyfile must not use the internal-only :8080 site")
    csp_match = re.search(r'Content-Security-Policy "([^"]+)"', caddy)
    if not csp_match:
        errors.append("production Caddyfile is missing a parseable CSP")
    else:
        csp = csp_match.group(1)
        if "connect-src 'self'" not in csp:
            errors.append("production CSP must retain same-origin connections")
        allowed_loopback_bridge = {
            "http://localhost:8765/v1/enrollment",
            "http://127.0.0.1:8765/v1/enrollment",
        }
        http_sources = re.findall(r"https?://[^\s;]+", csp)
        for source in http_sources:
            if source not in allowed_loopback_bridge:
                errors.append("production CSP must not allow external HTTP origins")
        for source in sorted(allowed_loopback_bridge):
            if source not in http_sources:
                errors.append(f"production CSP must allow local Worker bridge source {source}")
    return errors


def validate_mode(path: Path) -> list[str]:
    if os.name == "nt":
        return []
    mode = stat.S_IMODE(path.stat().st_mode)
    return [] if mode == 0o600 else [f"env file mode must be 0600, found {mode:04o}"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--base-env", type=Path)
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=Path(__file__).with_name("compose.production.yml"),
    )
    parser.add_argument(
        "--caddyfile",
        type=Path,
        default=Path(__file__).with_name("Caddyfile.production"),
    )
    parser.add_argument(
        "--backend-dockerfile",
        type=Path,
        default=Path(__file__).with_name("backend.Dockerfile"),
    )
    parser.add_argument(
        "--crawl-worker-dockerfile",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "backend"
        / "Dockerfile.crawl-worker",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    warnings: list[str] = []
    try:
        env = load_env(args.env_file)
        base = load_env(args.base_env) if args.base_env else None
        errors = validate_env(env, base, warnings)
        errors.extend(validate_mode(args.env_file))
        errors.extend(validate_compose(args.compose_file.read_text(encoding="utf-8")))
        errors.extend(validate_caddy(args.caddyfile.read_text(encoding="utf-8")))
        errors.extend(
            validate_backend_dockerfile(
                args.backend_dockerfile.read_text(encoding="utf-8")
            )
        )
        errors.extend(
            validate_crawl_worker_dockerfile(
                args.crawl_worker_dockerfile.read_text(encoding="utf-8")
            )
        )
    except (OSError, ValueError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"BLOCKED: {error}", file=sys.stderr)
        return 2
    print(
        "PASS: production authentication deployment preflight completed without displaying secrets"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
