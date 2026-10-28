"""Read-only runtime audit. Never print environment values or credentials."""

import json
import subprocess


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


worker = inspect("huipin-prelaunch-worker-1")
api = inspect("huipin-prelaunch-api-1")
probe = """
import json
from sqlalchemy import create_engine,text
from app.config import Settings
s=Settings()
with create_engine(s.database_url).connect() as c:
 print(json.dumps({
  'image_concurrency':s.xinghe_concurrency,
  'analysis_concurrency':s.replica_analysis_concurrency,
  'active_jobs':c.execute(text("select count(*) from jobs where status in ('queued','planning','processing','cancelling')")).scalar(),
  'workspace_limits':[list(row) for row in c.execute(text('select max_active_jobs,max_concurrent_jobs,count(*) from workspace_limits group by 1,2'))],
  'migration':c.execute(text('select version_num from alembic_version')).scalar(),
  'postgres_max_connections':c.execute(text('show max_connections')).scalar()
 }))
"""
runtime = json.loads(
    subprocess.check_output(
        ["docker", "exec", "-i", "huipin-prelaunch-api-1", "python", "-"],
        input=probe.encode(),
    )
)
report = {
    "worker_command": worker["Config"]["Cmd"],
    "worker_memory_bytes": worker["HostConfig"]["Memory"],
    "worker_pids_limit": worker["HostConfig"]["PidsLimit"],
    "backend_image": api["Config"]["Image"],
    "compose_files": api["Config"]["Labels"]["com.docker.compose.project.config_files"],
    "runtime": runtime,
}
print(json.dumps(report, indent=2))
