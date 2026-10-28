"""Pure release transformation; original services and provider values stay private."""

import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "detail_release", Path(__file__).parents[1] / "prepare_domestic_detail_release.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REVISION = "a" * 40
BACKEND, FRONTEND = "sha256:" + "b" * 64, "sha256:" + "c" * 64
PRIMARY_PROVIDER = {
    "POLARIS_ANALYSIS_BASE_URL": "https://api.tengliapi.xyz",
    "POLARIS_ANALYSIS_MODEL": "gpt-5.6-sol",
    "POLARIS_ANALYSIS_API_KEY": "synthetic-private-key",
}
PROVIDER = {
    "POLARIS_ANALYSIS_BASE_URL": "https://api.ooapi.cc",
    "POLARIS_ANALYSIS_MODEL": "gpt-5.6-sol",
    "POLARIS_ANALYSIS_API_KEY": "synthetic-one",
}


def base():
    app = {
        "image": "frozen:old",
        "build": {"context": "."},
        "environment": {
            **PRIMARY_PROVIDER,
            "POLARIS_PROVIDER_FAILOVER_ENABLED": "true",
            "POLARIS_PROVIDER_FAILOVER_BASE_URL": PROVIDER["POLARIS_ANALYSIS_BASE_URL"],
            "POLARIS_PROVIDER_FAILOVER_MODEL": PROVIDER["POLARIS_ANALYSIS_MODEL"],
            "POLARIS_PROVIDER_FAILOVER_API_KEY": "synthetic-one,synthetic-two",
            "POLARIS_IMAGE_API_KEY": "synthetic-original-image-key",
            "POLARIS_TENGLI_DAILY_BUDGET_USD": "25",
            "POLARIS_TENGLI_MONTHLY_BUDGET_USD": "100",
        },
        "labels": {},
        "volumes": [
            {
                "type": "volume",
                "source": "original-harness",
                "target": "/app/.data/aurora-main-image",
            },
            {
                "type": "volume",
                "source": "original-browser",
                "target": "/app/.data/ecommerce-session",
            },
        ],
        "ports": ["127.0.0.1:8000:8000"],
        "command": ["original"],
    }
    return {
        "name": "polaris-production",
        "services": {
            "api": deepcopy(app),
            "worker": deepcopy(app),
            "dispatcher": deepcopy(app),
            "frontend": {"image": "frozen:frontend"},
            "migrate": deepcopy(app),
            "caddy": {
                "volumes": [
                    {
                        "type": "bind",
                        "source": "/original/Caddyfile",
                        "target": "/etc/caddy/Caddyfile",
                    },
                    {"type": "volume", "source": "original-tls", "target": "/data"},
                ]
            },
        },
    }


def prepare(source=None, **changes):
    values = {
        "backend_image": BACKEND,
        "frontend_image": FRONTEND,
        "revision": REVISION,
        "caddy_file": f"/opt/polaris/releases/{REVISION}/deploy/Caddyfile.domestic-detail-native",
        "provider": deepcopy(PROVIDER),
    }
    values.update(changes)
    return MODULE.overlay(source or base(), **values)


