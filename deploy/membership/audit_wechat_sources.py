"""Run on Haiyu's host; report config metadata without exposing secret values."""

import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import yaml

root = Path("/app/haiyushuke-server")
config = yaml.safe_load((root / "configs/platform.production.yaml").read_text())[
    "payment"
]["wxpay"]
item = json.loads(
    subprocess.check_output(["docker", "inspect", "haiyushuke-platform"])
)[0]
env = dict(value.split("=", 1) for value in item["Config"]["Env"] if "=" in value)
current = env.get("PAYMENT_WX_API_V3_KEY", "")
pattern = re.compile(
    r"""(?im)^[ \t]*(?:export\s+)?["']?"""
    r"""(PAYMENT_WX_API_V3_KEY|mch_apiv3_key|api_v3_key|apiv3_key)["']?"""
    r"""[ \t]*[:=][ \t]*([^\r\n]+)"""
)
results = []
archives = []
scanned = 0


def inspect_text(raw, label):
    for match in pattern.finditer(raw):
        value = match.group(2).strip().rstrip(",")
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                continue
        else:
            value = value.strip("'")
        if not isinstance(value, str):
            continue
        results.append(
            {
                "file": label,
                "field": match.group(1),
                "bytes": len(value.encode()),
                "ascii_alphanumeric": bool(re.fullmatch("[A-Za-z0-9]{32}", value)),
                "same_as_running_key": value == current,
                "current_merchant_present": str(config["mch_id"]) in raw,
                "current_certificate_serial_present": str(
                    config["mch_certificate_serial_number"]
                )
                in raw,
            }
        )


for base, dirs, files in os.walk(root):
    dirs[:] = [
        directory
        for directory in dirs
        if directory not in {".git", "node_modules", "logs", "uploads", "image-blobs"}
    ]
    for name in files:
        path = Path(base, name)
        if any(
            name.endswith(ext)
            for ext in [".tar.gz", ".tgz", ".zip", ".dump", ".sql.gz"]
        ):
            archives.append(str(path.relative_to(root)))
            continue
        if path.stat().st_size > 2000000 or not (
            name.startswith(".env")
            or path.suffix in {".env", ".yaml", ".yml", ".bak", ".json", ".toml"}
            or name == "env.development"
        ):
            continue
        try:
            raw = path.read_text()
        except (OSError, UnicodeError):
            continue
        scanned += 1
        inspect_text(raw, str(path.relative_to(root)))
archive_configs = 0
for name in archives:
    if not name.endswith("/configuration.tar.gz"):
        continue
    with tarfile.open(root / name) as archive:
        for member in archive.getmembers():
            if not member.isfile() or member.size > 2000000:
                continue
            path = Path(member.name)
            if not (path.name.startswith(".env") or path.suffix in {".yaml", ".yml"}):
                continue
            with archive.extractfile(member) as source:
                raw = source.read().decode("utf-8", errors="strict")
            archive_configs += 1
            inspect_text(raw, name + ":" + member.name)
print(
    json.dumps(
        {
            "configuration_files_scanned": scanned,
            "archived_configuration_files_scanned": archive_configs,
            "key_entries": results,
            "archives": archives[:40],
        }
    )
)
