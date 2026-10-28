"""只读海宇短信配置，写入本产品被 Git 忽略的服务端环境文件。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    target = args.target.resolve()
    if target.name != ".env.sms.local" or target == source:
        parser.error("target must be the backend .env.sms.local file")
    if target.exists():
        parser.error("target already exists; review it instead of overwriting secrets")
    config = yaml.safe_load(source.read_text(encoding="utf-8"))["sms"]
    if config.get("provider", "tencent") != "tencent":
        parser.error("the configured Haiyu SMS provider is not Tencent Cloud")
    sms = config["tencent_cloud"]
    mapping = {
        "POLARIS_AUTH_SMS_ENABLED": "true",
        "POLARIS_AUTH_SMS_SECRET_ID": sms["secret_id"],
        "POLARIS_AUTH_SMS_SECRET_KEY": sms["secret_key"],
        "POLARIS_AUTH_SMS_REGION": sms.get("region") or "ap-guangzhou",
        "POLARIS_AUTH_SMS_SDK_APP_ID": sms["sms_sdk_app_id"],
        "POLARIS_AUTH_SMS_SIGN_NAME": sms["sign_name"],
        "POLARIS_AUTH_SMS_TEMPLATE_ID": sms["template_id"],
    }
    if not all(str(value).strip() for value in mapping.values()):
        parser.error("source SMS configuration is incomplete")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
        file.write("# Server-only credentials. Never commit or send to the browser.\n")
        for key, value in mapping.items():
            file.write(f"{key}={json.dumps(str(value), ensure_ascii=False)}\n")
    print("SMS configuration imported; credential values are hidden.")


if __name__ == "__main__":
    main()
