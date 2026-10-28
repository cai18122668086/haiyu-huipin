"""Online append-only object backup, isolated restore and additive DDL rehearsal.

The user's concurrent detail tasks stay running. Take the database snapshot
first, then copy immutable S3 objects. Verify every live DB asset against the
restored object checksum; refuse migration if the pair is incomplete.
"""

import hashlib, json, os, secrets, subprocess, time
from pathlib import Path

root = Path("/opt/polaris/backups/20261022-one-click-replica")
root.mkdir(mode=0o700)


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def private(path, body):
    path.write_text(body)
    path.chmod(0o600)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


images = json.loads(
    Path("/opt/polaris/replica-release-20261022/images.json").read_text()
)
baseline = {
    r: inspect("polaris-production-" + r + "-1")
    for r in ("api", "postgres", "minio", "detail-api", "detail-worker")
}
private(root / "baseline.private.json", json.dumps(baseline))
with (root / "postgres.dump").open("wb") as f:
    run(
        [
            "docker",
            "exec",
            "polaris-production-postgres-1",
            "pg_dump",
            "-U",
            "polaris",
            "-d",
            "polaris",
            "-Fc",
        ],
        stdout=f,
    )
print("Database snapshot saved.", flush=True)
backup_code = r"""
import os,boto3,tarfile,sys,json,hashlib,io
s=boto3.client('s3',endpoint_url=os.environ['POLARIS_S3_ENDPOINT_URL'],aws_access_key_id=os.environ['POLARIS_S3_ACCESS_KEY'],aws_secret_access_key=os.environ['POLARIS_S3_SECRET_KEY'],region_name=os.environ['POLARIS_S3_REGION'])
b=os.environ['POLARIS_S3_BUCKET']; records=[]
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as t:
 for page in s.get_paginator('list_objects_v2').paginate(Bucket=b):
  for v in page.get('Contents',[]):
   o=s.get_object(Bucket=b,Key=v['Key']); data=o['Body'].read(); name='objects/'+str(len(records))
   info=tarfile.TarInfo(name);info.size=len(data);t.addfile(info,io.BytesIO(data))
   records.append(dict(name=name,key=v['Key'],sha256=hashlib.sha256(data).hexdigest(),size=len(data),content_type=o.get('ContentType','application/octet-stream'),metadata=o.get('Metadata',{})))
 payload=json.dumps(records).encode(); info=tarfile.TarInfo('manifest.json');info.size=len(payload);t.addfile(info,io.BytesIO(payload))
"""
with (root / "objects.tar.gz").open("wb") as f:
    run(
        ["docker", "exec", "-i", "polaris-production-api-1", "python", "-"],
        input=backup_code.encode(),
        stdout=f,
    )
print("Immutable object archive saved.", flush=True)
run(
    [
        "tar",
        "-czf",
        str(root / "aurora-work.tar.gz"),
        "-C",
        "/var/lib/docker/volumes/polaris-production_polaris_aurora_work_data/_data",
        ".",
    ]
)
print("Harness work files saved.", flush=True)
network = "polaris-replica-restore-20261022"
pg = "polaris-replica-restore-postgres-20261022"
mini = "polaris-replica-restore-minio-20261022"
assert not pg.startswith("polaris-production-") and not mini.startswith(
    "polaris-production-"
)
run(["docker", "network", "create", "--internal", network], stdout=subprocess.DEVNULL)
for name in (pg, mini):
    run(["docker", "volume", "create", name], stdout=subprocess.DEVNULL)
