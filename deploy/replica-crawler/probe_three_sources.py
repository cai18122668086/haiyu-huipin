"""Real public product previews, complete imports, and readback in a disposable account."""

import ast
import hashlib
import http.cookiejar
import json
import os
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(
    os.environ.get(
        "HUIPIN_THREE_SOURCE_ROOT", "/srv/huipin-three-source-verification-20261029"
    )
)
ORIGIN = "https://huipin.haiyushuke.com"


def execute(code):
    result = subprocess.run(
        ["docker", "exec", "-i", "huipin-prelaunch-api-1", "python", "-"],
        input=code,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-1200:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def save(name, value):
    path = ROOT / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def main():
    assert not (ROOT / "run-started.json").exists(), (
        "Inspect the existing run before another paid trial"
    )
    save("run-started.json", {"started": True})
    source = ast.parse((ROOT / "probe_public.py").read_text())
    create = next(
        ast.literal_eval(n.value)
        for n in ast.walk(source)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Constant)
        and any(isinstance(t, ast.Name) and t.id == "create" for t in n.targets)
    )
    identity = execute(create)
    attempt = identity["attempt"]
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    uid = workspace = None
    report = {
        "production": True,
        "generation_requests": 0,
        "real_sms_sent": 0,
        "cases": {},
    }
    auth_headers = {}

    def request_json(path, body=None, timeout=180):
        request = urllib.request.Request(
            urljoin(ORIGIN, path),
            data=json.dumps(body).encode() if body is not None else None,
            headers=auth_headers,
        )
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)

    def read_image(path):
        with opener.open(urljoin(ORIGIN, path), timeout=60) as response:
            data = response.read(20 * 1024 * 1024 + 1)
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("image/")
            assert 0 < len(data) <= 20 * 1024 * 1024
            return hashlib.sha256(data).hexdigest()

    try:
        login = urllib.request.Request(
            ORIGIN + "/api/auth/signinup/code/consume",
            data=json.dumps(
                {
                    "deviceId": attempt["deviceId"],
                    "preAuthSessionId": attempt["preAuthSessionId"],
                    "userInputCode": identity["code"],
                    "shouldTryLinkingWithSessionUser": False,
                    "acceptedAgreements": True,
                    "flow": "login",
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "rid": "passwordless",
                "st-auth-mode": "cookie",
                "fdi-version": "3.1,4.0,4.1,4.2",
                "Origin": ORIGIN,
            },
        )
        with opener.open(login, timeout=30) as response:
            body = json.load(response)
            assert body["status"] == "OK"
            uid = body["user"]["id"]
            csrf = response.headers["anti-csrf"]
        auth_headers.update(
            {
                "Content-Type": "application/json",
                "rid": "session",
                "anti-csrf": csrf,
                "Origin": ORIGIN,
            }
        )
        workspace = request_json("/api/v1/workspaces")[0]["id"]
        save(
            "synthetic-identity.private.json",
            {
                "uid": uid,
                "phone": identity["phone"],
                "attempt": attempt["preAuthSessionId"],
                "workspace": workspace,
            },
        )
        base = "/api/v1/workspaces/" + workspace + "/replica"
        asset_base = "/api/v1/workspaces/" + workspace + "/assets/"
        cases = json.loads((ROOT / "cases.json").read_text())

        def test_case(case):
            label = case["label"]
            save(
                label + "-intent.private.json", {"url": case["url"], "submitted": True}
            )
            document = request_json(
                base + "/products", {"url": case["url"]}, timeout=60
            )
            save(label + "-document.private.json", {"id": document["id"]})
            deadline = time.monotonic() + 660
            while time.monotonic() < deadline:
                document = request_json(base + "/documents/" + document["id"])
                if document["status"] in {"ready", "failed"}:
                    break
                time.sleep(3)
            assert document["status"] == "ready", document["payload"].get("error_code")
            product = document["payload"]["product"]
            assert product["supplier"] == "everyinfra"
            assert product["numIid"] == case["item_id"]
            groups = {
                k: product[k] for k in ("imageUrls", "descImageUrls", "skuImageUrls")
            }
            save(label + "-response.private.json", document)
            assert any(groups.values()), {k: len(v) for k, v in groups.items()}
            print(
                json.dumps(
                    {
                        "case": label,
                        "stage": "fetched",
                        "counts": {k: len(v) for k, v in groups.items()},
                    }
                ),
                flush=True,
            )
            paths = [
                (
                    url,
                    base
                    + "/products/"
                    + document["id"]
                    + "/images/"
                    + group
                    + "/"
                    + str(i),
                )
                for group, urls in groups.items()
                for i, url in enumerate(urls)
            ]
            with ThreadPoolExecutor(max_workers=4) as pool:
                hashes = list(pool.map(lambda row: (row[0], read_image(row[1])), paths))
            preview_hashes = dict(hashes)
            urls = list(
                dict.fromkeys(url for values in groups.values() for url in values)
            )
            imported = {}
            for start in range(0, len(urls), 5):
                batch = urls[start : start + 5]
                results = request_json(
                    base + "/products/" + document["id"] + "/import", {"urls": batch}
                )
                assert {r["url"] for r in results} == set(batch)
                imported.update({r["url"]: r["asset_id"] for r in results})
            print(
                json.dumps(
                    {"case": label, "stage": "imported", "assets": len(imported)}
                ),
                flush=True,
            )

            def verify_asset(row):
                url, asset_id = row
                metadata = request_json(asset_base + asset_id)
                assert metadata["lifecycle_status"] == "ready"
                assert metadata["origin_kind"] == "imported"
                assert metadata["width"] >= 32 and metadata["height"] >= 32
                assert metadata["sha256"] == preview_hashes[url]
                original = request_json(
                    asset_base + asset_id + "/access-url",
                    {"disposition": "inline", "variant": "original"},
                )
                assert read_image(original["url"]) == metadata["sha256"]
                thumbnail = request_json(
                    asset_base + asset_id + "/access-url",
                    {"disposition": "inline", "variant": "thumbnail"},
                )
                read_image(thumbnail["url"])

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(verify_asset, imported.items()))
            again = request_json(
                base + "/products/" + document["id"] + "/import", {"urls": [urls[0]]}
            )
            assert again[0]["asset_id"] == imported[urls[0]]
            cached = request_json(base + "/products", {"url": case["url"]})
            assert cached["id"] == document["id"] and cached["status"] == "ready"
            result = {
                "item_id": product["numIid"],
                "all_image_groups_available": all(groups.values()),
                "groups": {
                    k: {
                        "available": bool(v),
                        "returned": len(v),
                        "preview_verified": len(v),
                        "import_verified": len(v),
                        "imported_original_verified": len(v),
                        "imported_thumbnail_verified": len(v),
                    }
                    for k, v in groups.items()
                },
                "unique_imported_assets": len(imported),
                "repeat_import_reused_asset": True,
                "repeat_fetch_reused_document": True,
                "cost_usd": product.get("costUsd"),
            }
            save(label + "-result.json", result)
            print(
                json.dumps(
                    {
                        "case": label,
                        "stage": "passed"
                        if all(groups.values())
                        else "available_images_verified",
                        "unique_assets": len(imported),
                    }
                ),
                flush=True,
            )
            return label, result

        failures = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(test_case, case): case["label"] for case in cases}
            for future in as_completed(futures):
                try:
                    label, result = future.result()
                    report["cases"][label] = result
                except (AssertionError, OSError, ValueError, KeyError) as error:
                    failures.append(
                        {"case": futures[future], "error_type": type(error).__name__}
                    )
        report["all_image_groups_available"] = (
            all(case["all_image_groups_available"] for case in report["cases"].values())
            and not failures
        )
        if failures:
            report["failures"] = failures
            raise AssertionError("See the private case receipts and saved report")
        credits = request_json("/api/v1/workspaces/" + workspace + "/credits")
        assert credits["balance_credits"] == 0
        report["business_credits_charged"] = 0
    finally:
        cleanup_data = {
            "uid": uid,
            "phone": identity["phone"],
            "attempt": attempt["preAuthSessionId"],
            "workspace": workspace,
        }
        cleanup = r"""
import asyncio,hashlib,json
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session
from app.config import Settings
from app.main import create_app
from app.models import UserRecord,WorkspaceRecord,AssetRecord,JobRecord
from app.storage import build_image_storage
from supertokens_python.syncio import delete_user
from redis import Redis
s=Settings();create_app(s.model_copy(update={'ecommerce_startup_recovery_enabled':False}))
data=DATA
removed=0
if data['uid']:
 engine=create_engine(s.database_url)
 with Session(engine) as db:
  user=db.scalar(select(UserRecord).where(UserRecord.auth_subject==data['uid'],UserRecord.phone_number==data['phone']))
  assert user is not None
  workspaces=list(db.scalars(select(WorkspaceRecord).where(WorkspaceRecord.created_by_user_id==user.id)))
  assert len(workspaces)==1 and workspaces[0].id==data['workspace']
  workspace_id=workspaces[0].id
  assert db.scalar(select(func.count()).select_from(JobRecord).where(JobRecord.workspace_id==workspace_id))==0
  assets=list(db.scalars(select(AssetRecord).where(AssetRecord.workspace_id==workspace_id)))
  storage=build_image_storage(s)
  async def remove_objects():
   for asset in assets:
    assert asset.origin_kind=='imported' and asset.object_key.startswith(workspace_id+'/')
    await storage.delete_object(asset.object_key,asset.object_version)
  asyncio.run(remove_objects())
  for asset in assets:db.delete(asset)
  db.flush();removed=len(assets)
  db.delete(workspaces[0]);db.flush();db.delete(user);db.commit()
 delete_user(data['uid']);engine.dispose()
redis=Redis.from_url(s.auth_sms_redis_url)
redis.delete('polaris:auth:sms:v1:cooldown:'+hashlib.sha256(data['phone'].encode()).hexdigest(),'polaris:auth:sms:v1:daily:'+hashlib.sha256(data['phone'].encode()).hexdigest(),'polaris:auth:sms:v1:attempt:'+hashlib.sha256(data['attempt'].encode()).hexdigest())
redis.close()
print(json.dumps({'synthetic_account_cleaned':True,'test_asset_objects_removed':removed}))
""".replace("data=DATA", "data=" + repr(cleanup_data))
        report.update(execute(cleanup))
        save("report.json", report)
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
