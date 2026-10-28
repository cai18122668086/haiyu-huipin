from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "preflight.py"
SPEC = importlib.util.spec_from_file_location("polaris_deploy_preflight", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


class SourcePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]

    def test_repository_sources_pass(self) -> None:
        errors, totals = PREFLIGHT.validate_source(self.repo_root)
        self.assertEqual(errors, [])
        self.assertEqual(totals, (10288, 4.45))

    def test_product_lock_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "backend").mkdir()
            blockers = PREFLIGHT.lock_blockers(target)
            self.assertEqual(blockers, ["backend/requirements.lock is absent"])

    def test_repository_product_lock_passes(self) -> None:
        self.assertEqual(PREFLIGHT.lock_blockers(self.repo_root), [])

    def test_tag_only_image_is_rejected(self) -> None:
        errors: list[str] = []
        PREFLIGHT.validate_digest_reference(
            "POLARIS_CADDY_IMAGE", "caddy:2.10.2-alpine", errors
        )
        self.assertTrue(errors)

    def test_placeholder_digest_is_rejected(self) -> None:
        errors: list[str] = []
        PREFLIGHT.validate_digest_reference(
            "POLARIS_CADDY_IMAGE",
            "caddy:2.10.2-alpine@sha256:" + ("0" * 64),
            errors,
        )
        self.assertTrue(errors)

    def test_allowed_task_runners_are_explicit(self) -> None:
        self.assertEqual(PREFLIGHT.ALLOWED_TASK_RUNNERS, {"mock", "tengli"})

    def test_provider_feature_switches_are_explicit(self) -> None:
        self.assertEqual(
            PREFLIGHT.PROVIDER_FLAG_KEYS,
            (
                "POLARIS_PRODUCT_IMAGE_PROVIDER_ENABLED",
                "POLARIS_REFERENCE_REMIX_PROVIDER_ENABLED",
                "POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED",
                "POLARIS_CREATIVE_IMAGE_PROVIDER_ENABLED",
                "POLARIS_AMAZON_VISUAL_SUITE_PROVIDER_ENABLED",
                "POLARIS_DOMESTIC_MAIN_IMAGE_PROVIDER_ENABLED",
                "POLARIS_CROSSBORDER_DETAIL_PROVIDER_ENABLED",
            ),
        )

    def write_fixture(self, target: Path, filename: str, transform: object) -> None:
        (target / "deploy").mkdir()
        (target / "backend").mkdir()
        (target / ".dockerignore").write_text(
            ".env\n**/.env\n*.pem\n*.key\n", encoding="utf-8"
        )
        (target / "backend" / "requirements.lock").write_text(
            (self.repo_root / "backend" / "requirements.lock").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        backend_env_text = (self.repo_root / "backend" / ".env.example").read_text(
            encoding="utf-8"
        )
        if filename == "backend/.env.example":
            self.assertTrue(callable(transform))
            backend_env_text = transform(backend_env_text)
        (target / "backend" / ".env.example").write_text(
            backend_env_text, encoding="utf-8"
        )
        source_deploy = self.repo_root / "deploy"
        package_text = (self.repo_root / "package.json").read_text(encoding="utf-8")
        if filename == "package.json":
            self.assertTrue(callable(transform))
            package_text = transform(package_text)
        (target / "package.json").write_text(package_text, encoding="utf-8")
        for name in (
            ".env.internal.example",
            "Caddyfile",
            "README.md",
            "backend.Dockerfile",
            "compose.internal.yml",
            "frontend.Dockerfile",
        ):
            text = (source_deploy / name).read_text(encoding="utf-8")
            if name == filename:
                self.assertTrue(callable(transform))
                text = transform(text)
            (target / "deploy" / name).write_text(text, encoding="utf-8")

    def validate_mutation(self, filename: str, transform: object) -> list[str]:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            self.write_fixture(target, filename, transform)
            errors, _ = PREFLIGHT.validate_source(target)
            return errors

    def test_internal_worker_requires_default_queue(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "      - --queues=default", "      - --queues=crawl", 1
            ),
        )
        self.assertTrue(any("worker must listen only to queue(s) default" in item for item in errors))

    def test_non_loopback_port_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                '      - "127.0.0.1:8080:8080"',
                '      - "0.0.0.0:8080:8080"',
            ),
        )
        self.assertTrue(any("sole 127.0.0.1" in item for item in errors))

    def test_additional_port_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "  postgres:\n",
                '  postgres:\n    ports:\n      - "127.0.0.1:5432:5432"\n',
                1,
            ),
        )
        self.assertTrue(any("exactly one service" in item for item in errors))

    def test_xinghe_skill_root_uses_packaged_runtime(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "${POLARIS_XINGHE_SKILL_ROOT:-/app/skill_sources/"
                "xinghe-ecommerce-detail}",
                "/root/.codex/skills/xinghe-ecommerce-detail",
            ),
        )
        self.assertTrue(any("packaged runtime default" in item for item in errors))

    def test_provider_runner_expression_is_fail_closed(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "POLARIS_TASK_RUNNER: ${POLARIS_TASK_RUNNER:-mock}",
                "POLARIS_TASK_RUNNER: paid",
                1,
            ),
        )
        self.assertTrue(any("environment-selectable" in item for item in errors))

    def test_provider_quota_expression_is_fail_closed(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "POLARIS_PROVIDER_QUOTA_ENABLED: "
                "${POLARIS_PROVIDER_QUOTA_ENABLED:-false}",
                "POLARIS_PROVIDER_QUOTA_ENABLED: true",
                1,
            ),
        )
        self.assertTrue(any("Provider quota" in item for item in errors))

    def test_provider_switch_defaults_are_fail_closed(self) -> None:
        for key in PREFLIGHT.PROVIDER_FLAG_KEYS:
            with self.subTest(key=key):
                errors = self.validate_mutation(
                    "compose.internal.yml",
                    lambda text, key=key: text.replace(
                        f"{key}: ${{{key}:-false}}",
                        f"{key}: ${{{key}:-true}}",
                        1,
                    ),
                )
                self.assertTrue(any(key in item for item in errors))

    def test_provider_switch_examples_default_false(self) -> None:
        for filename in ("backend/.env.example", ".env.internal.example"):
            for key in PREFLIGHT.PROVIDER_FLAG_KEYS:
                with self.subTest(filename=filename, key=key):
                    errors = self.validate_mutation(
                        filename,
                        lambda text, key=key: text.replace(
                            f"{key}=false", f"{key}=true", 1
                        ),
                    )
                    self.assertTrue(any(key in item and "default" in item for item in errors))

    def test_provider_switches_reach_runtime_services(self) -> None:
        compose = (self.repo_root / "deploy" / "compose.internal.yml").read_text(
            encoding="utf-8"
        )
        services = PREFLIGHT.parse_services(compose)
        for service in PREFLIGHT.PROVIDER_FLAG_SERVICES:
            with self.subTest(service=service):
                self.assertIn("*backend-common-environment", services[service])
        for key in PREFLIGHT.PROVIDER_FLAG_KEYS:
            self.assertEqual(compose.count(f"{key}: ${{{key}:-false}}"), 1)

    def test_internal_asset_access_is_pinned_to_same_origin(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "POLARIS_ASSET_ACCESS_URL_MODE: same_origin",
                "POLARIS_ASSET_ACCESS_URL_MODE: auto",
                1,
            ),
        )
        self.assertTrue(any("same-origin access proxy" in item for item in errors))

    def test_external_backend_network_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "  backend:\n    internal: true",
                "  backend:\n    internal: false",
            ),
        )
        self.assertTrue(
            any("backend network must set internal exactly to true" in item for item in errors)
        )

    def test_internal_ingress_network_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "  ingress:\n    internal: false",
                "  ingress:\n    internal: true",
            ),
        )
        self.assertTrue(
            any(
                "ingress network must set internal exactly to false" in item
                for item in errors
            )
        )

    def test_frontend_ingress_attachment_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "    networks:\n      - edge\n\n  caddy:",
                "    networks:\n      - edge\n      - ingress\n\n  caddy:",
                1,
            ),
        )
        self.assertTrue(
            any(
                "frontend network attachments must be exactly: edge" in item
                for item in errors
            )
        )

    def test_caddy_must_attach_to_dedicated_ingress(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "    networks:\n      - edge\n      - ingress\n\nnetworks:",
                "    networks:\n      - edge\n\nnetworks:",
                1,
            ),
        )
        self.assertTrue(
            any(
                "caddy network attachments must be exactly: edge, ingress" in item
                for item in errors
            )
        )

    def test_missing_resource_limit_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace("    mem_limit: 512m\n", "", 1),
        )
        self.assertTrue(any("missing mem_limit" in item for item in errors))

    def test_missing_logging_policy_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace("    <<: *service-security\n", "", 1),
        )
        self.assertTrue(any("security/logging" in item for item in errors))

    def test_caddy_file_capability_bounding_set_is_required(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "    cap_add:\n      - NET_BIND_SERVICE\n",
                "",
                1,
            ),
        )
        self.assertTrue(any("file capability" in item for item in errors))

    def test_transport_upgrade_header_is_rejected(self) -> None:
        header_name = "Strict" + "-Transport-Security"
        errors = self.validate_mutation(
            "Caddyfile",
            lambda text: text + f'\n{header_name} "max-age=3600"\n',
        )
        self.assertTrue(any("transport-upgrade" in item for item in errors))

    def test_provider_egress_is_worker_only(self) -> None:
        errors = self.validate_mutation(
            "compose.internal.yml",
            lambda text: text.replace(
                "    networks:\n      - edge\n      - ingress\n\nnetworks:",
                "    networks:\n      - edge\n      - ingress\n"
                "      - provider-egress\n\nnetworks:",
                1,
            ),
        )
        self.assertTrue(
            any(
                "caddy network attachments must be exactly: edge, ingress" in item
                for item in errors
            )
        )

    def test_readme_caddy_validation_retains_required_capability(self) -> None:
        errors = self.validate_mutation(
            "README.md",
            lambda text: text.replace("  --cap-add NET_BIND_SERVICE \\\n", "", 1),
        )
        self.assertTrue(
            any(
                "README Caddy validation must retain NET_BIND_SERVICE" in item
                for item in errors
            )
        )

    def test_backend_runtime_bundle_copy_is_required(self) -> None:
        errors = self.validate_mutation(
            "backend.Dockerfile",
            lambda text: text.replace(
                "COPY backend/skill_sources ./skill_sources",
                "# runtime bundle omitted",
            ),
        )
        self.assertTrue(any("skill_sources" in item for item in errors))

    def test_backend_catalog_assets_copy_is_required(self) -> None:
        errors = self.validate_mutation(
            "backend.Dockerfile",
            lambda text: text.replace(
                "COPY backend/catalog_assets ./catalog_assets",
                "# catalog assets omitted",
            ),
        )
        self.assertTrue(any("catalog_assets" in item for item in errors))

    def test_disabled_pip_hash_mode_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "backend.Dockerfile",
            lambda text: text.replace("PIP_REQUIRE_HASHES=1", "PIP_REQUIRE_HASHES=0"),
        )
        self.assertTrue(any("never disable pip hash mode" in item for item in errors))

    def test_pip_install_without_require_hashes_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "backend.Dockerfile",
            lambda text: text.replace("--require-hashes ", ""),
        )
        self.assertTrue(any("exactly one hash-locked" in item for item in errors))

    def test_local_project_pip_install_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "backend.Dockerfile",
            lambda text: text + "\nRUN python -m pip install .\n",
        )
        self.assertTrue(any("project/VCS/URL" in item for item in errors))

    def test_editable_pip_install_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "backend.Dockerfile",
            lambda text: text + "\nRUN python -m pip install --editable .\n",
        )
        self.assertTrue(any("project/VCS/URL" in item for item in errors))

    def test_unlocked_secondary_pip_install_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "backend.Dockerfile",
            lambda text: text + "\nRUN python -m pip install requests\n",
        )
        self.assertTrue(any("exactly one hash-locked" in item for item in errors))

    def test_vcs_url_pip_install_is_rejected(self) -> None:
        url = "git" + "+https://example.invalid/package.git"
        errors = self.validate_mutation(
            "backend.Dockerfile",
            lambda text: text + f"\nRUN python -m pip install {url}\n",
        )
        self.assertTrue(any("project/VCS/URL" in item for item in errors))

    def test_frontend_external_hosting_metadata_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "frontend.Dockerfile",
            lambda text: text + "\nCOPY .openai/hosting.json .openai/hosting.json\n",
        )
        self.assertTrue(any("hosting metadata" in item for item in errors))

    def test_npm_ci_without_ignore_scripts_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "frontend.Dockerfile",
            lambda text: text.replace("--ignore-scripts ", ""),
        )
        self.assertTrue(any("npm ci with --ignore-scripts" in item for item in errors))

    def test_secondary_npm_install_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "frontend.Dockerfile",
            lambda text: text + "\nRUN npm install example-package\n",
        )
        self.assertTrue(any("exactly one npm ci" in item for item in errors))

    def test_all_explicit_npm_lifecycle_scripts_are_rejected(self) -> None:
        for lifecycle in sorted(PREFLIGHT.NPM_LIFECYCLE_SCRIPTS):
            with self.subTest(lifecycle=lifecycle):
                errors = self.validate_mutation(
                    "frontend.Dockerfile",
                    lambda text, lifecycle=lifecycle: (
                        text + f"\nRUN npm run {lifecycle}\n"
                    ),
                )
                self.assertTrue(
                    any("must not invoke npm lifecycle scripts" in item for item in errors)
                )

    def test_postinstall_black_box_source_preflight_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            self.write_fixture(
                target,
                "frontend.Dockerfile",
                lambda text: text + "\nRUN npm run postinstall\n",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--repo-root",
                    str(target),
                    "--mode",
                    "source",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("npm lifecycle scripts", result.stdout)

    def test_npm_lifecycle_bypass_black_box_matrix_is_blocked(self) -> None:
        bypasses = (
            "RUN echo ok&&npm run postinstall",
            "RUN echo ok;npm run postinstall",
            'RUN /bin/sh -c "npm run postinstall"',
            "RUN bash -lc 'npm run postinstall'",
            "RUN false||npm run postinstall",
            "RUN printf ok|npm run postinstall",
            "RUN npm run build&&npm run postinstall",
            """RUN sh -c "bash -lc 'npm run postinstall'" """.strip(),
            r'RUN sh -c \"npm run postinstall\"',
            "RUN /usr/local/bin/npm.exe --silent run-script --if-present postinstall",
            r"RUN C:\node\npm.cmd --silent run-script postinstall",
            "RUN NPM --silent RUN POSTINSTALL",
            'RUN ["npm.cmd", "--silent", "run-script", "postinstall"]',
        )
        for bypass in bypasses:
            with self.subTest(bypass=bypass), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary)
                self.write_fixture(
                    target,
                    "frontend.Dockerfile",
                    lambda text, bypass=bypass: text + f"\n{bypass}\n",
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        str(MODULE_PATH),
                        "--repo-root",
                        str(target),
                        "--mode",
                        "source",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0, bypass)
                self.assertIn("npm", result.stdout.lower())

    def test_json_run_with_absolute_npm_path_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "frontend.Dockerfile",
            lambda text: (
                text + '\nRUN ["/usr/local/bin/npm", "run", "postinstall"]\n'
            ),
        )
        self.assertTrue(
            any("must not invoke npm lifecycle scripts" in item for item in errors)
        )

    def test_json_build_run_is_allowed(self) -> None:
        errors = self.validate_mutation(
            "frontend.Dockerfile",
            lambda text: text.replace(
                "RUN npm run build",
                'RUN ["npm", "run", "build"]',
                1,
            ),
        )
        self.assertEqual(errors, [])

    def test_csp_allows_only_local_worker_bridge_http_sources(self) -> None:
        errors = self.validate_mutation(
            "Caddyfile",
            lambda text: text.replace(
                "connect-src 'self' http://localhost:8765/v1/enrollment",
                "connect-src 'self' http://example.invalid http://localhost:8765/v1/enrollment",
                1,
            ),
        )
        self.assertTrue(any("external HTTP origins" in item for item in errors))

    def synthetic_deployment_values(self) -> dict[str, str]:
        digest_bases = (
            "0123456789abcdef",
            "123456789abcdef0",
            "23456789abcdef01",
            "3456789abcdef012",
            "456789abcdef0123",
            "56789abcdef01234",
        )
        release = "1a07d3ad6b75c93037d519f040839fe685957d00"
        values = {
            "POLARIS_RELEASE_SHA": release,
            "POLARIS_BACKEND_IMAGE": f"polaris/backend:{release}",
            "POLARIS_FRONTEND_IMAGE": f"polaris/frontend:{release}",
            "POLARIS_PYTHON_BASE_IMAGE": f"python:3.13.14-slim@sha256:{digest_bases[0] * 4}",
            "POLARIS_NODE_BASE_IMAGE": f"node:22.22.0-bookworm-slim@sha256:{digest_bases[1] * 4}",
            "POLARIS_POSTGRES_IMAGE": f"postgres:18.4-alpine@sha256:{digest_bases[2] * 4}",
            "POLARIS_REDIS_IMAGE": f"redis:8.6.4-alpine@sha256:{digest_bases[3] * 4}",
            "POLARIS_MINIO_IMAGE": (
                "minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:"
                f"{digest_bases[4] * 4}"
            ),
            "POLARIS_CADDY_IMAGE": f"caddy:2.10.2-alpine@sha256:{digest_bases[5] * 4}",
            "POLARIS_POSTGRES_PASSWORD": "synthetic-postgres-value",
            "POLARIS_REDIS_PASSWORD": "synthetic-redis-value",
            "POLARIS_MINIO_ROOT_USER": "synthetic-minio-user",
            "POLARIS_MINIO_ROOT_PASSWORD": "synthetic-minio-value",
            "POLARIS_ACCESS_URL_SECRET": "synthetic-signing-value",
            "POLARIS_TASK_RUNNER": "tengli",
            "POLARIS_PROVIDER_QUOTA_ENABLED": "true",
            "POLARIS_TENGLI_API_KEY": "synthetic-provider-key",
            "POLARIS_TENGLI_BASE_URL": "https://provider.example.test/v1",
            "POLARIS_TENGLI_IMAGE_MODEL": "synthetic-image-model",
            "POLARIS_TENGLI_VISION_MODEL": "synthetic-vision-model",
            "POLARIS_TENGLI_SMOKE_BUDGET_USD": "2",
            "POLARIS_TENGLI_DAILY_BUDGET_USD": "2",
            "POLARIS_TENGLI_MONTHLY_BUDGET_USD": "10",
            "POLARIS_TENGLI_DAILY_REQUEST_LIMIT": "20",
            "POLARIS_TENGLI_PLANNER_RESERVE_MICROS": "250000",
            "POLARIS_XINGHE_CONCURRENCY": "6",
        }
        values.update({key: "false" for key in PREFLIGHT.PROVIDER_FLAG_KEYS})
        return values

    def validate_synthetic_deployment(self, **overrides: str) -> list[str]:
        values = self.synthetic_deployment_values()
        values.update(overrides)
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "synthetic.env"
            env_file.write_text(
                "".join(f"{key}={value}\n" for key, value in values.items()),
                encoding="utf-8",
            )
            errors, _, _ = PREFLIGHT.validate_deployment(
                repo_root=self.repo_root,
                env_file=env_file,
                host_memory_mib=16384,
                host_cpus=8,
                free_disk_mib=40960,
                free_inode_percent=50,
                candidate_images_mib=4096,
                paired_backup_mib=4096,
            )
        return errors

    def test_provider_switches_require_tengli(self) -> None:
        for key in PREFLIGHT.PROVIDER_FLAG_KEYS:
            with self.subTest(key=key):
                errors = self.validate_synthetic_deployment(
                    POLARIS_TASK_RUNNER="mock",
                    **{key: "true"},
                )
                self.assertTrue(
                    any("require POLARIS_TASK_RUNNER=tengli" in item for item in errors)
                )

    def test_internal_tengli_runner_allows_quota_disabled(self) -> None:
        errors = self.validate_synthetic_deployment(
            POLARIS_PROVIDER_QUOTA_ENABLED="false",
        )
        self.assertEqual(errors, [])

    def test_tengli_runner_with_provider_quota_passes(self) -> None:
        errors = self.validate_synthetic_deployment(
            POLARIS_PROVIDER_QUOTA_ENABLED="true",
        )
        self.assertEqual(errors, [])

    def test_tengli_runner_rejects_out_of_range_concurrency(self) -> None:
        for concurrency in ("0", "3001", "true", "6.5"):
            with self.subTest(concurrency=concurrency):
                errors = self.validate_synthetic_deployment(
                    POLARIS_XINGHE_CONCURRENCY=concurrency,
                )
                self.assertTrue(
                    any(
                        "POLARIS_XINGHE_CONCURRENCY must be from 1 through 3000" in item
                        for item in errors
                    )
                )

    def test_zero_recurring_provider_limits_mean_unlimited(self) -> None:
        errors = self.validate_synthetic_deployment(
            POLARIS_TENGLI_SMOKE_BUDGET_USD="200",
            POLARIS_TENGLI_DAILY_BUDGET_USD="0",
            POLARIS_TENGLI_MONTHLY_BUDGET_USD="0",
            POLARIS_TENGLI_DAILY_REQUEST_LIMIT="0",
        )
        self.assertEqual(errors, [])

    def test_maximum_provider_allowance_passes(self) -> None:
        maximum = str(PREFLIGHT.MAX_PROVIDER_ALLOWANCE)
        errors = self.validate_synthetic_deployment(
            POLARIS_TENGLI_SMOKE_BUDGET_USD=maximum,
            POLARIS_TENGLI_DAILY_BUDGET_USD=maximum,
            POLARIS_TENGLI_MONTHLY_BUDGET_USD=maximum,
            POLARIS_TENGLI_DAILY_REQUEST_LIMIT=maximum,
        )
        self.assertEqual(errors, [])

    def test_provider_allowance_above_maximum_is_rejected(self) -> None:
        above_maximum = str(PREFLIGHT.MAX_PROVIDER_ALLOWANCE + 1)
        for key in (
            "POLARIS_TENGLI_DAILY_BUDGET_USD",
            "POLARIS_TENGLI_MONTHLY_BUDGET_USD",
            "POLARIS_TENGLI_DAILY_REQUEST_LIMIT",
            "POLARIS_TENGLI_SMOKE_BUDGET_USD",
        ):
            with self.subTest(key=key):
                overrides = {
                    "POLARIS_TENGLI_DAILY_BUDGET_USD": "2",
                    "POLARIS_TENGLI_MONTHLY_BUDGET_USD": "10",
                    key: above_maximum,
                }
                errors = self.validate_synthetic_deployment(**overrides)
                self.assertTrue(any(key in item for item in errors))

    def test_internal_provider_switches_allow_quota_disabled(self) -> None:
        for key in PREFLIGHT.PROVIDER_FLAG_KEYS:
            with self.subTest(key=key):
                errors = self.validate_synthetic_deployment(
                    POLARIS_PROVIDER_QUOTA_ENABLED="false",
                    **{key: "true"},
                )
                self.assertEqual(errors, [])

    def test_invalid_provider_quota_value_is_rejected(self) -> None:
        errors = self.validate_synthetic_deployment(
            POLARIS_PROVIDER_QUOTA_ENABLED="yes",
        )
        self.assertTrue(
            any(
                "POLARIS_PROVIDER_QUOTA_ENABLED must be true or false" in item
                for item in errors
            )
        )

    def test_enabled_provider_switch_rejects_placeholder_credential(self) -> None:
        key = PREFLIGHT.PROVIDER_FLAG_KEYS[0]
        errors = self.validate_synthetic_deployment(
            POLARIS_TENGLI_API_KEY="<SET_ON_SERVER_PROVIDER_API_KEY>",
            **{key: "true"},
        )
        self.assertTrue(any("POLARIS_TENGLI_API_KEY" in item for item in errors))

    def test_disabled_provider_switches_allow_mock_without_provider_credential(self) -> None:
        errors = self.validate_synthetic_deployment(
            POLARIS_TASK_RUNNER="mock",
            POLARIS_PROVIDER_QUOTA_ENABLED="false",
            POLARIS_TENGLI_API_KEY="<SET_ON_SERVER_PROVIDER_API_KEY>",
        )
        self.assertEqual(errors, [])

    def test_internal_provider_switch_contract_includes_model_pose(self) -> None:
        self.assertIn(
            "POLARIS_MODEL_POSE_TRANSFER_PROVIDER_ENABLED",
            PREFLIGHT.PROVIDER_FLAG_KEYS,
        )

    def test_invalid_provider_switch_value_is_rejected(self) -> None:
        key = PREFLIGHT.PROVIDER_FLAG_KEYS[0]
        errors = self.validate_synthetic_deployment(**{key: "yes"})
        self.assertTrue(any(f"{key} must be true or false" in item for item in errors))

    def test_enabled_provider_switch_with_valid_tengli_configuration_passes(self) -> None:
        key = PREFLIGHT.PROVIDER_FLAG_KEYS[0]
        errors = self.validate_synthetic_deployment(**{key: "true"})
        self.assertEqual(errors, [])

    def test_synthetic_deployment_black_box_passes(self) -> None:
        lines = [
            f"{key}={value}" for key, value in self.synthetic_deployment_values().items()
        ]
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "synthetic.env"
            env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--repo-root",
                    str(self.repo_root),
                    "--mode",
                    "deployment",
                    "--env-file",
                    str(env_file),
                    "--host-memory-mib",
                    "16384",
                    "--host-cpus",
                    "8",
                    "--free-disk-mib",
                    "40960",
                    "--free-inode-percent",
                    "50",
                    "--candidate-images-mib",
                    "4096",
                    "--paired-backup-mib",
                    "4096",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("PASS deployment preflight", result.stdout)

    def test_example_deployment_black_box_blocks_without_value_leak(self) -> None:
        env_file = self.repo_root / "deploy" / ".env.internal.example"
        values = PREFLIGHT.parse_env(env_file)
        result = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "--repo-root",
                str(self.repo_root),
                "--mode",
                "deployment",
                "--env-file",
                str(env_file),
                "--host-memory-mib",
                "4096",
                "--host-cpus",
                "4",
                "--free-disk-mib",
                "40960",
                "--free-inode-percent",
                "50",
                "--candidate-images-mib",
                "4096",
                "--paired-backup-mib",
                "4096",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED deployment preflight", result.stdout)
        protected_keys = (
            PREFLIGHT.SENSITIVE_KEYS
            | PREFLIGHT.PROVIDER_SENSITIVE_KEYS
            | PREFLIGHT.THIRD_PARTY_IMAGE_KEYS
            | PREFLIGHT.APPLICATION_IMAGE_KEYS
            | {"POLARIS_RELEASE_SHA"}
        )
        for key in protected_keys:
            self.assertNotIn(values.get(key, ""), result.stdout)

    def test_each_readme_service_resource_value_is_verified(self) -> None:
        compose = (self.repo_root / "deploy" / "compose.internal.yml").read_text(
            encoding="utf-8"
        )
        resources = PREFLIGHT.service_resources(PREFLIGHT.parse_services(compose))
        for service in sorted(PREFLIGHT.EXPECTED_SERVICES):
            memory, cpus, pids = resources[service]
            original = f"| `{service}` | {memory} | {cpus:.2f} | {pids} |"
            variants = (
                f"| `{service}` | {memory + 1} | {cpus:.2f} | {pids} |",
                f"| `{service}` | {memory} | {cpus + 0.01:.2f} | {pids} |",
                f"| `{service}` | {memory} | {cpus:.2f} | {pids + 1} |",
            )
            for changed in variants:
                with self.subTest(service=service, changed=changed):
                    errors = self.validate_mutation(
                        "README.md",
                        lambda text, original=original, changed=changed: text.replace(
                            original, changed, 1
                        ),
                    )
                    self.assertTrue(
                        any(f"does not match Compose for {service}" in item for item in errors)
                    )

    def test_each_readme_resource_total_is_verified(self) -> None:
        original = (
            "| **Long-running total** | **10288** | **4.45** | **5504** |"
        )
        variants = (
            "| **Long-running total** | **10289** | **4.45** | **5504** |",
            "| **Long-running total** | **10288** | **4.46** | **5504** |",
            "| **Long-running total** | **10288** | **4.45** | **5505** |",
        )
        for changed in variants:
            with self.subTest(changed=changed):
                errors = self.validate_mutation(
                    "README.md",
                    lambda text, changed=changed: text.replace(original, changed, 1),
                )
                self.assertTrue(
                    any("long-running resource total" in item for item in errors)
                )

    def test_each_compose_service_resource_value_is_verified(self) -> None:
        compose = (self.repo_root / "deploy" / "compose.internal.yml").read_text(
            encoding="utf-8"
        )
        services = PREFLIGHT.parse_services(compose)
        resources = PREFLIGHT.service_resources(services)
        for service in sorted(PREFLIGHT.EXPECTED_SERVICES):
            memory, cpus, pids = resources[service]
            original_block = services[service]
            variants = (
                original_block.replace(
                    f"    mem_limit: {memory}m",
                    f"    mem_limit: {memory + 1}m",
                    1,
                ),
                original_block.replace(
                    f'    cpus: "{cpus:.2f}"',
                    f'    cpus: "{cpus + 0.01:.2f}"',
                    1,
                ),
                original_block.replace(
                    f"    pids_limit: {pids}",
                    f"    pids_limit: {pids + 1}",
                    1,
                ),
            )
            for changed_block in variants:
                with self.subTest(service=service, changed=changed_block):
                    errors = self.validate_mutation(
                        "compose.internal.yml",
                        lambda text, original_block=original_block, changed_block=changed_block: (
                            text.replace(original_block, changed_block, 1)
                        ),
                    )
                    self.assertTrue(
                        any(f"does not match Compose for {service}" in item for item in errors)
                    )

    def test_missing_verified_vinext_patch_is_rejected(self) -> None:
        errors = self.validate_mutation(
            "package.json",
            lambda text: text.replace(
                "node build/patch-vinext-production.mjs && vinext build",
                "vinext build",
            ),
        )
        self.assertTrue(any("build must apply" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
