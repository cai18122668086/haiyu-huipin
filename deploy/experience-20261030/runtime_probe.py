"""Read-only production proof; prints no credentials and triggers no paid work."""

import json
from pathlib import Path

import release_base as runner

runner.ROOT = Path(__file__).resolve().parent
code = """import json,sys
from app.credit_billing import PRICING_VERSION, image_unit_credits
from app.config import Settings
from app.replica import AnalysisRequest
from app.replica_exact_compact import protocol_binding,run_class
from app.replica_direct_delivery import V15_BINDING
from app.replica_fast import CONCURRENCY
from app.native_portrait_delivery import style_native_budget_matches
from app.xinghe_agent import _BOUNDED_JSON_PLANNER_PROFILES
from app.refined_detail import PROFILE
s=Settings()
assert PRICING_VERSION=='image-credits-5-8-15-v2'
assert image_unit_credits('detail_pack',{'settings':{'detail_speed':'fast'}})==8
assert image_unit_credits('detail_pack')==15
assert image_unit_credits('creative_image',{'inspiration_case_id':'validated','inspiration_workflow':'product_prompt'})==5
p=protocol_binding()
assert p=={'version':'exact-compact-v16','content_sha256':'63df401796fde30686b4485af519a2f5ee0f3a3577cfd2225dfc352d2cd2c59c'}
legacy=run_class('exact',V15_BINDING)
assert sys.modules[legacy.__module__].protocol_binding()==V15_BINDING
assert CONCURRENCY==20 and s.image_global_concurrency==3000 and s.worker_async_runtime_enabled
assert s.credit_wechat_confirmation_mode=='query' and s.credit_payment_reconciliation_enabled
assert {'amazon_visual_suite',PROFILE} <= _BOUNDED_JSON_PLANNER_PROFILES
assert style_native_budget_matches([1440,1920],(1086,1448))
assert not style_native_budget_matches([1440,1920],(768,1024))
assert not style_native_budget_matches([1440,1920],(1254,1254))
assert AnalysisRequest.model_json_schema()['properties']['replica_speed']['default']=='standard'
print(json.dumps({'current_protocol':p,'legacy_v15_preserved':True,'fast_concurrency':CONCURRENCY,'image_global_concurrency':s.image_global_concurrency,'worker_async_runtime':s.worker_async_runtime_enabled,'wechat_query_reconciliation':True,'bounded_refined_and_amazon_planners':True,'native_portrait_acceptance':True,'preview_and_wrong_aspect_rejected':True}))
"""
result = runner.inner(code)
worker = runner.inspect("huipin-prelaunch-worker-1")
command = worker["Config"]["Cmd"]
assert "--concurrency=3000" in command
assert "--queues=default,one_click_replica,domestic_detail_v431" in command
result["worker_command"] = command
result["health"] = {
    name: runner.inspect("huipin-prelaunch-" + name + "-1")["State"]["Health"]["Status"]
    for name in ("api", "worker", "dispatcher", "frontend")
}
assert set(result["health"].values()) == {"healthy"}
runner.write_json("runtime-proof.json", result)
print(json.dumps(result))
