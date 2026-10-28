"""Exercise financial races in a disposable PostgreSQL container."""

import json
import subprocess
import time
from pathlib import Path

root = Path("/srv/huipin-membership-20261028")
name = "huipin-billing-postgres-test-20261028"
subprocess.run(["docker", "run", "-d", "--name", name, "--network", "none",
                "--label", "codex.membership-test=20261028", "--memory", "256m",
                "--tmpfs", "/var/lib/postgresql/data", "-e", "POSTGRES_PASSWORD=synthetic-test-password",
                "-e", "POSTGRES_DB=huipin_test", "postgres:16-alpine"], check=True, stdout=subprocess.DEVNULL)
try:
    for _ in range(30):
        ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"], capture_output=True)
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        raise SystemExit("Disposable database did not start")
    command = (
        "PIP_REQUIRE_HASHES=0 python -m pip install --no-index --find-links=/testwheels --target=/tmp/testdeps pytest >/tmp/test-install.log 2>&1 || { cat /tmp/test-install.log; exit 1; };  "
        "PYTHONPATH=/tmp/testdeps:/app "
        "POLARIS_TEST_DATABASE_URL=postgresql+psycopg://postgres:synthetic-test-password@127.0.0.1:5432/huipin_test "
        "python -m pytest -o addopts='' -q --tb=short "
        "tests/test_credit_payments_postgres.py tests/test_credit_payments.py "
        "tests/test_credit_memberships.py tests/test_account_payment_followup.py "
        "tests/test_admin_billing.py tests/test_haiyu_payments_protocol.py tests/test_credit_migration_chain.py "
        "tests/test_customer_credit_billing.py tests/test_quota_fixes.py "
        "tests/test_phone_passwords.py tests/test_sms_identity_migration.py tests/test_public_browsing.py"
    )
    with (root / "postgres-test.log").open("w") as output:
        result = subprocess.run(["docker", "run", "--rm", "--network", "container:" + name,
            "-v", str(root / "release") + ":/testwheels:ro", "--entrypoint", "sh",
            "huipin-membership/backend:20261028", "-c", command], stdout=output, stderr=subprocess.STDOUT)
    print((root / "postgres-test.log").read_text()[-10000:])
    (root / "postgres-test-result.json").write_text(json.dumps({"exit_code": result.returncode,
        "isolated_database": True, "original_platform_database_accessed": False}))
    raise SystemExit(result.returncode)
finally:
    subprocess.run(["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL)
