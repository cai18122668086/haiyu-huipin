"""Candidate recovery, migration and payment checks on disposable PostgreSQL."""

import subprocess
import time


def run(runner):
    name = "huipin-experience-postgres-test-20261030"
    tests = [
        "tests/test_stage_length_migration_postgres.py",
        "tests/test_support_cases.py",
        "tests/test_experience_pricing.py",
        "tests/test_customer_credit_billing.py",
        "tests/test_admin_billing.py",
        "tests/test_credit_payments.py",
        "tests/test_replica_creation_records.py",
        "tests/test_replica_auto_start.py",
        "tests/test_replica_automatic_recovery.py",
        "tests/test_replica_fast.py",
        "tests/test_aurora_main_image_native_fast_workflow.py",
        "tests/test_aurora_main_image_native_fast_runtime.py",
        "tests/test_aurora_main_image_native_fast_provider.py",
        "tests/test_aurora_main_image_native_fast_transport.py",
        "tests/test_aurora_main_image_fast_quota.py",
        "tests/test_main_image_auto_start.py",
        "tests/test_credit_migration_chain.py",
        "tests/test_credit_payments_postgres.py",
        "tests/test_credit_memberships.py",
        "tests/test_credit_refunds.py",
        "tests/test_phone_passwords.py",
        "tests/test_payment_reconciliation.py",
    ]
    subprocess.run(["docker", "run", "-d", "--name", name, "--network", "none",
                    "--memory", "256m", "--tmpfs", "/var/lib/postgresql/data",
                    "-e", "POSTGRES_PASSWORD=synthetic-test-password",
                    "-e", "POSTGRES_DB=huipin_experience_test", "postgres:16-alpine"],
                   check=True, stdout=subprocess.DEVNULL)
    try:
        for _ in range(30):
            if subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"],
                              capture_output=True).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Isolated PostgreSQL did not start")
        command = (
            "PIP_REQUIRE_HASHES=0 python -m pip install --no-index --find-links=/testwheels --target=/tmp/testdeps pytest >/tmp/install.log 2>&1 && "
            "mkdir -p /tmp/candidate/public && cp -R /app /tmp/candidate/backend && cp /app/test-fixtures/og.png /tmp/candidate/public/og.png && "
            "cd /tmp/candidate/backend && "
            "PYTHONPATH=/tmp/testdeps:/tmp/candidate/backend POLARIS_DATABASE_URL=postgresql+psycopg://postgres:synthetic-test-password@127.0.0.1:5432/huipin_experience_test "
            "python -m alembic -c alembic.ini upgrade head && "
            "PYTHONPATH=/tmp/testdeps:/tmp/candidate/backend "
            "POLARIS_TEST_DATABASE_URL=postgresql+psycopg://postgres:synthetic-test-password@127.0.0.1:5432/huipin_experience_test "
            "python -m pytest -o addopts='' -q --tb=short --maxfail=1 " + " ".join(tests)
        )
        with (runner.ROOT / "postgres-test.log").open("w") as output:
            result = subprocess.run(["docker", "run", "--rm", "--network", "container:" + name,
                                     "--memory", "2g", "--cpus", "2", "-v",
                                     "/srv/huipin-membership-20261028/release:/testwheels:ro",
                                     "--entrypoint", "sh", runner.BACKEND, "-c", command],
                                    stdout=output, stderr=subprocess.STDOUT)
        runner.write_json("test-result.json", {"exit_code": result.returncode,
                          "image_id": runner.inspect(runner.BACKEND)["Id"],
                          "isolated_database": True, "tests": tests})
        print((runner.ROOT / "postgres-test.log").read_text()[-5000:])
        assert result.returncode == 0, "Candidate tests failed"
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL)
