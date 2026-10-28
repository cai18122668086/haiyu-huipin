"""Compare modified local backend files with the active API and worker."""

import hashlib
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
changed = subprocess.check_output(
    ["git", "diff", "--name-only", "--", "backend/app"], cwd=root,
).decode().splitlines()
added = subprocess.check_output(
    ["git", "ls-files", "--others", "--exclude-standard", "backend/app"], cwd=root,
).decode().splitlines()
expected = {
    name.removeprefix("backend/"): hashlib.sha256(
        (root / name).read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    for name in changed + added
}
inner = '''import hashlib,json
from pathlib import Path
expected=EXPECTED
for name,digest in expected.items():
 assert hashlib.sha256((Path('/app')/name).read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest()==digest,name
print(json.dumps({'matched_files':len(expected)}))
'''.replace("EXPECTED", repr(expected))
outer = '''import subprocess,json
code=CODE
result={}
for role in ['api','worker','dispatcher']:
 result[role]=json.loads(subprocess.check_output(['docker','exec','-i','huipin-prelaunch-'+role+'-1','python','-'],input=code.encode()))
print(json.dumps(result))
'''.replace("CODE", repr(inner))
result = json.loads(subprocess.check_output(
    ["ssh", "-o", "BatchMode=yes", "haiyu-website", "python3", "-"],
    input=outer.encode(),
))
(root / "verification/concurrency-source-verification.json").write_text(
    json.dumps(result, indent=2), encoding="utf8",
)
print(json.dumps(result))
