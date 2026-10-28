"""Isolated lifecycle checks: synthetic tasks, loopback HTTP, temporary profiles."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from worker_host import InstanceLock, Lifecycle, verify_release

_temporary_release = None
if "POLARIS_RUNTIME_TEST_RELEASE" in os.environ:
    RELEASE = Path(os.environ["POLARIS_RUNTIME_TEST_RELEASE"])
else:
    import atexit
    import shutil

    _temporary_release = tempfile.TemporaryDirectory(prefix="polaris-worker-test-")
    atexit.register(_temporary_release.cleanup)
    RELEASE = Path(_temporary_release.name)
    (RELEASE / "backend/app").mkdir(parents=True)
    source_root = Path(__file__).resolve().parents[2]
    paths = ["backend/app/__init__.py", "backend/app/ecommerce_local_worker.py"]
    for relative in paths:
        shutil.copy2(source_root / relative, RELEASE / relative)
    shutil.copy2(Path(__file__).parent / "worker_host.py", RELEASE / "worker_host.py")
    paths.append("worker_host.py")
    manifest = {
        "release_id": "ci-fixture",
        "files": [
            {
                "path": relative,
                "sha256": hashlib.sha256((RELEASE / relative).read_bytes()).hexdigest(),
            }
            for relative in paths
        ],
    }
    (RELEASE / "runtime-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
sys.path.insert(0, str(RELEASE / "backend"))
from app.ecommerce_local_worker import LocalWorkerLoop  # noqa: E402


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Condition did not become true")


class RuntimeTests(unittest.TestCase):
    def test_drain_finishes_active_task_and_keeps_renewing_its_lease(self):
        self._assert_drain(1)

    def test_two_concurrent_slots_drain_without_new_claims(self):
        self._assert_drain(2)

    def _assert_drain(self, concurrency):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lifecycle = Lifecycle(root, "fixture")
            entered, release_task = threading.Event(), threading.Event()
            events = []
            entry_lock = threading.Lock()
            entered_count = 0

            class Api:
                claims = 0

                def heartbeat(self, _worker, payload):
                    events.append(("heartbeat", payload["ready"]))
                    return {"data": {}}

                def claim(self, *_args):
                    self.claims += 1
                    return {
                        "data": {
                            "id": f"fixture_task_{self.claims}",
                            "lease": {"fencing_token": 1},
                        }
                    }

                def renew_lease(self, *_args):
                    events.append(("renew", time.monotonic()))

            class Runtime:
                worker_id = "fixture_worker"

                def execute_claimed(self, *_args):
                    nonlocal entered_count
                    with entry_lock:
                        entered_count += 1
                        if entered_count == concurrency:
                            entered.set()
                    if not release_task.wait(5):
                        raise AssertionError("Fixture task timed out")
                    events.append(("task_finished", True))

            api = Api()
            loop = LocalWorkerLoop(
                api,
                Runtime(),
                concurrency=concurrency,
                heartbeat_interval_seconds=0.03,
                poll_interval_seconds=0.02,
                lease_renew_interval_seconds=0.03,
                token_refresh_interval_seconds=0.03,
            )
            lifecycle.start()
            thread = threading.Thread(
                target=loop.run, args=(lambda *_a: {}, lifecycle.stop)
            )
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                lifecycle.stop_file.write_text("drain")
                wait_for(lifecycle.stop.is_set)
                renewals = sum(event[0] == "renew" for event in events)
                time.sleep(0.12)
                self.assertTrue(
                    thread.is_alive(), "Drain must wait for the active task"
                )
                self.assertGreater(sum(e[0] == "renew" for e in events), renewals)
                self.assertEqual(api.claims, concurrency, "No extra tasks during drain")
            finally:
                release_task.set()
                lifecycle.stop.set()
                thread.join(5)
                lifecycle.finish()
            self.assertFalse(thread.is_alive())
            self.assertLess(
                events.index(("task_finished", True)),
                events.index(("heartbeat", False)),
            )

    def test_duplicate_host_is_rejected_by_cross_process_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / "instance.lock"
            code = (
                "from pathlib import Path;"
                "from worker_host import InstanceLock;import sys\n"
            )
            code += 'with InstanceLock(Path(sys.argv[1])): print("unexpected")\n'
            with InstanceLock(lock):
                child = subprocess.run(
                    [sys.executable, "-B", "-c", code, str(lock)],
                    capture_output=True,
                    text=True,
                    env=os.environ | {"PYTHONPATH": str(Path(__file__).parent)},
                )
            self.assertNotEqual(child.returncode, 0)
            self.assertIn("WORKER_ALREADY_RUNNING", child.stderr)

    def test_release_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "module.py"
            data.write_text("original")
            manifest = {
                "files": [
                    {
                        "path": "module.py",
                        "sha256": hashlib.sha256(b"original").hexdigest(),
                    }
                ]
            }
            (root / "runtime-manifest.json").write_text(json.dumps(manifest))
            verify_release(root)
            data.write_text("changed")
            with self.assertRaisesRegex(RuntimeError, "HASH_MISMATCH"):
                verify_release(root)

    def test_manifest_cannot_escape_release_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "release"
            root.mkdir()
            (root / "runtime-manifest.json").write_text(
                json.dumps({"files": [{"path": "../outside", "sha256": ""}]})
            )
            with self.assertRaisesRegex(RuntimeError, "FILE_MISSING"):
                verify_release(root)

    def test_cli_host_connects_only_to_fixture_and_drains(self):
        self._assert_cli_host(True)

    def test_auth_failure_is_distinct_from_a_running_process(self):
        self._assert_cli_host(False)

    def _assert_cli_host(self, authenticated):
        fixture_calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                self.assert_fixture()
                fixture_calls.append(self.path)
                if not authenticated:
                    response = json.dumps(
                        {"error": {"code": "WORKER_AUTH_INVALID"}}
                    ).encode()
                    self.send_response(401)
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                    return
                data = None
                if self.path.endswith("/heartbeat"):
                    data = {"ready": payload["ready"]}
                elif self.path.endswith("/token/refresh"):
                    data = {"session_token": "fixture-session-token-only"}
                elif not self.path.endswith("/claim"):
                    raise AssertionError("Unexpected protocol request")
                response = json.dumps({"data": data}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def assert_fixture(self):
                if not self.path.startswith("/api/ecommerce/workers/fixture_worker/"):
                    raise AssertionError("Only synthetic worker IDs are allowed")

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("POLARIS_", "AWS_", "OPENAI_"))
            }
            env.update(
                PYTHONPATH=os.pathsep.join([str(RELEASE / "backend"), str(RELEASE)]),
                PYTHONNOUSERSITE="1",
                PYTHONDONTWRITEBYTECODE="1",
                POLARIS_WORKER_API_URL=f"http://127.0.0.1:{server.server_port}",
                POLARIS_WORKER_ID="fixture_worker",
                POLARIS_WORKER_SESSION_TOKEN="fixture-session-token-only",
                POLARIS_WORKER_SESSION_TOKEN_FILE=str(root / "fixture-token"),
                POLARIS_WORKER_PROFILE_ROOT=str(root / "profiles"),
                POLARIS_WORKER_CAPABILITIES="browser.taobao",
                POLARIS_WORKER_HEARTBEAT_INTERVAL="0.1",
                POLARIS_WORKER_POLL_INTERVAL="0.05",
                POLARIS_WORKER_TOKEN_REFRESH_INTERVAL="0.1",
            )
            # Reserve a separate ephemeral port for the real local bridge.
            import socket

            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                env["POLARIS_WORKER_BRIDGE_PORT"] = str(sock.getsockname()[1])
            command = [
                sys.executable,
                "-P",
                "-B",
                "-m",
                "worker_host",
                "--release-root",
                str(RELEASE),
                "--worker-root",
                str(root),
            ]
            child = subprocess.Popen(
                command,
                env=env,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                status = root / "runtime-control/status.json"
                wait_for(
                    lambda: (
                        status.exists()
                        and (
                            json.loads(status.read_text()).get("heartbeat_successes", 0)
                            > 0
                            if authenticated
                            else json.loads(status.read_text()).get("connection")
                            == "authorization_required"
                        )
                    )
                )
                if not authenticated:
                    observed = json.loads(status.read_text())
                    self.assertEqual(observed["phase"], "running")
                    self.assertEqual(observed["heartbeat_successes"], 0)
                    self.assertEqual(
                        observed["last_heartbeat_error"], "WORKER_AUTH_INVALID"
                    )
                controller = Path(__file__).parent / "control_runtime.py"
                drained = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(controller),
                        "drain",
                        "--worker-root",
                        str(root),
                        "--timeout",
                        "5",
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(drained.returncode, 0, drained.stdout + drained.stderr)
                self.assertEqual(child.wait(5), 0)
                self.assertEqual(json.loads(status.read_text())["phase"], "stopped")
                self.assertTrue(fixture_calls)
                # A persistent stop request must prevent a new claimant after reboot.
                before = len(fixture_calls)
                stopped = subprocess.run(
                    command, env=env, cwd=root, capture_output=True, timeout=5
                )
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertEqual(len(fixture_calls), before)
            finally:
                if child.poll() is None:
                    (root / "runtime-control/stop.request").write_text("drain")
                    child.wait(5)
                child.communicate()
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
