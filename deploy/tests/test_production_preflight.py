from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))

from generate_production_env import (
    IMAGE_KEYS,
    PROVIDER_KEYS,
    SUPERTOKENS_IMAGE,
    load_env,
)
from production_preflight import (
    BUDGET_POLICY_KEYS,
    parse_args,
    validate_backend_dockerfile,
    validate_caddy,
    validate_compose,
    validate_crawl_worker_dockerfile,
    validate_env,
    validate_queue_topology,
)


class ProductionPreflightTests(unittest.TestCase):
    def valid_env(self) -> dict[str, str]:
        sha = "b" * 40
        digest = "a" * 64
        env = {
            "POLARIS_RELEASE_SHA": sha,
            "POLARIS_BACKEND_IMAGE": f"polaris/backend:{sha}",
            "POLARIS_CRAWL_WORKER_IMAGE": f"polaris/crawl-worker:{sha}",
            "POLARIS_FRONTEND_IMAGE": f"polaris/frontend:{sha}",
            "POLARIS_SUPERTOKENS_IMAGE": SUPERTOKENS_IMAGE,
            "POLARIS_POSTGRES_PASSWORD": "p" * 32,
            "POLARIS_REDIS_PASSWORD": "r" * 32,
            "POLARIS_MINIO_ROOT_USER": "minio-production-user",
            "POLARIS_MINIO_ROOT_PASSWORD": "m" * 32,
            "POLARIS_ACCESS_URL_SECRET": "a" * 48,
            "POLARIS_SUPERTOKENS_API_KEY": "s" * 64,
            "POLARIS_AUTH_SMTP_HOST": "smtp.example.com",
            "POLARIS_AUTH_SMTP_PORT": "587",
            "POLARIS_AUTH_SMTP_USERNAME": "smtp-user",
            "POLARIS_AUTH_SMTP_PASSWORD": "smtp-password",
            "POLARIS_AUTH_SMTP_SECURE": "false",
            "POLARIS_AUTH_EMAIL_FROM_NAME": "Polaris Commerce Studio",
            "POLARIS_AUTH_EMAIL_FROM_ADDRESS": "no-reply@mail.tengliapi.xyz",
            "POLARIS_ANNOUNCEMENT_ADMIN_EMAILS": '["admin@example.com"]',
            "POLARIS_ACME_EMAIL": "ops@tengliapi.xyz",
            "POLARIS_S3_BUCKET": "polaris-production",
            "POLARIS_AUTO_CREATE_SCHEMA": "false",
            "POLARIS_STORAGE_BACKEND": "s3",
            "POLARIS_S3_AUTO_CREATE_BUCKET": "false",
            "POLARIS_TASK_DISPATCHER": "celery",
            "POLARIS_ECOMMERCE_PROXY_ENABLED": "false",
        }
        env.update(
            {
                key: f"example/{key.lower()}:approved@sha256:{digest}"
                for key in IMAGE_KEYS
            }
        )
        defaults = {
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
            "POLARIS_XINGHE_CONCURRENCY": "6",
            "POLARIS_XINGHE_SKILL_ROOT": "/app/skill_sources/xinghe-ecommerce-detail",
        }
        env.update(defaults)
        return env

    def test_load_env_accepts_lowercase_proxy_keys(self) -> None:
        env_path = DEPLOY / "production.env.example"
        env = load_env(env_path)
        self.assertEqual(env["http_proxy"], "")
        self.assertEqual(env["no_proxy"], "*")

    def test_preflight_cli_defaults_to_crawl_worker_dockerfile(self) -> None:
        with patch.object(sys, "argv", ["production_preflight.py", "--env-file", "env"]):
            args = parse_args()
        self.assertEqual(
            args.crawl_worker_dockerfile,
            DEPLOY.parent / "backend" / "Dockerfile.crawl-worker",
        )

    def test_repository_production_files_pass_static_checks(self) -> None:
        compose = (DEPLOY / "compose.production.yml").read_text(encoding="utf-8")
        caddy = (DEPLOY / "Caddyfile.production").read_text(encoding="utf-8")
        dockerfile = (DEPLOY / "backend.Dockerfile").read_text(encoding="utf-8")
        crawl_dockerfile = (DEPLOY.parent / "backend" / "Dockerfile.crawl-worker").read_text(encoding="utf-8")
        self.assertEqual(validate_compose(compose), [])
        self.assertEqual(validate_caddy(caddy), [])
        self.assertEqual(validate_backend_dockerfile(dockerfile), [])
        self.assertEqual(validate_crawl_worker_dockerfile(crawl_dockerfile), [])
        self.assertEqual(validate_env(self.valid_env()), [])
        for literal in (
            'POLARIS_S3_AUTO_CREATE_BUCKET: "false"',
            'POLARIS_ECOMMERCE_PROXY_ENABLED: "false"',
            'polaris-production_ecommerce_profiles',
            'queues=crawl',
            '--queues=default',
            'HTTP_PROXY: ""',
            'NO_PROXY: "*"',
        ):
            self.assertIn(literal, compose)

    def test_production_csp_allows_both_local_worker_bridge_hosts(self) -> None:
        caddy = (DEPLOY / "Caddyfile.production").read_text(encoding="utf-8")
        for origin in ("http://localhost:8765", "http://127.0.0.1:8765"):
            self.assertIn(origin, caddy)

    def test_production_csp_rejects_missing_local_worker_bridge_host(self) -> None:
        caddy = (DEPLOY / "Caddyfile.production").read_text(encoding="utf-8")
        mutated = caddy.replace(" http://127.0.0.1:8765/v1/enrollment", "")
        errors = validate_caddy(mutated)
        self.assertTrue(any("127.0.0.1:8765" in item for item in errors))

    def test_production_csp_rejects_external_http_source(self) -> None:
        caddy = (DEPLOY / "Caddyfile.production").read_text(encoding="utf-8")
        mutated = caddy.replace(
            "connect-src 'self'",
            "connect-src 'self' http://example.invalid",
            1,
        )
        errors = validate_caddy(mutated)
        self.assertTrue(any("external HTTP origins" in item for item in errors))

    def test_repository_compose_files_use_upstream_image_capacity(self) -> None:
        compose_files = (
            DEPLOY.parent / "compose.yaml",
            DEPLOY / "compose.internal.yml",
            DEPLOY / "compose.production.yml",
        )
        for compose_file in compose_files:
            with self.subTest(compose_file=compose_file.name):
                compose = compose_file.read_text(encoding="utf-8")
                self.assertIn("POLARIS_XINGHE_CONCURRENCY:-3000", compose)
                self.assertIn("--concurrency=${POLARIS_WORKER_CONCURRENCY:-3000}", compose)
                self.assertNotIn("POLARIS_XINGHE_CONCURRENCY:-2", compose)
                self.assertNotIn("--concurrency=2", compose)

    def test_env_generator_tracks_every_compose_provider_flag(self) -> None:
        compose = (DEPLOY / "compose.production.yml").read_text(encoding="utf-8")
        compose_provider_keys = set(
            re.findall(r"\$\{(POLARIS_[A-Z0-9_]+_PROVIDER_ENABLED)(?=[:}])", compose)
        )
        generated_provider_keys = {
            key for key in PROVIDER_KEYS if key.endswith("_PROVIDER_ENABLED")
        }
        self.assertEqual(generated_provider_keys, compose_provider_keys)
        self.assertIn(
            "POLARIS_AMAZON_VISUAL_SUITE_PROVIDER_ENABLED:-false",
            compose,
        )

    def test_shared_data_volume_drift_is_rejected(self) -> None:
        compose = (DEPLOY / "compose.production.yml").read_text(encoding="utf-8")
        mutations = (
            compose.replace(
                "polaris-internal-staging_redis_data",
                "polaris-production_redis_data",
            ),
            compose.replace(
                "polaris-internal-staging_minio_data",
                "polaris-production_minio_data",
            ),
            compose.replace(
                "  redis_data:\n    name: polaris-internal-staging_redis_data\n    external: true",
                "  redis_data:\n    name: polaris-internal-staging_redis_data",
            ),
            compose.replace("- minio_data:/data", "- minio_data:/srv/minio"),
        )
        for mutated in mutations:
            with self.subTest():
                self.assertTrue(validate_compose(mutated))

    def test_owner_script_packaging_is_required_and_narrow(self) -> None:
        owner_copy = (
            "COPY backend/scripts/assign_workspace_owner.py "
            "./scripts/assign_workspace_owner.py\n"
        )
        self.assertTrue(validate_backend_dockerfile("FROM scratch\n"))
        forbidden_copies = (
            "COPY backend/scripts ./scripts\n",
            "COPY backend/scripts/ ./scripts/\n",
            "ADD backend/scripts ./scripts\n",
            'COPY ["backend/scripts", "./scripts"]\n',
            "COPY backend/scripts/export_openapi.py ./scripts/export_openapi.py\n",
        )
        for forbidden in forbidden_copies:
            with self.subTest(forbidden=forbidden):
                self.assertTrue(validate_backend_dockerfile(forbidden + owner_copy))

    def test_supertokens_api_key_rejects_underscore(self) -> None:
        env = self.valid_env()
        env["POLARIS_SUPERTOKENS_API_KEY"] = "a" * 40 + "_"
        errors = validate_env(env)
        self.assertTrue(any("characters rejected" in error for error in errors))

    def test_announcement_admin_allowlist_is_required_and_normalized(self) -> None:
        invalid_values = (
            "[]",
            '"admin@example.com"',
            '["Admin@example.com"]',
            '["admin@example.com", "admin@example.com"]',
            '["not-an-email"]',
        )
        for value in invalid_values:
            with self.subTest(value=value):
                env = self.valid_env()
                env["POLARIS_ANNOUNCEMENT_ADMIN_EMAILS"] = value
                self.assertTrue(
                    any(
                        "POLARIS_ANNOUNCEMENT_ADMIN_EMAILS" in error
                        for error in validate_env(env)
                    )
                )

    def test_template_markers_and_short_secrets_fail(self) -> None:
        env = self.valid_env()
        env["POLARIS_SUPERTOKENS_API_KEY"] = "<GENERATE_ON_SERVER>"
        errors = validate_env(env)
        self.assertTrue(any("POLARIS_SUPERTOKENS_API_KEY" in error for error in errors))

    def test_boolean_provider_controls_are_strict(self) -> None:
        invalid_values = ("yes", "1", "TRUE", "")
        keys = (
            *(key for key in PROVIDER_KEYS if key.endswith("_PROVIDER_ENABLED")),
            "POLARIS_PROVIDER_QUOTA_ENABLED",
            "POLARIS_WORKSPACE_CREDIT_ENFORCEMENT_ENABLED",
        )
        for key in keys:
            for invalid in invalid_values:
                with self.subTest(key=key, invalid=invalid):
                    env = self.valid_env()
                    env[key] = invalid
                    errors = validate_env(env)
                    self.assertIn(f"{key} must be exactly true or false", errors)

    def test_provider_drift_is_detected_without_values(self) -> None:
        env = self.valid_env()
        base = {key: env[key] for key in PROVIDER_KEYS}
        env["POLARIS_TASK_RUNNER"] = "tengli"
        errors = validate_env(env, base)
        self.assertIn(
            "Provider settings changed during auth cutover: POLARIS_TASK_RUNNER", errors
        )

    def test_execution_mode_drift_is_allowed_as_reviewed_strategy(self) -> None:
        env = self.valid_env()
        base = {key: env[key] for key in PROVIDER_KEYS}
        base["POLARIS_AURORA_EXECUTION_MODE"] = "parallel_v20_calibrated"
        warnings: list[str] = []
        errors = validate_env(env, base, warnings)
        self.assertEqual(errors, [])
        self.assertTrue(any("POLARIS_AURORA_EXECUTION_MODE" in warning for warning in warnings))

    def test_budget_policy_drift_warns_without_blocking(self) -> None:
        env = self.valid_env()
        base = {key: env[key] for key in PROVIDER_KEYS}
        for key in BUDGET_POLICY_KEYS:
            env[key] = "999999"
        warnings: list[str] = []
        errors = validate_env(env, base, warnings)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        for key in sorted(BUDGET_POLICY_KEYS):
            self.assertIn(key, warnings[0])

    def test_budget_policy_warning_does_not_mask_provider_drift(self) -> None:
        env = self.valid_env()
        base = {key: env[key] for key in PROVIDER_KEYS}
        env["POLARIS_TENGLI_DAILY_BUDGET_USD"] = "999999"
        env["POLARIS_TASK_RUNNER"] = "tengli"
        warnings: list[str] = []
        errors = validate_env(env, base, warnings)
        self.assertIn(
            "Provider settings changed during auth cutover: POLARIS_TASK_RUNNER", errors
        )
        self.assertTrue(
            any("POLARIS_TENGLI_DAILY_BUDGET_USD" in warning for warning in warnings)
        )

    def test_budget_policy_keys_are_provider_keys(self) -> None:
        self.assertTrue(BUDGET_POLICY_KEYS.issubset(set(PROVIDER_KEYS)))

    def test_production_storage_and_domestic_policy_are_fail_closed(self) -> None:
        env = self.valid_env()
        for key, value in (
            ("POLARIS_S3_AUTO_CREATE_BUCKET", "true"),
            ("POLARIS_ECOMMERCE_PROXY_ENABLED", "true"),
        ):
            with self.subTest(key=key):
                env[key] = value
                errors = validate_env(env)
                self.assertTrue(any(key in error for error in errors))

    def test_crawl_worker_dockerfile_rejects_unlocked_runtime(self) -> None:
        dockerfile = (DEPLOY.parent / "backend" / "Dockerfile.crawl-worker").read_text(encoding="utf-8")
        self.assertEqual(validate_crawl_worker_dockerfile(dockerfile), [])
        mutations = (
            dockerfile.replace("ARG POLARIS_PYTHON_BASE_IMAGE", "FROM python:3.13.14-slim"),
            dockerfile.replace("python -m pip install --require-hashes", "python -m pip install '.[browser]'"),
            dockerfile.replace("requirements.browser.lock", "requirements.txt"),
        )
        for mutated in mutations:
            with self.subTest():
                self.assertTrue(validate_crawl_worker_dockerfile(mutated))

    def test_production_compose_requires_separate_worker_queues(self) -> None:
        compose = (DEPLOY / "compose.production.yml").read_text(encoding="utf-8")
        self.assertEqual(validate_queue_topology(
            compose, expected={"worker": {"default"}, "crawl-worker": {"crawl"}}
        ), [])

        mutations = (
            compose.replace("--queues=default", "--queues=crawl", 1),
            compose.replace("--queues=crawl", "--queues=default", 1),
            compose.replace(
                "--queues=default",
                "--queues=default,crawl",
                1,
            ),
        )
        for mutated in mutations:
            with self.subTest():
                errors = validate_queue_topology(
                    mutated, expected={"worker": {"default"}, "crawl-worker": {"crawl"}}
                )
                self.assertTrue(any("queue(s)" in error for error in errors))

    def test_production_compose_rejects_bucket_autocreation(self) -> None:
        compose = (DEPLOY / "compose.production.yml").read_text(encoding="utf-8")
        mutated = compose.replace(
            'POLARIS_S3_AUTO_CREATE_BUCKET: "false"',
            'POLARIS_S3_AUTO_CREATE_BUCKET: "true"',
        )
        self.assertTrue(validate_compose(mutated))

    def test_ci_builds_all_release_runtime_images(self) -> None:
        ci = (DEPLOY.parent / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        for dockerfile in (
            "deploy/backend.Dockerfile",
            "backend/Dockerfile.crawl-worker",
            "deploy/frontend.Dockerfile",
        ):
            with self.subTest(dockerfile=dockerfile):
                self.assertIn(dockerfile, ci)
        self.assertIn("docker build", ci)
        self.assertIn('DOCKER_BUILDKIT: "1"', ci)
        self.assertNotIn("docker/build-push-action", ci)
        self.assertNotIn("docker/setup-buildx-action", ci)
        self.assertIn("POLARIS_CRAWL_WORKER_IMAGE: polaris/crawl-worker:ci", ci)
        self.assertIn(
            "docker compose -f deploy/compose.production.yml config --quiet", ci
        )

    def test_announcement_admin_drift_is_detected_without_values(self) -> None:
        env = self.valid_env()
        base = {
            "POLARIS_ANNOUNCEMENT_ADMIN_EMAILS": env[
                "POLARIS_ANNOUNCEMENT_ADMIN_EMAILS"
            ]
        }
        env["POLARIS_ANNOUNCEMENT_ADMIN_EMAILS"] = '["other@example.com"]'

        errors = validate_env(env, base)

        self.assertIn(
            "Authorization settings changed during deployment: "
            "POLARIS_ANNOUNCEMENT_ADMIN_EMAILS",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
