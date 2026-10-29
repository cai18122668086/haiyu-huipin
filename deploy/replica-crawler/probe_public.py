"""Real public gateway + disposable synthetic identity; never send SMS."""

import base64
import http.cookiejar
import json
import subprocess
import time
import urllib.request
from pathlib import Path


def execute(code):
    result = subprocess.run(
        ["docker", "exec", "-i", "huipin-prelaunch-api-1", "python", "-"],
        input=code,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-900:])
    return json.loads(result.stdout.strip().splitlines()[-1])


create = r"""
import json,secrets,sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/app')
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app.config import Settings
from app.main import create_app
from app.sms_auth import TencentSmsSender
from app.models import UserRecord
s=Settings();engine=create_engine(s.database_url)
phone='+86188'+''.join(str(secrets.randbelow(10)) for _ in range(8))
with Session(engine) as db: assert db.scalar(select(UserRecord.id).where(UserRecord.phone_number==phone)) is None
codes=[]
def fake_send(_self,p,c,_life):
 assert p==phone
 codes.append(c)
 return 'synthetic-no-sms'
with tempfile.TemporaryDirectory() as directory:
 p=Path(directory)
 settings=s.model_copy(update={'database_url':'sqlite:///'+(p/'test.db').as_posix(),'storage_dir':p/'storage','aurora_work_root':p/'work','auto_create_schema':True,'environment':'test','storage_backend':'local','ecommerce_startup_recovery_enabled':False})
 with patch.object(TencentSmsSender,'send',fake_send),TestClient(create_app(settings),base_url='https://huipin.haiyushuke.com') as client:
  result=client.post('/api/auth/signinup/code',headers={'rid':'passwordless'},json={'phoneNumber':phone,'shouldTryLinkingWithSessionUser':False})
  attempt=result.json()
  assert attempt['status']=='OK' and len(codes)==1
  print(json.dumps({'phone':phone,'attempt':attempt,'code':codes[0]}))
engine.dispose()
"""
data = execute(create)
attempt = data["attempt"]
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
headers = {
    "Content-Type": "application/json",
    "rid": "passwordless",
    "fdi-version": "3.1,4.0,4.1,4.2",
    "st-auth-mode": "cookie",
    "Origin": "https://huipin.haiyushuke.com",
}
payload = {
    "deviceId": attempt["deviceId"],
    "preAuthSessionId": attempt["preAuthSessionId"],
    "userInputCode": data["code"],
    "shouldTryLinkingWithSessionUser": False,
    "acceptedAgreements": True,
    "flow": "login",
}
uid = None
report = {}
try:
    request = urllib.request.Request(
        "https://huipin.haiyushuke.com/api/auth/signinup/code/consume",
        data=json.dumps(payload).encode(),
        headers=headers,
    )
    with opener.open(request, timeout=20) as response:
        body = json.load(response)
        assert body["status"] == "OK"
        uid = body["user"]["id"]
        front = response.headers.get_all("front-token")
        front_valid = len(front) == 1 and isinstance(
            json.loads(base64.b64decode(front[0], validate=True)), dict
        )
        cookie_names = [cookie.name for cookie in jar]
        anti_csrf = response.headers.get("anti-csrf")
        assert anti_csrf
        authenticated_headers = {
            "Content-Type": "application/json",
            "rid": "session",
            "anti-csrf": anti_csrf,
            "Origin": "https://huipin.haiyushuke.com",
        }
    with opener.open("https://huipin.haiyushuke.com/api/v1/me", timeout=20) as response:
        me = json.load(response)
        assert response.status == 200
    with opener.open(
        "https://huipin.haiyushuke.com/api/v1/workspaces", timeout=20
    ) as response:
        workspace = json.load(response)[0]["id"]
    with opener.open(
        "https://huipin.haiyushuke.com/api/v1/workspaces/"
        + workspace
        + "/credits/packages?include_memberships=true",
        timeout=20,
    ) as response:
        catalog = json.load(response)
        assert (
            catalog["enabled"] is True
            and catalog["channels"] == ["alipay", "wechat"]
            and len(catalog["packages"]) == 9
        )
    with opener.open(
        "https://huipin.haiyushuke.com/api/v1/workspaces/" + workspace + "/credits",
        timeout=20,
    ) as response:
        account = json.load(response)
        assert (
            account["enforcement_enabled"] is True and account["balance_credits"] == 0
        )
    with opener.open(
        "https://huipin.haiyushuke.com/api/v1/workspaces/"
        + workspace
        + "/replica/product-platforms",
        timeout=20,
    ) as response:
        sources = json.load(response)
        assert len(sources) == 12
    base = "https://huipin.haiyushuke.com/api/v1/workspaces/" + workspace + "/replica"
    share = "https://e.tb.cn/h.jg61u1APU5yXxB5?tk=4gYdTBpoxZG"
    request = urllib.request.Request(
        base + "/products",
        data=json.dumps({"url": share}).encode(),
        headers=authenticated_headers,
    )
    with opener.open(request, timeout=45) as response:
        document = json.load(response)
        assert response.status == 202
    deadline = time.monotonic() + 660
    while time.monotonic() < deadline:
        with opener.open(base + "/documents/" + document["id"], timeout=30) as response:
            document = json.load(response)
        if document["status"] in ("ready", "failed"):
            break
        time.sleep(3)
    assert document["status"] == "ready", {
        "status": document["status"],
        "error": document["payload"].get("error_code"),
    }
    product = document["payload"]["product"]
    assert product["supplier"] == "everyinfra"
    assert (
        len(product["imageUrls"]) > 0
        and len(product["descImageUrls"]) > 0
        and len(product["skuImageUrls"]) > 0
    )
    assert "_product_provider_state" not in document["payload"]
    assert "_product_fetch_attempt" not in document["payload"]
    request = urllib.request.Request(
        base + "/products",
        data=json.dumps({"url": document["payload"]["canonical_url"]}).encode(),
        headers=authenticated_headers,
    )
    with opener.open(request, timeout=30) as response:
        cached = json.load(response)
    assert cached["id"] == document["id"] and cached["status"] == "ready"
    crawler = {
        "source_count": len(sources),
        "default_supplier": product["supplier"],
        "mobile_share_resolved": True,
        "counts": {
            k: len(product[k]) for k in ("imageUrls", "descImageUrls", "skuImageUrls")
        },
        "same_day_cache_reused": True,
        "private_provider_state_hidden": True,
        "cost_usd": product.get("costUsd"),
    }
    import urllib.error

    try:
        opener.open(
            "https://huipin.haiyushuke.com/api/v1/admin/billing/users", timeout=20
        )
        raise AssertionError("Customer was allowed to read admin data")
    except urllib.error.HTTPError as error:
        assert error.code == 403
    report = {
        "payment_catalog_enabled": True,
        "channels": ["alipay", "wechat"],
        "commercial_products": 9,
        "new_customer_initial_credits": 0,
        "customer_admin_access_denied": True,
        "public_consume_status": "OK",
        "front_token_decodes": front_valid,
        "front_token_header_count": len(front),
        "stored_cookie_names": cookie_names,
        "public_authenticated_me_status": 200,
        "real_sms_sent": 0,
        "generation_requests": 0,
        "crawler": crawler,
    }
