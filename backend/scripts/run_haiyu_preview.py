"""Run the existing local preview with operator-owned backend/.env settings."""

import argparse
import hashlib
import os
import sys
from pathlib import Path

import uvicorn

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "backend"))

from app.config import Settings  # noqa: E402 -- local script sets the import root above
from app.main import create_app  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18821)
    args = parser.parse_args()
    data = root / "backend" / ".data" / "haiyu-preview-latest"
    data.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=root / "backend" / ".env",
        environment="development",
        auth_mode="development",
        database_url="sqlite:///" + (data / "preview.db").as_posix(),
        auto_create_schema=True,
        storage_backend="local",
        storage_dir=data / "storage",
        asset_access_url_mode="same_origin",
        task_dispatcher="inline",
        auto_process_jobs=True,
        ecommerce_startup_recovery_enabled=False,
        development_provider_env_file=root / "backend" / ".env",
        development_provider_settings_enabled=False,
    )
    application = create_app(settings)
    revision = hashlib.sha256(
        b"".join(
            (root / "backend/app" / name).read_bytes()
            for name in ("haiyu_transport.py", "xinghe_agent.py", "replica.py")
        )
    ).hexdigest()[:12]

    @application.middleware("http")
    async def preview_instance(request, call_next):
        response = await call_next(request)
        # Loopback preview only: verify the actual loaded process, not just
        # an edited proxy file or a shared database that all instances see.
        response.headers["X-Haiyu-Preview-Instance"] = (
            f"port={args.port};pid={os.getpid()};revision={revision}"
        )
        return response

    uvicorn.run(application, host="127.0.0.1", port=args.port, access_log=False)
