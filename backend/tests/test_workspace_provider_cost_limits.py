from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.models import WorkspaceLimitRecord

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations"
    / "versions"
    / "20260820_0010_workspace_provider_cost_limits.py"
)


def test_workspace_provider_cost_defaults_are_ten_million_dollars_in_micros() -> None:
    daily_default = (
        WorkspaceLimitRecord.__table__.c.daily_provider_cost_limit_micros.default
    )
    monthly_default = (
        WorkspaceLimitRecord.__table__.c.monthly_provider_cost_limit_micros.default
    )

    assert daily_default is not None
    assert monthly_default is not None
    assert daily_default.arg == 10_000_000_000_000
    assert monthly_default.arg == 10_000_000_000_000


def test_workspace_limit_migration_targets_only_legacy_defaults() -> None:
    spec = spec_from_file_location("workspace_limit_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration._LEGACY_DAILY_LIMIT_MICROS == 10_000_000
    assert migration._LEGACY_MONTHLY_LIMIT_MICROS == 100_000_000
    assert migration._NEW_LIMIT_MICROS == 10_000_000_000_000