finally:
    cleanup = r"""
import hashlib,json,sys
sys.path.insert(0,'/app')
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app.config import Settings
from app.main import create_app
from app.models import UserRecord,WorkspaceRecord
from supertokens_python.syncio import delete_user
from redis import Redis
s=Settings();create_app(s.model_copy(update={'ecommerce_startup_recovery_enabled':False}))
data=DATA
if data['uid']:
 engine=create_engine(s.database_url)
 with Session(engine) as db:
  user=db.scalar(select(UserRecord).where(UserRecord.auth_subject==data['uid'],UserRecord.phone_number==data['phone']))
  assert user is not None
  workspaces=list(db.scalars(select(WorkspaceRecord).where(WorkspaceRecord.created_by_user_id==user.id)))
  assert len(workspaces)==1
  db.delete(workspaces[0]);db.flush();db.delete(user);db.commit()
 delete_user(data['uid']);engine.dispose()
redis=Redis.from_url(s.auth_sms_redis_url)
redis.delete('polaris:auth:sms:v1:cooldown:'+hashlib.sha256(data['phone'].encode()).hexdigest(),'polaris:auth:sms:v1:daily:'+hashlib.sha256(data['phone'].encode()).hexdigest(),'polaris:auth:sms:v1:attempt:'+hashlib.sha256(data['attempt'].encode()).hexdigest())
redis.close()
print(json.dumps({'synthetic_account_cleaned':True}))
"""
    cleanup = cleanup.replace(
        "data=DATA",
        "data="
        + repr(
            {"uid": uid, "phone": data["phone"], "attempt": attempt["preAuthSessionId"]}
        ),
    )
    report.update(execute(cleanup))
Path("/srv/huipin-everyinfra-20261029/public-integrated-probe.json").write_text(
    json.dumps(report, indent=2)
)
print(json.dumps(report))
