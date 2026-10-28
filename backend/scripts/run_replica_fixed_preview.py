"""Run the isolated repair against its own database, storage, and native roots."""

import argparse
import os
import sys
from pathlib import Path

import uvicorn

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "backend"))

from app.config import Settings  # noqa: E402 -- local script sets the import root above
from app.main import create_app  # noqa: E402
from app.replica_native import NativeReplicaRun, ReplicaNativeError  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18840)
    args = parser.parse_args()
    data = root / "backend/.data/fixed-preview"
    settings = Settings(
        _env_file=root / "backend/.env",
        environment="development",
        auth_mode="development",
        database_url="sqlite:///" + (data / "preview.db").as_posix(),
        auto_create_schema=True,
        storage_backend="local",
        storage_dir=data / "storage",
        aurora_work_root=data / "provider",
        asset_access_url_mode="same_origin",
        task_dispatcher="inline",
        auto_process_jobs=True,
        ecommerce_startup_recovery_enabled=False,
        development_provider_settings_enabled=False,
        development_provider_env_file=root / "backend/.env",
    )
    original_restore = NativeReplicaRun.restore

    async def isolated_restore(self, snapshot):
        # An old absolute path must never send this preview's writes into the
        # other chat's run directory, including a retry of an imported record.
        if not Path(snapshot["root"]).resolve().is_relative_to(root.resolve()):
            raise ReplicaNativeError(
                "此历史检查点尚未复制到独立修复目录，已阻止跨目录恢复。"
            )
        return await original_restore(self, snapshot)

    NativeReplicaRun.restore = isolated_restore
    application = create_app(settings)

    @application.middleware("http")
    async def identify(request, call_next):
        response = await call_next(request)
        response.headers["X-Haiyu-Preview-Instance"] = (
            f"isolated-repair;port={args.port};pid={os.getpid()}"
        )
        return response

    uvicorn.run(application, host="127.0.0.1", port=args.port, access_log=False)
