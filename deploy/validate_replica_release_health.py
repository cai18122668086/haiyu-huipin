"""Prove literal-dollar environment and shell health preservation in isolation."""
import importlib.util
import json
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("release", root / "release_replica_native.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
name = "polaris-native-health-regression"
compose = root / "health-regression.compose.json"
probe = "test \"$PROOF_LITERAL\" = 'cost$5' && test \"$HOSTNAME\" = \"$(cat /etc/hostname)\""
service = {"image": "polaris/replica-native-backend:20261022", "container_name": name,
           "network_mode": "none", "read_only": True, "cap_drop": ["ALL"],
           "security_opt": ["no-new-privileges:true"], "mem_limit": "128m", "cpus": 0.25}
release.preserve_runtime_fields(service, {"Config": {
    "Env": ["PROOF_LITERAL=cost$5"], "Cmd": ["python", "-B", "-c", "import time;time.sleep(60)"],
    "Entrypoint": None, "Healthcheck": {"Test": ["CMD-SHELL", probe],
        "Interval": 1_000_000_000, "Timeout": 3_000_000_000, "StartPeriod": 1_000_000_000, "Retries": 2}}})
assert subprocess.run(["docker", "inspect", name], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, check=False).returncode != 0, "Proof container already exists"
compose.write_text(json.dumps({"name": name, "services": {"proof": service}}))
created = False
try:
    release.compose(compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "proof")
    created = True
    for _ in range(20):
        live = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
        assert live["Config"]["Labels"]["com.docker.compose.project"] == name
        if live["State"]["Health"]["Status"] == "healthy":
            break
        time.sleep(1)
    else:
        raise RuntimeError("Correctly escaped probe did not become healthy")
    assert "PROOF_LITERAL=cost$5" in live["Config"]["Env"]
    broken = subprocess.run(["docker", "exec", name, "sh", "-c", probe.replace("$", "$$")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    assert broken.returncode != 0, "Old double escaping unexpectedly passed"
    print(json.dumps({"isolated_health_regression_passed": True, "literal_dollar_preserved": True,
                      "old_double_escaping_rejected": True, "supplier_requests": 0}))
finally:
    if created:
        live = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
        assert live["Name"] == "/" + name
        assert live["Config"]["Labels"]["com.docker.compose.project"] == name
        subprocess.run(["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL)
    compose.unlink(missing_ok=True)
