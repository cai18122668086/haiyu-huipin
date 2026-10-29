"""Public auth/read-only regression with one disposable synthetic identity.

Reuses the reviewed no-SMS creation/cleanup templates, without the paid crawler
or generation sections. Credentials stay in a mode-0600 server-side file.
"""

import argparse
import ast
import base64
import http.cookiejar
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("/srv/huipin-replica-fast-20261029")
PRIVATE = ROOT / "qa.private.json"
REPORT = ROOT / "public-fast-probe.json"
HOST = "https://huipin.haiyushuke.com"


def template(name):
    tree = ast.parse(
        Path("/srv/huipin-product-preview-20261029/probe_public.py").read_text()
    )
    return next(
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


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


def save_private(data):
    descriptor = os.open(PRIVATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(data, stream)


def setup():
    assert not PRIVATE.exists(), "A test identity already exists; clean it first"
    create = template("create").replace(
        "phone='+86188'+''.join(str(secrets.randbelow(10)) for _ in range(8))",
        "phone='+8618800291029'",
    )
    data = execute(create)
    data["uid"] = None
    save_private(data)
    try:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        headers = {
            "Content-Type": "application/json",
            "rid": "passwordless",
            "fdi-version": "3.1,4.0,4.1,4.2",
            "st-auth-mode": "cookie",
            "Origin": HOST,
        }
        attempt = data["attempt"]
        payload = {
            "deviceId": attempt["deviceId"],
            "preAuthSessionId": attempt["preAuthSessionId"],
            "userInputCode": data["code"],
            "shouldTryLinkingWithSessionUser": False,
            "acceptedAgreements": True,
            "flow": "register",
            "password": "ReplicaFast-QA-20261029",
        }
        request = urllib.request.Request(
            HOST + "/api/auth/signinup/code/consume",
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        with opener.open(request, timeout=30) as response:
            body = json.load(response)
            assert body["status"] == "OK"
            data["uid"] = body["user"]["id"]
            save_private(data)
            front = response.headers.get_all("front-token")
            assert len(front) == 1 and isinstance(
                json.loads(base64.b64decode(front[0], validate=True)), dict
            )

        def get(path):
            with opener.open(HOST + path, timeout=30) as response:
                assert response.status == 200
                return json.load(response)

        assert get("/api/v1/me")
        workspace = get("/api/v1/workspaces")[0]["id"]
        base = "/api/v1/workspaces/" + workspace
        catalog = get(base + "/credits/packages?include_memberships=true")
        assert (
            catalog["enabled"]
            and catalog["channels"] == ["alipay", "wechat"]
            and len(catalog["packages"]) == 9
        )
        account = get(base + "/credits")
        assert account["enforcement_enabled"] and account["balance_credits"] == 0
        assert len(get(base + "/replica/product-platforms")) == 12
        try:
            get("/api/v1/admin/billing/users")
            raise AssertionError("Customer admin access must be denied")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        report = {
            "public_login": "passed",
            "me": 200,
            "front_token_valid": True,
            "payment_channels_preserved": True,
            "new_customer_credits": 0,
            "source_count": 12,
            "customer_admin_access": 403,
            "real_sms_sent": 0,
            "generation_requests": 0,
            "crawler_requests": 0,
        }
        REPORT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    except BaseException:
        cleanup()
        raise


def cleanup():
    data = json.loads(PRIVATE.read_text())
    code = template("cleanup").replace(
        "data=DATA",
        "data="
        + repr(
            {
                "uid": data["uid"],
                "phone": data["phone"],
                "attempt": data["attempt"]["preAuthSessionId"],
            }
        ),
    )
    result = execute(code)
    report = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    report.update(result)
    REPORT.write_text(json.dumps(report, indent=2))
    PRIVATE.unlink()
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "cleanup"])
    {"setup": setup, "cleanup": cleanup}[parser.parse_args().action]()
