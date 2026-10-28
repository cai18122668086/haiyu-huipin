"""Lifecycle host for an immutable Polaris worker release; no crawl policy here."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import threading
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class InstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError("WORKER_ALREADY_RUNNING") from None
        return self

    def __exit__(self, *_args):
        if self.file is not None:
            self.file.close()
            self.file = None


class Lifecycle:
    def __init__(self, control_root: Path, release_id: str):
        self.root = control_root
        self.root.mkdir(parents=True, exist_ok=True)
        self.stop_file = self.root / "stop.request"
        self.stop = threading.Event()
        self.monitor_done = threading.Event()
        self.lock = threading.RLock()
        self.state = {
            "pid": os.getpid(),
            "release_id": release_id,
            "started_at": utc_now(),
            "phase": "starting",
            "heartbeat_successes": 0,
            "connection": "starting",
        }
        self.thread = None

    def write(self, **updates):
        with self.lock:
            self.state.update(updates, updated_at=utc_now())
            temporary = self.root / f".status.{os.getpid()}.tmp"
            temporary.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
            os.replace(temporary, self.root / "status.json")

    def request_stop(self, *_args):
        with self.lock:
            self.stop.set()
            self.write(phase="draining")

    def heartbeat_ok(self, ready):
        with self.lock:
            self.write(
                last_heartbeat_at=utc_now(),
                heartbeat_successes=self.state["heartbeat_successes"] + 1,
                ready=bool(ready),
                connection="online" if ready else "offline",
                last_heartbeat_error=None,
            )

    def start(self):
        self.write(phase="running")

        def watch():
            while not self.monitor_done.is_set():
                if self.stop_file.exists():
                    self.request_stop()
                    return
                self.monitor_done.wait(0.2)

        self.thread = threading.Thread(
            target=watch, name="worker-lifecycle", daemon=True
        )
        self.thread.start()

    def finish(self):
        self.monitor_done.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.write(phase="stopped", ready=False, stopped_at=utc_now())


def verify_release(release_root: Path):
    manifest = json.loads(
        (release_root / "runtime-manifest.json").read_text(encoding="utf-8")
    )
    for item in manifest["files"]:
        path = (release_root / item["path"]).resolve()
        if not path.is_relative_to(release_root.resolve()) or not path.is_file():
            raise RuntimeError("WORKER_RELEASE_FILE_MISSING")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError("WORKER_RELEASE_HASH_MISMATCH")
    return manifest


def run_agent(module, config, lifecycle):
    """Compose existing worker primitives with an external stop request."""

    class ObservedClient(module.WorkerApiClient):
        def heartbeat(self, worker_id, payload):
            try:
                result = super().heartbeat(worker_id, payload)
            except module.LocalWorkerError as exc:
                auth_codes = {
                    "WORKER_AUTH_INVALID",
                    "WORKER_AUTH_REQUIRED",
                    "WORKER_TOKEN_EXPIRED",
                    "WORKER_TOKEN_ROTATED",
                    "WORKER_SCOPE_REVOKED",
                }
                code = str(exc)
                lifecycle.write(
                    connection="authorization_required"
                    if code in auth_codes
                    else "unavailable",
                    last_heartbeat_error=code
                    if code in auth_codes
                    else "WORKER_API_UNAVAILABLE",
                )
                raise
            lifecycle.heartbeat_ok(payload.get("ready", True))
            return result

    api = ObservedClient(
        config.api_url, config.session_token, timeout=config.request_timeout_seconds
    )
    profiles = module.LocalProfileStore(config.profile_root)
    runtime = module.LocalWorkerRuntime(api, config.worker_id, profiles)
    loop = module.LocalWorkerLoop(
        api,
        runtime,
        capabilities=list(config.capabilities),
        concurrency=config.concurrency,
        heartbeat_interval_seconds=config.heartbeat_interval_seconds,
        poll_interval_seconds=config.poll_interval_seconds,
        lease_renew_interval_seconds=config.lease_renew_interval_seconds,
        token_refresh_interval_seconds=config.token_refresh_interval_seconds,
        on_token_refreshed=config.persist_session_token,
        on_recovery_token_refreshed=config.persist_recovery_token,
        on_worker_id_persisted=config.persist_worker_id,
        enrollment_token=config.enrollment_token,
        recovery_token=config.recovery_token,
        device_id=os.environ.get("POLARIS_WORKER_DEVICE_ID", config.worker_id),
    )
    bridge = module.LocalWorkerBridge(
        loop,
        host=config.bridge_host,
        port=config.bridge_port,
        allowed_origin=config.bridge_origin,
    )
    bridge.start()
    lifecycle.start()
    try:
        loop.run(
            module.build_local_product_crawler(timeout_ms=config.browser_timeout_ms),
            lifecycle.stop,
        )
    finally:
        bridge.stop()
        lifecycle.finish()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--worker-root", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args(argv)
    manifest = verify_release(args.release_root)
    if args.verify_only:
        print(
            json.dumps({"release_verified": True, "release_id": manifest["release_id"]})
        )
        return 0
    from app import ecommerce_local_worker as module

    module_path = Path(module.__file__).resolve()
    if not module_path.is_relative_to((args.release_root / "backend").resolve()):
        raise RuntimeError("WORKER_SOURCE_OUTSIDE_RELEASE")
    config = module.LocalWorkerConfig.from_env()
    if args.check_config:
        print(
            json.dumps(
                {
                    "config_valid": True,
                    "source_isolated": True,
                    "network_used": False,
                    "concurrency": config.concurrency,
                }
            )
        )
        return 0
    if args.worker_root is None:
        raise RuntimeError("WORKER_ROOT_REQUIRED")
    control_root = args.worker_root / "runtime-control"
    with InstanceLock(control_root / "instance.lock"):
        if (control_root / "stop.request").exists():
            return 0
        lifecycle = Lifecycle(control_root, manifest["release_id"])
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            number = getattr(signal, name, None)
            if number is not None:
                with suppress(ValueError):
                    signal.signal(number, lifecycle.request_stop)
        run_agent(module, config, lifecycle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
