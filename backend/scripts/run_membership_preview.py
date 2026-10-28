"""Isolated account UI preview: local SQLite, no merchant configuration."""

import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings  # noqa: E402 -- local script sets the import root above
from app.main import create_app  # noqa: E402

if __name__ == "__main__":
    data = ROOT / "backend/.data/membership-preview"
    data.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        environment="development",
        auth_mode="development",
        database_url="sqlite:///" + (data / "preview.db").as_posix(),
        storage_backend="local",
        storage_dir=data / "storage",
        auto_create_schema=True,
        auto_process_jobs=False,
        task_dispatcher="inline",
        credit_payments_enabled=False,
        workspace_credit_enforcement_enabled=False,
        workspace_credit_pilot_ids=[],
        development_provider_settings_enabled=False,
        ecommerce_startup_recovery_enabled=False,
        aurora_work_root=data / "aurora",
        ecommerce_session_dir=data / "sessions",
        ecommerce_1688_session_dir=data / "1688",
        ecommerce_jd_session_dir=data / "jd",
        ecommerce_pinduoduo_session_dir=data / "pdd",
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=18939, access_log=False)
