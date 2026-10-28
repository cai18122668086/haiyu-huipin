from __future__ import annotations

import json
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from typing import ClassVar

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))

from post_deploy_smoke import main, run_smoke


class _HealthHandler(BaseHTTPRequestHandler):
    payloads: ClassVar[dict[str, tuple[int, object]]] = {}

    def do_GET(self) -> None:
        status, payload = self.payloads.get(self.path, (404, {"error": "not found"}))
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


class PostDeploySmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        _HealthHandler.payloads = {
            "/health/live": (200, {"status": "ok", "checks": {"process": "ok"}}),
            "/health/ready": (
                200,
                {
                    "status": "ok",
                    "checks": {
                        "database": "ok",
                        "task_broker": "ok",
                        "object_storage": "ok",
                        "authentication": "ok",
                    },
                },
            ),
            "/health/ecommerce": (
                200,
                {
                    "status": "ok",
                    "metrics": {
                        "task_counts": {},
                        "active_leases": 0,
                        "expired_leases": 0,
                        "oldest_queued_age_seconds": 0.0,
                    },
                },
            ),
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_all_health_contracts_pass(self) -> None:
        results = run_smoke(self.base_url)
        self.assertEqual([result.error for result in results], [None, None, None])
        self.assertEqual([result.status_code for result in results], [200, 200, 200])

    def test_ready_requires_all_production_dependencies(self) -> None:
        payload = _HealthHandler.payloads["/health/ready"][1]
        assert isinstance(payload["checks"], dict)
        payload["checks"]["authentication"] = "failed"
        results = run_smoke(self.base_url)
        self.assertIn("authentication", results[1].error or "")

    def test_ecommerce_health_rejects_missing_metric(self) -> None:
        payload = _HealthHandler.payloads["/health/ecommerce"][1]
        assert isinstance(payload["metrics"], dict)
        del payload["metrics"]["expired_leases"]
        results = run_smoke(self.base_url)
        self.assertEqual(results[2].error, "metric is missing: expired_leases")

    def test_malformed_json_shape_is_reported_without_traceback(self) -> None:
        _HealthHandler.payloads["/health/ecommerce"] = (200, ["unexpected"])
        results = run_smoke(self.base_url)
        self.assertEqual(results[2].error, "response JSON was not an object")

    def test_live_rejects_malformed_checks_without_traceback(self) -> None:
        _HealthHandler.payloads["/health/live"] = (200, {"status": "ok", "checks": []})
        results = run_smoke(self.base_url)
        self.assertEqual(results[0].error, "checks object is missing")

    def test_cli_returns_blocked_without_printing_response_body(self) -> None:
        _HealthHandler.payloads["/health/live"] = (503, {"secret": "must-not-print"})
        output = StringIO()
        with redirect_stdout(output):
            code = main(["--base-url", self.base_url])
        self.assertEqual(code, 1)
        self.assertIn("FAIL /health/live: HTTP 503", output.getvalue())
        self.assertNotIn("must-not-print", output.getvalue())


if __name__ == "__main__":
    unittest.main()
