"""Verify active settings, queues, provider connectivity and public availability."""

import json
import subprocess
from pathlib import Path
from urllib.request import urlopen

ROOT = Path("/srv/huipin-concurrency-20261028")
code = """import asyncio,json
from urllib.request import urlopen
from urllib.parse import urljoin
from sqlalchemy import create_engine,text
from app.config import Settings
from app.celery_app import celery_app
from app.async_provider_transport import AsyncProviderTransport,close_async_provider_session
s=Settings()
assert s.xinghe_concurrency==s.image_global_concurrency==3000
assert s.replica_analysis_concurrency==0
assert s.worker_async_runtime_enabled and s.provider_async_transport_enabled
inspector=celery_app.control.inspect(timeout=10)
stats=inspector.stats(); queues=inspector.active_queues()
assert len(stats)==1
pool=next(iter(stats.values()))['pool']
assert pool['max-concurrency']==3000 and 'thread' in pool['implementation']
queue_names=sorted(q['name'] for q in next(iter(queues.values())))
assert queue_names==['default','domestic_detail_v431','one_click_replica']
with create_engine(s.database_url).connect() as c:
 limits=[list(row) for row in c.execute(text('select max_active_jobs,max_concurrent_jobs,count(*) from workspace_limits group by 1,2'))]
 migration=c.execute(text('select version_num from alembic_version')).scalar()
 assert migration=='20261028_0033'
with urlopen('http://127.0.0.1:8000/health/ready',timeout=30) as response:
 ready=response.status
 assert ready==200
async def probe():
 result={}
 try:
  for role,base,key in [('analysis',s.analysis_base_url,s.analysis_api_key.get_secret_value() or s.tengli_api_key.get_secret_value()),('image',s.image_base_url,s.image_api_key.get_secret_value() or s.tengli_api_key.get_secret_value())]:
   status,payload=await AsyncProviderTransport().get_json(urljoin(base.rstrip('/')+'/', 'v1/models'),{'Authorization':'Bearer '+key},30)
   result[role]={'models_get_status':status}
  return result
 finally:
  await close_async_provider_session()
providers=asyncio.run(probe())
print(json.dumps({'image_global_concurrency':s.image_global_concurrency,'image_local_concurrency':s.xinghe_concurrency,'analysis_limit':s.replica_analysis_concurrency,'worker_pool':pool,'queues':queue_names,'workspace_limits':limits,'readiness_status':ready,'migration':migration,'provider_readonly_connectivity':providers,'paid_probe_requests':0}))
"""
runtime = json.loads(
    subprocess.check_output(
        ["docker", "exec", "-i", "huipin-prelaunch-api-1", "python", "-"],
        input=code.encode(),
    )
)
health = {}
for role in ["api", "worker", "dispatcher"]:
    info = json.loads(
        subprocess.check_output(
            ["docker", "inspect", "huipin-prelaunch-" + role + "-1"]
        )
    )[0]
    assert info["State"]["Health"]["Status"] == "healthy"
    assert info["Config"]["Image"] == "huipin-concurrency/backend:20261028"
    health[role] = {"health": "healthy", "restart_count": info["RestartCount"]}
with urlopen("https://huipin.haiyushuke.com/account", timeout=30) as response:
    assert response.status == 200
report = {"runtime": runtime, "services": health, "public_account_status": 200}
(ROOT / "runtime-verification.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
