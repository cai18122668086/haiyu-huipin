#!/usr/bin/env python3
"""Create a server-local production environment without printing secrets.

Run this on the server. Existing infrastructure and Provider values are copied
from the current protected env file; authentication secrets are added through
hidden prompts or generated with the secrets module.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import stat
import sys
from pathlib import Path

RELEASE_SHA = re.compile(r"^[0-9a-f]{40}$")
SUPERTOKENS_IMAGE = (
    "supertokens/supertokens-postgresql:12.0.10@sha256:"
    "4516ec7c00b8fb2a773012694d8ebf03b54799d59cfc974160590f16d235ce72"
)
IMAGE_KEYS = (
    "POLARIS_PYTHON_BASE_IMAGE",
    "POLARIS_NODE_BASE_IMAGE",
    "POLARIS_POSTGRES_IMAGE",
    "POLARIS_REDIS_IMAGE",
    "POLARIS_MINIO_IMAGE",
    "POLARIS_CADDY_IMAGE",
)
INFRA_SECRET_KEYS = (
    "POLARIS_POSTGRES_PASSWORD",
    "POLARIS_REDIS_PASSWORD",
    "POLARIS_MINIO_ROOT_USER",
    "POLARIS_MINIO_ROOT_PASSWORD",
    "POLARIS_ACCESS_URL_SECRET",
)
AUTHORIZATION_KEYS = ("POLARIS_ANNOUNCEMENT_ADMIN_EMAILS",)
PROVIDER_KEYS = (
    "POLARIS_TASK_RUNNER",
    "POLARIS_PROVIDER_QUOTA_ENABLED",
    "POLARIS_WORKSPACE_CREDIT_ENFORCEMENT_ENABLED",
    "POLARIS_AURORA_EXECUTION_MODE",
    "POLARIS_AURORA_MAX_TRANSIENT_RETRIES",
    "POLARIS_AURORA_RETRY_BACKOFF_BASE_SECONDS",
    "POLARIS_AURORA_RETRY_BACKOFF_MAX_SECONDS",
    "POLARIS_PRODUCT_IMAGE_PROVIDER_ENABLED",
    "POLARIS_REFERENCE_REMIX_PROVIDER_ENABLED",
    "POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED",
    "POLARIS_CREATIVE_IMAGE_PROVIDER_ENABLED",
    "POLARIS_AMAZON_VISUAL_SUITE_PROVIDER_ENABLED",
    "POLARIS_DOMESTIC_MAIN_IMAGE_PROVIDER_ENABLED",
    "POLARIS_CROSSBORDER_DETAIL_PROVIDER_ENABLED",
    "POLARIS_TENGLI_API_KEY",
    "POLARIS_TENGLI_BASE_URL",
    "POLARIS_TENGLI_IMAGE_MODEL",
    "POLARIS_TENGLI_VISION_MODEL",
    "POLARIS_ANALYSIS_MODEL",
    "POLARIS_TENGLI_SMOKE_BUDGET_USD",
    "POLARIS_TENGLI_DAILY_BUDGET_USD",
    "POLARIS_TENGLI_MONTHLY_BUDGET_USD",
    "POLARIS_TENGLI_DAILY_REQUEST_LIMIT",
    "POLARIS_TENGLI_PLANNER_RESERVE_MICROS",
    "POLARIS_XINGHE_CONCURRENCY",
    "POLARIS_XINGHE_SKILL_ROOT",
)
DEFAULT_PROVIDER_VALUES = {
    "POLARIS_TASK_RUNNER": "mock",
    "POLARIS_PROVIDER_QUOTA_ENABLED": "true",
    "POLARIS_WORKSPACE_CREDIT_ENFORCEMENT_ENABLED": "false",
    "POLARIS_AURORA_EXECUTION_MODE": "sequential_v20",
    "POLARIS_AURORA_MAX_TRANSIENT_RETRIES": "2",
    "POLARIS_AURORA_RETRY_BACKOFF_BASE_SECONDS": "2",
    "POLARIS_AURORA_RETRY_BACKOFF_MAX_SECONDS": "30",
    "POLARIS_PRODUCT_IMAGE_PROVIDER_ENABLED": "false",
    "POLARIS_REFERENCE_REMIX_PROVIDER_ENABLED": "false",
    "POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED": "false",
    "POLARIS_CREATIVE_IMAGE_PROVIDER_ENABLED": "false",
    "POLARIS_AMAZON_VISUAL_SUITE_PROVIDER_ENABLED": "false",
    "POLARIS_DOMESTIC_MAIN_IMAGE_PROVIDER_ENABLED": "false",
    "POLARIS_CROSSBORDER_DETAIL_PROVIDER_ENABLED": "false",
    "POLARIS_TENGLI_API_KEY": "",
    "POLARIS_TENGLI_BASE_URL": "https://api.tengliapi.xyz:8443",
    "POLARIS_TENGLI_IMAGE_MODEL": "gpt-image-2",
    "POLARIS_TENGLI_VISION_MODEL": "gpt-5.6-terra",
    "POLARIS_ANALYSIS_MODEL": "gpt-5.6-sol",
    "POLARIS_TENGLI_SMOKE_BUDGET_USD": "200",
    "POLARIS_TENGLI_DAILY_BUDGET_USD": "0",
    "POLARIS_TENGLI_MONTHLY_BUDGET_USD": "0",
    "POLARIS_TENGLI_DAILY_REQUEST_LIMIT": "0",
    "POLARIS_TENGLI_PLANNER_RESERVE_MICROS": "250000",
    "POLARIS_XINGHE_CONCURRENCY": "3000",
    "POLARIS_XINGHE_SKILL_ROOT": "/app/skill_sources/xinghe-ecommerce-detail",
}


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return bytes(value[1:-1], "utf-8").decode("unicode_escape")
    return value


def load_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid env syntax at line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
            raise ValueError(f"invalid env key at line {line_number}")
        result[key] = _unquote(value)
    return result


def _random_secret(byte_count: int = 48) -> str:
    return secrets.token_urlsafe(byte_count)


def _random_supertokens_api_key() -> str:
    # Core v12.0.10 accepts only alphanumeric characters, hyphen, and equals.
    return secrets.token_hex(48)


def build_production_env(
    base: dict[str, str],
    *,
    release_sha: str,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    smtp_secure: bool,
    acme_email: str,
    supertokens_api_key: str | None = None,
) -> dict[str, str]:
    if not RELEASE_SHA.fullmatch(release_sha):
        raise ValueError(
            "release SHA must be exactly 40 lowercase hexadecimal characters"
        )
    for name, value in {
        "SMTP host": smtp_host,
        "SMTP username": smtp_username,
        "SMTP password": smtp_password,
        "ACME email": acme_email,
    }.items():
        if not value or "\n" in value or "\r" in value:
            raise ValueError(f"{name} is required and must be a single line")
    if not 1 <= smtp_port <= 65535:
        raise ValueError("SMTP port must be between 1 and 65535")
    if "@" not in acme_email:
        raise ValueError("ACME contact must be an email address")

    result: dict[str, str] = {}
    missing_images = [key for key in IMAGE_KEYS if not base.get(key)]
    if missing_images:
        raise ValueError(
            "base env is missing approved image references: "
            + ", ".join(missing_images)
        )
    for key in IMAGE_KEYS:
        result[key] = base[key]

    result.update(
        {
            "POLARIS_RELEASE_SHA": release_sha,
            "POLARIS_BACKEND_IMAGE": f"polaris/backend:{release_sha}",
            "POLARIS_CRAWL_WORKER_IMAGE": f"polaris/crawl-worker:{release_sha}",
            "POLARIS_FRONTEND_IMAGE": f"polaris/frontend:{release_sha}",
            "POLARIS_SUPERTOKENS_IMAGE": SUPERTOKENS_IMAGE,
        }
    )

    for key in INFRA_SECRET_KEYS:
        result[key] = base.get(key) or _random_secret()
    missing_authorization = [key for key in AUTHORIZATION_KEYS if not base.get(key)]
    if missing_authorization:
        raise ValueError(
            "base env is missing authorization configuration: "
            + ", ".join(missing_authorization)
        )
    for key in AUTHORIZATION_KEYS:
        result[key] = base[key]
    result["POLARIS_SUPERTOKENS_API_KEY"] = (
        supertokens_api_key or _random_supertokens_api_key()
    )

    result.update(
        {
            "POLARIS_AUTH_SMTP_HOST": smtp_host,
            "POLARIS_AUTH_SMTP_PORT": str(smtp_port),
            "POLARIS_AUTH_SMTP_USERNAME": smtp_username,
            "POLARIS_AUTH_SMTP_PASSWORD": smtp_password,
            "POLARIS_AUTH_SMTP_SECURE": "true" if smtp_secure else "false",
            "POLARIS_AUTH_EMAIL_FROM_NAME": "Polaris Commerce Studio",
            "POLARIS_AUTH_EMAIL_FROM_ADDRESS": "no-reply@mail.tengliapi.xyz",
            "POLARIS_ACME_EMAIL": acme_email,
            "POLARIS_S3_BUCKET": base.get("POLARIS_S3_BUCKET") or "polaris-production",
            "POLARIS_AUTO_CREATE_SCHEMA": "false",
            "POLARIS_STORAGE_BACKEND": "s3",
            "POLARIS_S3_AUTO_CREATE_BUCKET": "false",
            "POLARIS_TASK_DISPATCHER": "celery",
            # Domestic ecommerce is direct-only for this release. Preserve
            # the existing Provider feature switches from the server profile.
            "POLARIS_ECOMMERCE_PROXY_ENABLED": "false",
        }
    )
    for key in PROVIDER_KEYS:
        result[key] = base.get(key, DEFAULT_PROVIDER_VALUES[key])
    return result


def quote_env_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("env values must be single-line")
    if re.fullmatch(r"[A-Za-z0-9_./:@+,-]*", value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def render_env(values: dict[str, str]) -> str:
    groups = (
        (
            "Release and immutable images",
            (
                "POLARIS_RELEASE_SHA",
                "POLARIS_BACKEND_IMAGE",
                "POLARIS_CRAWL_WORKER_IMAGE",
                "POLARIS_FRONTEND_IMAGE",
                *IMAGE_KEYS,
                "POLARIS_SUPERTOKENS_IMAGE",
            ),
        ),
        ("Server-local secrets", (*INFRA_SECRET_KEYS, "POLARIS_SUPERTOKENS_API_KEY")),
        ("Authorization allowlists", AUTHORIZATION_KEYS),
        (
            "Transaction email and TLS",
            (
                "POLARIS_AUTH_SMTP_HOST",
                "POLARIS_AUTH_SMTP_PORT",
                "POLARIS_AUTH_SMTP_USERNAME",
                "POLARIS_AUTH_SMTP_PASSWORD",
                "POLARIS_AUTH_SMTP_SECURE",
                "POLARIS_AUTH_EMAIL_FROM_NAME",
                "POLARIS_AUTH_EMAIL_FROM_ADDRESS",
                "POLARIS_ACME_EMAIL",
            ),
        ),
        (
            "Storage and preserved Provider configuration",
            (
                "POLARIS_S3_BUCKET",
                "POLARIS_AUTO_CREATE_SCHEMA",
                "POLARIS_STORAGE_BACKEND",
                "POLARIS_S3_AUTO_CREATE_BUCKET",
                "POLARIS_TASK_DISPATCHER",
                "POLARIS_ECOMMERCE_PROXY_ENABLED",
                *PROVIDER_KEYS,
            ),
        ),
    )
    lines = [
        "# Generated on the production server. Never copy this file into Git, logs,",
        "# support bundles, command output, or chat. Required mode: 0600.",
        "",
    ]
    for title, keys in groups:
        lines.append(f"# {title}")
        for key in keys:
            lines.append(f"{key}={quote_env_value(values[key])}")
        lines.append("")
    return "\n".join(lines)


def write_env_file(path: Path, values: dict[str, str], *, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_env(values))
    except (OSError, ValueError):
        try:
            path.unlink(missing_ok=True)
        finally:
            raise
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _prompt_required(label: str, *, hidden: bool = False) -> str:
    prompt = getpass.getpass if hidden else input
    while True:
        value = prompt(f"{label}: ").strip()
        if value:
            return value
        print(f"{label} is required.", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-env", type=Path, default=Path("/etc/polaris/polaris.env")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("/etc/polaris/polaris.production.env")
    )
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.base_env.is_file():
        print("BLOCKED: protected base env file does not exist", file=sys.stderr)
        return 2
    try:
        base = load_env(args.base_env)
        smtp_host = _prompt_required("SMTP host")
        port_raw = input("SMTP port [587]: ").strip() or "587"
        smtp_username = _prompt_required("SMTP username")
        smtp_password = _prompt_required("SMTP password", hidden=True)
        secure_raw = (
            input("Implicit TLS (normally true only for port 465) [false]: ")
            .strip()
            .lower()
        )
        if secure_raw not in {"", "false", "no", "n", "true", "yes", "y"}:
            raise ValueError("implicit TLS answer must be true/false or yes/no")
        smtp_secure = secure_raw in {"true", "yes", "y"}
        acme_email = _prompt_required("ACME operations email")
        values = build_production_env(
            base,
            release_sha=args.release_sha,
            smtp_host=smtp_host,
            smtp_port=int(port_raw),
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            smtp_secure=smtp_secure,
            acme_email=acme_email,
        )
        write_env_file(args.output, values, force=args.force)
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(
        f"Created protected production env file at {args.output} (values not displayed)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