password = secrets.token_urlsafe(32)
private(
    root / "postgres.env",
    "POSTGRES_USER=polaris\nPOSTGRES_DB=polaris\nPOSTGRES_PASSWORD=" + password + "\n",
)
private(
    root / "minio.env",
    "MINIO_ROOT_USER=replica_restore\nMINIO_ROOT_PASSWORD=" + password + "\n",
)
run(
    [
        "docker",
        "run",
        "-d",
        "--pull=never",
        "--name",
        pg,
        "--network",
        network,
        "--memory",
        "384m",
        "--cpus",
        "0.5",
        "--env-file",
        str(root / "postgres.env"),
        "-v",
        pg + ":/var/lib/postgresql",
        baseline["postgres"]["Image"],
    ],
    stdout=subprocess.DEVNULL,
)
run(
    [
        "docker",
        "run",
        "-d",
        "--pull=never",
        "--name",
        mini,
        "--network",
        network,
        "--memory",
        "384m",
        "--cpus",
        "0.5",
        "--env-file",
        str(root / "minio.env"),
        "-v",
        mini + ":/data",
        baseline["minio"]["Image"],
        "server",
        "/data",
    ],
    stdout=subprocess.DEVNULL,
)
for n in range(40):
    if (
        subprocess.run(
            ["docker", "exec", pg, "pg_isready", "-U", "polaris"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    ):
        break
    time.sleep(1)
else:
    raise RuntimeError("Isolated PostgreSQL did not become ready")
with (root / "postgres.dump").open("rb") as f:
    run(
        [
            "docker",
            "exec",
            "-i",
            pg,
            "pg_restore",
            "-U",
            "polaris",
            "-d",
            "polaris",
            "--no-owner",
            "--no-acl",
        ],
        stdin=f,
    )
env = dict(v.split("=", 1) for v in baseline["api"]["Config"]["Env"] if "=" in v)
env.update(
    POLARIS_ENVIRONMENT="test",
    POLARIS_AUTH_MODE="development",
    POLARIS_DATABASE_URL="postgresql+psycopg://polaris:"
    + password
    + "@"
    + pg
    + ":5432/polaris",
    POLARIS_STORAGE_BACKEND="s3",
    POLARIS_S3_ENDPOINT_URL="http://" + mini + ":9000",
    POLARIS_S3_ACCESS_KEY="replica_restore",
    POLARIS_S3_SECRET_KEY=password,
    POLARIS_S3_AUTO_CREATE_BUCKET="false",
    POLARIS_AUTO_CREATE_SCHEMA="false",
    POLARIS_TASK_RUNNER="mock",
    POLARIS_TASK_DISPATCHER="inline",
    POLARIS_AUTO_PROCESS_JOBS="true",
    POLARIS_ECOMMERCE_STARTUP_RECOVERY_ENABLED="false",
    POLARIS_ECOMMERCE_LOGIN_ENABLED="false",
    POLARIS_RETENTION_ENABLED="false",
    POLARIS_PROVIDER_QUOTA_ENABLED="false",
    POLARIS_WORKSPACE_CREDIT_ENFORCEMENT_ENABLED="false",
    POLARIS_AURORA_WORK_ROOT="/tmp/aurora-work",
)
for k in list(env):
    if k.endswith(("API_KEY", "API_TOKEN")):
        env[k] = ""
private(root / "restore.env", "\n".join(k + "=" + v for k, v in env.items()) + "\n")
restore_code = r"""
import os,tarfile,json,hashlib,boto3,io,subprocess
from sqlalchemy import create_engine,text
s=boto3.client('s3',endpoint_url=os.environ['POLARIS_S3_ENDPOINT_URL'],aws_access_key_id=os.environ['POLARIS_S3_ACCESS_KEY'],aws_secret_access_key=os.environ['POLARIS_S3_SECRET_KEY'],region_name=os.environ['POLARIS_S3_REGION'])
b=os.environ['POLARIS_S3_BUCKET'];s.create_bucket(Bucket=b)
with tarfile.open('/backup/objects.tar.gz') as t:
 records=json.load(t.extractfile('manifest.json'))
 for r in records:
  data=t.extractfile(r['name']).read();assert hashlib.sha256(data).hexdigest()==r['sha256'];s.put_object(Bucket=b,Key=r['key'],Body=data,ContentType=r['content_type'],Metadata=r['metadata'])
engine=create_engine(os.environ['POLARIS_DATABASE_URL']); mapping={r['key']:r for r in records}
with engine.connect() as c:
 before=c.execute(text('SELECT version_num FROM alembic_version')).scalar()
 rows=list(c.execute(text("SELECT object_key,sha256 FROM assets WHERE storage_provider='s3' AND deleted_at IS NULL AND lifecycle_status='ready'")))
 for key,sha in rows:
  assert key in mapping, 'Database asset absent from paired objects'
  assert hashlib.sha256(s.get_object(Bucket=b,Key=key)['Body'].read()).hexdigest()==sha, 'Restored checksum mismatch'
subprocess.run(['python','-m','alembic','upgrade','head'],check=True)
with engine.connect() as c:
 assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261021_0027'
 assert c.execute(text('SELECT count(*) FROM replica_documents')).scalar()==0
print(json.dumps(dict(restored=True,objects=len(records),verified_db_assets=len(rows),schema_before=before,schema_after='20261021_0027')))
"""
with (root / "restore-result.log").open("w") as f:
    run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "-i",
            "--network",
            network,
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--read-only",
            "--tmpfs",
            "/tmp:uid=10001,gid=10001,mode=0700",
            "--env-file",
            str(root / "restore.env"),
            "-v",
            str(root / "objects.tar.gz") + ":/backup/objects.tar.gz:ro",
            images["api"],
            "python",
            "-",
        ],
        input=restore_code.encode(),
        stdout=f,
        stderr=subprocess.STDOUT,
    )
print((root / "restore-result.log").read_text()[-2000:], flush=True)
manifest = {
    "method": "database snapshot then immutable S3 copy; restored live assets verified",
    "online_reason": "User requires concurrent detail work to remain running. Additive table only; no existing data mutation.",
    "artifacts": {
        p.name: {"bytes": p.stat().st_size, "sha256": digest(p)}
        for p in [
            root / "postgres.dump",
            root / "objects.tar.gz",
            root / "aurora-work.tar.gz",
        ]
    },
    "detail_containers_unchanged": all(
        inspect("polaris-production-" + r + "-1")["Id"] == baseline[r]["Id"]
        for r in ("detail-api", "detail-worker")
    ),
}
assert manifest["detail_containers_unchanged"]
private(root / "manifest.json", json.dumps(manifest, indent=2))
# Only these two newly created restore-drill containers may be stopped.
# Never stop, restart or restore into polaris-production-postgres-1/minio-1.
assert (
    inspect("polaris-replica-restore-postgres-20261022")["Id"]
    != baseline["postgres"]["Id"]
)
assert (
    inspect("polaris-replica-restore-minio-20261022")["Id"] != baseline["minio"]["Id"]
)
run(
    [
        "docker",
        "stop",
        "--time",
        "30",
        "polaris-replica-restore-postgres-20261022",
        "polaris-replica-restore-minio-20261022",
    ],
    stdout=subprocess.DEVNULL,
)
print(
    "Paired backup, isolated object/database restore and additive migration rehearsal PASS. Production unchanged.",
    flush=True,
)
