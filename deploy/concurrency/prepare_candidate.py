"""Package only changed backend files, with baseline digests for safe patching."""

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
output = root / "release-package/concurrency-20261028"
output.mkdir(parents=True, exist_ok=True)
changed = (
    subprocess.check_output(
        ["git", "diff", "--name-only", "--", "backend/app"],
        cwd=root,
    )
    .decode()
    .splitlines()
)
added = (
    subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "backend/app"],
        cwd=root,
    )
    .decode()
    .splitlines()
)
baseline = {}


def digest(content):
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


with tarfile.open(output / "patch.tar.gz", "w:gz") as archive:
    for name in changed + added:
        target = str(Path(name).relative_to("backend")).replace("\\", "/")
        old = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=root, capture_output=True
        )
        baseline[target] = digest(old.stdout) if old.returncode == 0 else None
        archive.add(root / name, arcname="patch/" + target)
    for name in [
        "test_provider_capacity.py",
        "test_task_runner_selection.py",
        "test_haiyu_transport.py",
        "test_p0_gates.py",
        "test_xinghe_agent.py",
        "test_xinghe_single_image_provider.py",
        "test_execution_reliability.py",
        "test_replica_analysis_attempt.py",
        "test_replica_analysis_recovery.py",
        "conftest.py",
    ]:
        path = root / "backend/tests" / name
        if path.exists():
            archive.add(path, arcname="tests/" + name)
    for path in Path(__file__).parent.glob("*.py"):
        archive.add(path, arcname="tools/" + path.name)
    content = json.dumps(baseline, indent=2).encode()
    info = tarfile.TarInfo("baseline.json")
    info.size = len(content)
    archive.addfile(info, io.BytesIO(content))
print(
    json.dumps({"patch_files": len(baseline), "archive": str(output / "patch.tar.gz")})
)