class ReleaseTest(unittest.TestCase):
    def test_detail_worker_defaults_to_six_tasks_and_is_configurable(self):
        worker = prepare()["services"]["detail-worker"]
        self.assertIn("--concurrency=6", worker["command"])
        self.assertEqual(worker["mem_limit"], "2g")
        self.assertIn("--prefetch-multiplier=1", worker["command"])
        self.assertIn("--concurrency=12", prepare(worker_concurrency=12)["services"]["detail-worker"]["command"])
        for value in (0, 33, True):
            with self.assertRaises(ValueError):
                prepare(worker_concurrency=value)

    def test_analysis_uses_one_existing_text_account_with_its_endpoint_and_model(self):
        result = prepare()
        for name in ("detail-api", "detail-worker"):
            environment = result["services"][name]["environment"]
            for key, value in PROVIDER.items():
                self.assertEqual(environment[key], value)

    def test_concatenated_account_pool_cannot_be_used_as_a_single_credential(self):
        original = base()
        environment = original["services"]["api"]["environment"]
        environment.update(
            {
                "POLARIS_PROVIDER_FAILOVER_BASE_URL": "https://api.ooapi.cc",
                "POLARIS_PROVIDER_FAILOVER_API_KEY": "synthetic-one,synthetic-two",
            }
        )
        with self.assertRaises(ValueError):
            prepare(
                original,
                provider={
                    **PROVIDER,
                    "POLARIS_ANALYSIS_BASE_URL": environment[
                        "POLARIS_PROVIDER_FAILOVER_BASE_URL"
                    ],
                    "POLARIS_ANALYSIS_API_KEY": environment[
                        "POLARIS_PROVIDER_FAILOVER_API_KEY"
                    ],
                },
            )

    def test_each_existing_account_is_supported_and_unknown_accounts_are_rejected(self):
        for provider in (
            PRIMARY_PROVIDER,
            PROVIDER,
            {**PROVIDER, "POLARIS_ANALYSIS_API_KEY": "synthetic-two"},
        ):
            result = prepare(provider=provider)
            self.assertEqual(
                result["services"]["detail-api"]["environment"][
                    "POLARIS_ANALYSIS_API_KEY"
                ],
                provider["POLARIS_ANALYSIS_API_KEY"],
            )
        for provider in (
            {**PROVIDER, "POLARIS_ANALYSIS_API_KEY": "unconfigured-account"},
            {**PROVIDER, "POLARIS_ANALYSIS_MODEL": "unconfigured-model"},
            {
                **PRIMARY_PROVIDER,
                "POLARIS_ANALYSIS_API_KEY": PROVIDER["POLARIS_ANALYSIS_API_KEY"],
            },
        ):
            with self.assertRaises(ValueError):
                prepare(provider=provider)

    def test_disabled_account_pool_is_not_authority_for_a_release(self):
        original = base()
        original["services"]["api"]["environment"][
            "POLARIS_PROVIDER_FAILOVER_ENABLED"
        ] = "false"
        with self.assertRaises(ValueError):
            prepare(original)

    def test_original_api_and_default_worker_are_never_replaced(self):
        original = base()
        before = deepcopy(original)
        result = prepare(original)
        self.assertEqual(original, before)
        self.assertEqual(
            result["services"]["api"],
            {"environment": {"POLARIS_AUTO_PROCESS_JOBS": "false"}},
        )
        self.assertNotIn("worker", result["services"])
        self.assertEqual(result["services"]["dispatcher"]["image"], BACKEND)
        for name in ("detail-api", "detail-worker"):
            service = result["services"][name]
            self.assertNotIn("ports", service)
            self.assertNotIn("build", service)
            self.assertEqual(len(service["volumes"]), 1)
            environment = service["environment"]
            self.assertEqual(
                environment["POLARIS_IMAGE_API_KEY"], "synthetic-original-image-key"
            )
            self.assertEqual(environment["POLARIS_TENGLI_DAILY_BUDGET_USD"], "25")
            self.assertEqual(
                environment["POLARIS_ECOMMERCE_STARTUP_RECOVERY_ENABLED"], "false"
            )
            self.assertEqual(environment["POLARIS_PROVIDER_FAILOVER_ENABLED"], "false")
            self.assertEqual(environment["POLARIS_AUTO_PROCESS_JOBS"], "false")
            self.assertEqual(environment["POLARIS_ANALYSIS_TIMEOUT_SECONDS"], "1200")
        command = result["services"]["detail-worker"]["command"]
        self.assertIn("--queues=domestic_detail_v431", command)
        self.assertNotIn("--queues=default", command)

    def test_creation_rollback_keeps_existing_native_runtime_alive(self):
        result = prepare(native_creation=False)["services"]
        self.assertEqual(
            result["detail-api"]["environment"][
                "POLARIS_DOMESTIC_DETAIL_NATIVE_CREATION_ENABLED"
            ],
            "false",
        )
        self.assertIn("detail-worker", result)
        self.assertEqual(
            result["detail-api"]["environment"]["POLARIS_LEGACY_DETAIL_API_URL"],
            "http://api:8000",
        )

    def test_caddy_changes_only_the_reviewed_config_bind(self):
        volumes = prepare()["services"]["caddy"]["volumes"]
        self.assertEqual(volumes[1], base()["services"]["caddy"]["volumes"][1])
        self.assertTrue(volumes[0]["read_only"])
        self.assertTrue(
            volumes[0]["source"].endswith("/deploy/Caddyfile.domestic-detail-native")
        )

    def test_reject_unreviewed_images_paths_and_provider_authority(self):
        for changes in (
            {"backend_image": "backend:latest"},
            {"frontend_image": "frontend:latest"},
            {"revision": "short"},
            {"caddy_file": "/tmp/unreviewed"},
            {"provider": {**PROVIDER, "POLARIS_IMAGE_API_KEY": "unauthorized"}},
            {
                "provider": {
                    **PROVIDER,
                    "POLARIS_ANALYSIS_BASE_URL": "https://outside.example",
                }
            },
        ):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                prepare(**changes)
        with self.assertRaises(ValueError):
            prepare({**base(), "name": "another-project"})


if __name__ == "__main__":
    unittest.main()
