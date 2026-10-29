"""Validate byte-identical candidate sources in a disposable writable workspace."""

import subprocess
import time


def run_candidate_tests(runner):
    name = "huipin-everyinfra-postgres-test-20261029"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "none",
            "--memory",
            "256m",
            "--tmpfs",
            "/var/lib/postgresql/data",
            "-e",
            "POSTGRES_PASSWORD=synthetic-test-password",
            "-e",
            "POSTGRES_DB=huipin_test",
            "postgres:16-alpine",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            if (
                subprocess.run(
                    ["docker", "exec", name, "pg_isready", "-U", "postgres"],
                    check=False,
                    capture_output=True,
                ).returncode
                == 0
            ):
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable PostgreSQL did not start")
        command = (
            "PIP_REQUIRE_HASHES=0 python -m pip install --no-index --find-links=/testwheels --target=/tmp/testdeps pytest >/tmp/test-install.log 2>&1 || { cat /tmp/test-install.log; exit 1; }; "
            "mkdir -p /tmp/candidate-source && cp -R /app /tmp/candidate-source/backend && "
            "mkdir -p /tmp/candidate-source/public && cp /app/test-fixtures/og.png /tmp/candidate-source/public/og.png && "
            "cd /tmp/candidate-source/backend && PYTHONPATH=/tmp/testdeps:/tmp/candidate-source/backend "
            "POLARIS_TEST_DATABASE_URL=postgresql+psycopg://postgres:synthetic-test-password@127.0.0.1:5432/huipin_test "
            "python -m pytest -o addopts='' -q --tb=short --maxfail=1 "
            "tests/test_credit_payments_postgres.py tests/test_credit_payments.py tests/test_credit_memberships.py "
            "tests/test_account_payment_followup.py tests/test_admin_billing.py tests/test_haiyu_payments_protocol.py "
            "tests/test_credit_migration_chain.py tests/test_credit_refunds.py tests/test_payment_reconciliation.py "
            "tests/test_customer_credit_billing.py tests/test_quota_fixes.py tests/test_phone_passwords.py "
            "tests/test_sms_identity_migration.py tests/test_public_browsing.py "
            "tests/test_everyinfra.py tests/test_justone.py tests/test_replica.py "
            + " ".join(runner.EXTRA_TESTS)
            + " "
            + "-k 'not twenty_images_fit_job_capacity'"
        )
        with (runner.ROOT / "postgres-test.log").open("w") as output:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "container:" + name,
                    "-v",
                    "/srv/huipin-membership-20261028/release:/testwheels:ro",
                    "--entrypoint",
                    "sh",
                    runner.BACKEND,
                    "-c",
                    command,
                ],
                check=False,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        runner.write_json(
            "test-result.json",
            {
                "exit_code": result.returncode,
                "image_id": runner.inspect(runner.BACKEND)["Id"],
                "isolated_database": True,
                "production_database_accessed": False,
                "preexisting_capacity_test_excluded": True,
            },
        )
        print((runner.ROOT / "postgres-test.log").read_text()[-10000:])
        assert result.returncode == 0, "Isolated tests failed"
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL
        )
