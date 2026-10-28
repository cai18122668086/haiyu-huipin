"""Run a disposable native replica acceptance container; never replace services."""
import argparse
import json
import os
import subprocess
from pathlib import Path


def main(method, run_tag=None, plan_only=False, fresh_campaign=False):
    root = Path("/opt/polaris/replica-native-20261022")
    source = json.loads(subprocess.check_output(["docker", "inspect", "polaris-production-replica-api-1"]))[0]
    private = root / "validation.env.private"
    values = source["Config"]["Env"]
    assert not any("\n" in row for row in values)
    private.write_text("\n".join(values) + "\n", encoding="utf-8")
    os.chmod(private, 0o600)
    validation = root / "validation"
    validation.mkdir(exist_ok=True)
    if method == "exact":
        patch = json.loads((root / "backend/source_manifests/aurora-ecommerce-reference-remix-polaris-patches.json").read_text())
        # Only the private acceptance fixture may move across this precise,
        # audited failure-transition fix. Production sessions remain immutable.
        for path in (validation / "outputs/native-live").glob("exact-*-native.json"):
            snapshot = json.loads(path.read_text())
            if snapshot.get("source_hash") == patch["original_runtime_hash"]:
                snapshot["source_hash"] = patch["patched_runtime_hash"]
                snapshot["acceptance_fixture_source_patch"] = patch["patch_id"]
                path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    subprocess.run(["sudo", "chown", "-R", "10001:10001", str(validation)], check=True)
    name = "polaris-replica-native-validation-" + method + ("-" + run_tag if run_tag else "")
    networks = list(source["NetworkSettings"]["Networks"])
    network = next((name for name in networks if "provider-egress" in name), next(name for name in networks if not json.loads(subprocess.check_output(["docker", "network", "inspect", name]))[0]["Internal"]))
    command = ["docker", "run", "--rm", "--name", name,
        "--network", network,
        "--memory", "1g", "--cpus", "1", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp:rw,noexec,nosuid,size=512m",
        "--env-file", str(private), "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONPATH=/app",
        "--mount", "type=bind,src=" + str(validation) + ",dst=/validation", "--workdir", "/validation",
        "polaris/replica-native-candidate:20261022", "python", "-u", "/app/acceptance/runner.py", method]
    if run_tag:
        command += ["--run-tag", run_tag]
    if plan_only:
        command += ["--plan-only"]
    if fresh_campaign:
        command += ["--fresh-campaign"]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("method", choices=["exact", "style"])
    parser.add_argument("--run-tag", choices=["verified", "final"])
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--fresh-campaign", action="store_true")
    args = parser.parse_args()
    main(args.method, args.run_tag, args.plan_only, args.fresh_campaign)
