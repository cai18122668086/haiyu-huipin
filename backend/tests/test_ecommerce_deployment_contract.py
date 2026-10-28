from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_compose_keeps_domestic_crawl_on_dedicated_queue_and_profile_volume() -> None:
    compose = (BACKEND_ROOT.parent / "compose.yaml").read_text(encoding="utf-8")

    assert "crawl-worker:" in compose
    assert "crawl-maintenance:" in compose
    assert "--queues=crawl" in compose
    assert "--concurrency=1" in compose
    assert "polaris_ecommerce_profiles:/app/.data/ecommerce-session" in compose
    assert "POLARIS_TASK_DISPATCHER: celery" in compose
    assert "NO_PROXY: \"*\"" in compose
    assert "HTTP_PROXY: \"\"" in compose


def test_production_env_contract_is_fail_closed() -> None:
    env_file = (
        BACKEND_ROOT.parent / "deploy" / "production.env.example"
    ).read_text(encoding="utf-8")

    required = {
        "POLARIS_ENVIRONMENT=production",
        "POLARIS_AUTH_MODE=external",
        "POLARIS_AUTO_CREATE_SCHEMA=false",
        "POLARIS_STORAGE_BACKEND=s3",
        "POLARIS_S3_AUTO_CREATE_BUCKET=false",
        "POLARIS_TASK_DISPATCHER=celery",
        "POLARIS_ECOMMERCE_PROXY_ENABLED=false",
        "POLARIS_SUPERTOKENS_COOKIE_SECURE=true",
        "NO_PROXY=*",
    }
    assert required.issubset(set(env_file.splitlines()))
    assert "POLARIS_AMAZON" not in env_file


@pytest.mark.skipif(
    not (
        os.environ.get("POLARIS_POSTGRES_E2E_URL")
        and os.environ.get("POLARIS_POSTGRES_E2E_ALLOW_MUTATION") == "true"
    ),
    reason=("requires an explicitly supplied disposable PostgreSQL URL and "
        "POLARIS_POSTGRES_E2E_ALLOW_MUTATION=true"
    ),
)
def test_postgres_migrations_upgrade_downgrade_upgrade() -> None:
    """Exercise the full migration chain only against an explicit test DB."""

    environment = os.environ.copy()
    environment["POLARIS_DATABASE_URL"] = os.environ["POLARIS_POSTGRES_E2E_URL"]
    environment["POLARIS_AUTO_CREATE_SCHEMA"] = "false"
    commands = [
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "downgrade",
            "base",
        ],
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
    ]
    for command in commands:
        subprocess.run(
            command,
            cwd=BACKEND_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
