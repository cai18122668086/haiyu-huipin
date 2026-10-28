"""用独立本机 Core/Redis 和模拟短信验证完整账号流程，不发送真实短信。"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from redis import Redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.sms_auth import TencentSmsSender  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", action="append", type=Path)
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    if (
        settings.supertokens_connection_uri != "http://127.0.0.1:18933"
        or settings.auth_sms_redis_url != "redis://127.0.0.1:18934/0"
    ):
        raise RuntimeError("Only the isolated local authentication runtime is allowed")
    phone = "+86188" + "".join(str(secrets.randbelow(10)) for _ in range(8))
    codes: list[str] = []
    assertions = 0

    def fake_send(_sender: object, _phone: str, code: str, _lifetime: int) -> str:
        assert _phone == phone
        codes.append(code)
        return "synthetic-sms-no-network"

    with tempfile.TemporaryDirectory(prefix="polaris-phone-e2e-") as directory:
        root = Path(directory)
        settings = settings.model_copy(
            update={
                "database_url": "sqlite:///" + (root / "test.db").as_posix(),
                "storage_dir": root / "storage",
                "aurora_work_root": root / "work",
                "auto_create_schema": True,
                "ecommerce_startup_recovery_enabled": False,
            }
        )
        with (
            patch.object(TencentSmsSender, "send", fake_send),
            TestClient(
                create_app(settings),
                base_url="http://localhost:18931",
                headers={"origin": "http://localhost:18931"},
            ) as client,
        ):
            response = client.post(
                "/api/auth/signinup/code",
                headers={"rid": "passwordless"},
                json={"phoneNumber": phone, "shouldTryLinkingWithSessionUser": False},
            )
            attempt = response.json()
            assert attempt["status"] == "OK", (response.status_code, attempt)
            assert len(codes) == 1
            assertions += 2
            payload = {
                "deviceId": attempt["deviceId"],
                "preAuthSessionId": attempt["preAuthSessionId"],
                "userInputCode": codes[-1],
                "shouldTryLinkingWithSessionUser": False,
                "acceptedAgreements": True,
                "flow": "register",
                "password": "FirstPassword123",
            }
            no_consent = client.post(
                "/api/auth/signinup/code/consume",
                headers={"rid": "passwordless"},
                json={**payload, "acceptedAgreements": False},
            )
            assert no_consent.json()["status"] == "GENERAL_ERROR"
            registered = client.post(
                "/api/auth/signinup/code/consume",
                headers={"rid": "passwordless"},
                json=payload,
            )
            assert (
                registered.status_code == 200 and registered.json()["status"] == "OK"
            ), registered.text
            me = client.get("/api/v1/me")
            assert me.status_code == 200, me.text
            uid = me.json()["id"]
            assert me.json()["email"] is None
            assertions += 4
            profile = client.get("/api/v1/auth/phone/profile")
            assert profile.json()["has_password"] is True, profile.text
            assertions += 1
            client.cookies.clear()
            invalid = client.post(
                "/api/v1/auth/phone/password/login",
                json={"phone": phone, "password": "WrongPassword123"},
            )
            assert invalid.status_code == 401, invalid.text
            login = client.post(
                "/api/v1/auth/phone/password/login",
                json={"phone": phone, "password": "FirstPassword123"},
            )
            assert login.status_code == 200, login.text
            assert client.get("/api/v1/me").json()["id"] == uid
            assertions += 3
            anti_csrf = login.headers.get("anti-csrf")
            headers = {"rid": "session"}
            if anti_csrf:
                headers["anti-csrf"] = anti_csrf
            changed = client.post(
                "/api/v1/auth/phone/password/change",
                headers=headers,
                json={
                    "current_password": "FirstPassword123",
                    "new_password": "SecondPassword123",
                },
            )
            assert changed.status_code == 200, changed.text
            assert client.get("/api/v1/me").status_code == 401
            assertions += 2
            client.cookies.clear()
            assert (
                client.post(
                    "/api/v1/auth/phone/password/login",
                    json={"phone": phone, "password": "FirstPassword123"},
                ).status_code
                == 401
            )
            login = client.post(
                "/api/v1/auth/phone/password/login",
                json={"phone": phone, "password": "SecondPassword123"},
            )
            assert login.status_code == 200, login.text
            assert client.get("/api/v1/me").json()["id"] == uid
            assertions += 3
            # 原始验证码已被 Core 原子核销，再次提交不能重设密码。
            reused = client.post(
                "/api/auth/signinup/code/consume",
                headers={"rid": "passwordless"},
                json={**payload, "flow": "reset", "password": "InjectedPassword123"},
            )
            assert reused.json()["status"] != "OK", reused.text
            assertions += 1
            redis = Redis.from_url(settings.auth_sms_redis_url)
            redis.delete(
                "polaris:auth:sms:v1:cooldown:"
                + hashlib.sha256(phone.encode()).hexdigest()
            )
            response = client.post(
                "/api/auth/signinup/code",
                headers={"rid": "passwordless"},
                json={"phoneNumber": phone, "shouldTryLinkingWithSessionUser": False},
            )
            attempt = response.json()
            assert attempt["status"] == "OK", attempt
            reset = client.post(
                "/api/auth/signinup/code/consume",
                headers={"rid": "passwordless"},
                json={
                    "deviceId": attempt["deviceId"],
                    "preAuthSessionId": attempt["preAuthSessionId"],
                    "userInputCode": codes[-1],
                    "acceptedAgreements": True,
                    "flow": "reset",
                    "password": "ThirdPassword123",
                    "shouldTryLinkingWithSessionUser": False,
                },
            )
            assert reset.status_code == 200 and reset.json()["status"] == "OK", (
                reset.text
            )
            assert client.get("/api/v1/me").json()["id"] == uid
            client.cookies.clear()
            assert (
                client.post(
                    "/api/v1/auth/phone/password/login",
                    json={"phone": phone, "password": "SecondPassword123"},
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/api/v1/auth/phone/password/login",
                    json={"phone": phone, "password": "ThirdPassword123"},
                ).status_code
                == 200
            )
            assertions += 5
            assert (
                client.post(
                    "/api/v1/auth/phone/password/login",
                    headers={"origin": "https://other.example"},
                    json={"phone": phone, "password": "ThirdPassword123"},
                ).status_code
                == 403
            )
            assertions += 1
            redis.close()
            print(
                f"PASS: {assertions} real Core/Redis assertions: "
                "registration, passwords, "
                "SMS reset, sessions, replay and origin checks; no real SMS sent."
            )


if __name__ == "__main__":
    main()
