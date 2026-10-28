"""Publish a lint-only string wrap, proving identical Python AST first."""

import ast
import json
import subprocess
import time
from pathlib import Path

root = Path("/srv/huipin-concurrency-20261028")
image = "huipin-concurrency/backend:20261028"
tested = "huipin-concurrency/backend:20261028-tested"


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


old = subprocess.check_output(["docker", "exec", "huipin-prelaunch-api-1", "cat", "/app/app/task_runner.py"])
new = (root / "task_runner-final.py").read_bytes()
assert ast.dump(ast.parse(old)) == ast.dump(ast.parse(new))
before_id = inspect(image)["Id"]
subprocess.run(["docker", "tag", image, tested], check=True)
(root / "Dockerfile.format").write_text("FROM " + tested + "\nCOPY task_runner-final.py /app/app/task_runner.py\n")
subprocess.run(["docker", "build", "--pull=false", "-f", str(root / "Dockerfile.format"), "-t", image, str(root)], check=True)
compose = ["docker", "compose", "--project-name", "huipin-prelaunch", "--env-file",
           "/etc/huipin-prelaunch/prelaunch.env", "-f", "/srv/huipin-prelaunch/runtime/compose.yml",
           "-f", "/srv/huipin-membership-20261028/billing.override.yml"]
roles = ["api", "worker", "dispatcher"]
try:
    subprocess.run(compose + ["up", "-d", "--no-deps", *roles], check=True)
    for _ in range(60):
        if all(inspect("huipin-prelaunch-" + role + "-1")["State"].get("Health", {}).get("Status") == "healthy" for role in roles):
            break
        time.sleep(2)
    else:
        raise RuntimeError("Formatting build failed health checks")
except BaseException:
    subprocess.run(["docker", "tag", tested, image], check=True)
    subprocess.run(compose + ["up", "-d", "--no-deps", *roles], check=True)
    raise
report = {"tested_image_id": before_id, "active_image_id": inspect(image)["Id"],
          "python_ast_identical": True, "change": "wrap one error-message literal to satisfy line length"}
(root / "format-equivalence.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
