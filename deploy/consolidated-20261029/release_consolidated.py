"""Scoped production release using the existing audited build/test/rollback flow."""

import argparse
import json
from pathlib import Path

import release_base as runner

runner.ROOT = Path("/srv/huipin-consolidated-20261029")
runner.BASE_BACKEND = "huipin-replica-fast/backend:20261029-v2"
runner.BASE_FRONTEND = "huipin-replica-fast/frontend:20261029-v2"
runner.BACKEND = "huipin-consolidated/backend:20261029-v1"
runner.FRONTEND = "huipin-consolidated/frontend:20261029-v1"
runner.EXTRA_TESTS = (
    "tests/test_product_previews.py",
    "tests/test_refined_detail.py",
    "tests/test_replica_planner_truncation.py",
    "tests/test_replica_style_delivery.py",
    "tests/test_replica_canvas.py",
    "tests/test_replica_analysis_recovery.py",
    "tests/test_replica_column_evidence.py",
    "tests/test_replica_compact.py",
    "tests/test_replica_image_recovery.py",
    "tests/test_xinghe_agent.py",
    "tests/test_replica_fast.py",
    "tests/test_execution_reliability.py",
    "tests/test_provider_capacity.py",
    "tests/test_new_provider_routing.py",
    "tests/test_replica_execution_context.py",
    "tests/test_replica_execution_resume.py",
    "tests/test_new_image_skill_providers.py",
    "tests/test_new_detail_and_amazon_providers.py",
)


def verify_assets(image=None):
    expected = json.loads((runner.ROOT / "release-manifest.json").read_text())[
        "fast_assets"
    ]
    code = """import hashlib,json
from pathlib import Path
expected=EXPECTED
actual={name:hashlib.sha256((Path('/app')/name).read_bytes().replace(bytes([13,10]),bytes([10]))).hexdigest() for name in expected}
from app.new_detail_skill_provider import BundledSkillManifestValidator
from app.replica_fast import SKILLS,CONCURRENCY
for skill in SKILLS.values(): BundledSkillManifestValidator().validate(skill)
assert CONCURRENCY==20
print(json.dumps(actual))
""".replace("expected=EXPECTED", "expected=" + repr(expected))
    assert runner.inner(code, image) == expected, (
        "Fast Skill assets differ from reviewed source"
    )


def build():
    manifest = json.loads((runner.ROOT / "release-manifest.json").read_text())
    assert runner.inspect(runner.BASE_BACKEND)["Id"] == manifest["baseline"]["image_id"]
    assert (
        runner.inspect(runner.BASE_FRONTEND)["Id"]
        == manifest["baseline"]["frontend_image_id"]
    )
    runner.build()
    verify_assets(runner.BACKEND)


def activate():
    # The reused release flow requires this existing crawler key. It must remain
    # identical; this release changes image tags only, not credentials or routing.
    current = runner.environment(runner.inspect("huipin-prelaunch-api-1"))
    key = (
        (runner.ROOT / "provider.private.env")
        .read_text()
        .strip()
        .removeprefix(runner.SECRET_NAME + "=")
    )
    assert key == current[runner.SECRET_NAME], "Existing provider credential changed"
    verify_assets(runner.BACKEND)
    assert (
        runner.inspect(runner.BASE_BACKEND)["Id"]
        == json.loads((runner.ROOT / "release-manifest.json").read_text())["baseline"][
            "image_id"
        ]
    )
    runner.activate()
    verify_assets()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "test", "activate"])
    action = parser.parse_args().action
    {"build": build, "test": runner.test, "activate": activate}[action]()
