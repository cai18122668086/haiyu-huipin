from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))

from generate_production_env import (
    IMAGE_KEYS,
    SUPERTOKENS_IMAGE,
    build_production_env,
    load_env,
    render_env,
    write_env_file,
)


class ProductionEnvGeneratorTests(unittest.TestCase):
    def base_env(self) -> dict[str, str]:
        digest = "a" * 64
        base = {
            key: f"example/{key.lower()}:approved@sha256:{digest}" for key in IMAGE_KEYS
        }
        base.update(
            {
                "POLARIS_POSTGRES_PASSWORD": "existing-postgres-secret-123456789",
                "POLARIS_REDIS_PASSWORD": "existing-redis-secret-123456789012",
                "POLARIS_MINIO_ROOT_USER": "existing-minio-user",
                "POLARIS_MINIO_ROOT_PASSWORD": "existing-minio-secret-123456789012",
                "POLARIS_ACCESS_URL_SECRET": "existing-access-secret-12345678901234567890",
                "POLARIS_ANNOUNCEMENT_ADMIN_EMAILS": '["admin@example.com"]',
                "POLARIS_S3_BUCKET": "existing-assets",
                "POLARIS_TASK_RUNNER": "tengli",
                "POLARIS_AURORA_EXECUTION_MODE": "sequential_v20",
                "POLARIS_AURORA_MAX_TRANSIENT_RETRIES": "2",
                "POLARIS_AURORA_RETRY_BACKOFF_BASE_SECONDS": "2",
                "POLARIS_AURORA_RETRY_BACKOFF_MAX_SECONDS": "30",
                "POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED": "true",
                "POLARIS_AMAZON_VISUAL_SUITE_PROVIDER_ENABLED": "true",
                "POLARIS_TENGLI_API_KEY": "existing-provider-key",
            }
        )
        return base

    def test_build_preserves_infrastructure_and_provider_values(self) -> None:
        sha = "b" * 40
        base = self.base_env()
        values = build_production_env(
            base,
            release_sha=sha,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="smtp-user",
            smtp_password="smtp-secret",
            smtp_secure=False,
            acme_email="ops@tengliapi.xyz",
            supertokens_api_key="s" * 64,
        )
        self.assertEqual(
            values["POLARIS_POSTGRES_PASSWORD"], base["POLARIS_POSTGRES_PASSWORD"]
        )
        self.assertEqual(values["POLARIS_TASK_RUNNER"], "tengli")
        self.assertEqual(values["POLARIS_AURORA_EXECUTION_MODE"], "sequential_v20")
        self.assertEqual(values["POLARIS_AURORA_MAX_TRANSIENT_RETRIES"], "2")
        self.assertEqual(values["POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED"], "true")
        self.assertEqual(values["POLARIS_AMAZON_VISUAL_SUITE_PROVIDER_ENABLED"], "true")
        self.assertEqual(values["POLARIS_TENGLI_API_KEY"], "existing-provider-key")
        self.assertEqual(values["POLARIS_SUPERTOKENS_IMAGE"], SUPERTOKENS_IMAGE)
        self.assertEqual(
            values["POLARIS_AUTH_EMAIL_FROM_ADDRESS"], "no-reply@mail.tengliapi.xyz"
        )
        self.assertEqual(values["POLARIS_BACKEND_IMAGE"], f"polaris/backend:{sha}")
        self.assertEqual(
            values["POLARIS_ANNOUNCEMENT_ADMIN_EMAILS"],
            base["POLARIS_ANNOUNCEMENT_ADMIN_EMAILS"],
        )

    def test_build_requires_announcement_admin_allowlist(self) -> None:
        base = self.base_env()
        base.pop("POLARIS_ANNOUNCEMENT_ADMIN_EMAILS")
        with self.assertRaisesRegex(ValueError, "authorization configuration"):
            build_production_env(
                base,
                release_sha="f" * 40,
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_username="smtp-user",
                smtp_password="smtp-secret",
                smtp_secure=False,
                acme_email="ops@tengliapi.xyz",
                supertokens_api_key="k" * 64,
            )

    def test_build_defaults_model_pose_provider_to_disabled_when_missing(self) -> None:
        base = self.base_env()
        base.pop("POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED")
        values = build_production_env(
            base,
            release_sha="e" * 40,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="smtp-user",
            smtp_password="smtp-secret",
            smtp_secure=False,
            acme_email="ops@tengliapi.xyz",
            supertokens_api_key="k" * 64,
        )
        self.assertEqual(
            values["POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED"], "false"
        )

    def test_render_and_load_round_trip_special_smtp_password(self) -> None:
        values = build_production_env(
            self.base_env(),
            release_sha="c" * 40,
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="user@example.com",
            smtp_password="space dollar$ hash# quote' slash\\",
            smtp_secure=True,
            acme_email="ops@tengliapi.xyz",
            supertokens_api_key="k" * 64,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "production.env"
            path.write_text(render_env(values), encoding="utf-8")
            loaded = load_env(path)
        self.assertEqual(loaded, values)

    def test_writer_refuses_overwrite_and_uses_private_mode(self) -> None:
        values = build_production_env(
            self.base_env(),
            release_sha="d" * 40,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="smtp-user",
            smtp_password="smtp-secret",
            smtp_secure=False,
            acme_email="ops@tengliapi.xyz",
            supertokens_api_key="k" * 64,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "production.env"
            write_env_file(path, values)
            with self.assertRaises(FileExistsError):
                write_env_file(path, values)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
