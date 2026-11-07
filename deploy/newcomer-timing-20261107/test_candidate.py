"""Exercise the production image with an isolated disposable PostgreSQL."""

import subprocess
import time


def run(runner):
    name = "huipin-newcomer-timing-test-" + runner.MANIFEST["revision"][:12]
    tests = [
        "tests/test_replica_free_restart.py",
        "tests/test_everyinfra.py",
        "tests/test_haiyu_transport.py",
        "tests/test_replica_image_recovery.py",
        "tests/test_growth_center.py",
        "tests/test_growth_center_postgres.py",
        "tests/test_growth_rewards.py",
        "tests/test_growth_rewards_postgres.py",
        "tests/test_replica_welcome.py",
        "tests/test_replica_welcome_postgres.py",
        "tests/test_credit_migration_chain.py",
        "tests/test_credit_payments_postgres.py",
        "tests/test_credit_payments.py",
        "tests/test_credit_memberships.py",
        "tests/test_credit_refunds.py",
        "tests/test_customer_credit_billing.py",
        "tests/test_payment_reconciliation.py",
        "tests/test_account_payment_followup.py",
        "tests/test_phone_passwords.py",
        "tests/test_auth_external.py",
        "tests/test_replica.py",
        "tests/test_replica_fast.py",
        "tests/test_replica_billing.py",
    ]
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
            "POSTGRES_DB=huipin_welcome_test",
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
            raise RuntimeError("Isolated PostgreSQL did not start")
        command = (
            "PIP_REQUIRE_HASHES=0 python -m pip install --no-index --find-links=/testwheels --target=/tmp/testdeps pytest >/tmp/install.log 2>&1 && "
            "mkdir -p /tmp/candidate/public && cp -R /app /tmp/candidate/backend && cp /app/test-fixtures/og.png /tmp/candidate/public/og.png && "
            "cd /tmp/candidate/backend && PYTHONPATH=/tmp/testdeps:/tmp/candidate/backend "
            "POLARIS_TEST_DATABASE_URL=postgresql+psycopg://postgres:synthetic-test-password@127.0.0.1:5432/huipin_welcome_test "
            "python -m pytest -o addopts='' -q --tb=short --maxfail=1 "
            + " ".join(tests)
        )
        with (runner.ROOT / "postgres-test.log").open("w") as log:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "container:" + name,
                    "--memory",
                    "2g",
                    "--cpus",
                    "2",
                    "-v",
                    "/srv/huipin-membership-20261028/release:/testwheels:ro",
                    "--entrypoint",
                    "sh",
                    runner.BACKEND,
                    "-c",
                    command,
                ],
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        runner.write_json(
            "test-result.json",
            {
                "exit_code": result.returncode,
                "image_id": runner.inspect(runner.BACKEND)["Id"],
                "isolated_database": True,
                "tests": tests,
            },
        )
        print((runner.ROOT / "postgres-test.log").read_text()[-6000:])
        assert result.returncode == 0, "Isolated candidate tests failed"
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL
        )
