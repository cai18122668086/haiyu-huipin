from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))

from post_deploy_queue_check import main, validate_active_queues


class PostDeployQueueCheckTests(unittest.TestCase):
    def valid_payload(self) -> dict[str, list[dict[str, str]]]:
        return {
            "celery@api-worker": [{"name": "default"}],
            "crawl@crawl-worker": [{"name": "crawl"}],
        }

    def test_valid_active_queues_pass(self) -> None:
        self.assertEqual(validate_active_queues(self.valid_payload()), [])

    def test_wrong_queue_is_rejected(self) -> None:
        payload = self.valid_payload()
        payload["celery@api-worker"] = [{"name": "crawl"}]
        errors = validate_active_queues(payload)
        self.assertTrue(any("celery@api-worker" in error for error in errors))
        self.assertTrue(any("queue(s) default" in error for error in errors))

    def test_missing_worker_class_is_rejected(self) -> None:
        errors = validate_active_queues({"celery@api-worker": [{"name": "default"}]})
        self.assertIn("no active worker matched the required crawl@ node prefix", errors)

    def test_malformed_payload_is_rejected(self) -> None:
        self.assertEqual(
            validate_active_queues("not-json"),
            ["active_queues output was not valid JSON"],
        )
        self.assertEqual(
            validate_active_queues(json.dumps({"celery@api-worker": {}})),
            [
                "celery@api-worker returned an invalid queue list",
                "no active worker matched the required crawl@ node prefix",
            ],
        )

    def test_unknown_worker_name_is_rejected_without_echoing_payload(self) -> None:
        errors = validate_active_queues({"secret-node": [{"name": "default"}]})
        self.assertIn("secret-node has an unrecognized worker name prefix", errors)
        self.assertNotIn("default", " ".join(errors))

    def test_cli_reads_stdin_and_returns_success(self) -> None:
        with patch("sys.stdin", StringIO(json.dumps(self.valid_payload()))):
            output = StringIO()
            with patch("sys.stdout", output):
                code = main([])
        self.assertEqual(code, 0)
        self.assertIn("PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
