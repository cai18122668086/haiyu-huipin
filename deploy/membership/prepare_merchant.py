"""Run on the server: reuse the existing merchant without exporting its secrets."""

import base64
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID

ROOT = Path("/srv/huipin-membership-20261028")
SECRETS = ROOT / "secrets"
SOURCE = Path("/app/haiyushuke-server")
SECRETS.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(SECRETS, 0o700)
os.chown(SECRETS, 10001, 10001)
config = yaml.safe_load((SOURCE / "configs/platform.production.yaml").read_text())["payment"]
container = json.loads(subprocess.check_output(["docker", "inspect", "haiyushuke-platform"]))[0]
environment = dict(value.split("=", 1) for value in container["Config"]["Env"] if "=" in value)


def path_of(value):
    path = SOURCE / value.removeprefix("/app/")
    if not path.resolve().is_relative_to(SOURCE.resolve()):
        raise ValueError("Invalid merchant certificate path")
    return path


def save_secret(name, value):
    path = SECRETS / name
    path.write_text(value)
    os.chown(path, 10001, 10001)
    os.chmod(path, 0o400)


def serial(cert):
    # This MD5 identifies the public certificate; payment signatures use RSA2.
    return hashlib.md5((cert.issuer.rfc4514_string() + str(cert.serial_number)).encode()).hexdigest()


ali, wx = config["alipay"], config["wxpay"]
app_cert = x509.load_pem_x509_certificate(path_of(ali["app_cert_path"]).read_bytes())
alipay_cert_path = path_of(ali["alipay_cert_path"])
alipay_cert = x509.load_pem_x509_certificate(alipay_cert_path.read_bytes())
for cert in [app_cert, alipay_cert]:
    if not cert.not_valid_before.replace(tzinfo=UTC) <= datetime.now(UTC) < cert.not_valid_after.replace(tzinfo=UTC):
        raise ValueError("Merchant certificate is outside its validity period")
seller_id = app_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
if not re.fullmatch(r"2088\d{12}", seller_id):
    raise ValueError("Merchant seller identity could not be confirmed from application certificate")
root_certs = [x509.load_pem_x509_certificate(item) for item in re.findall(
    b"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
    path_of(ali["alipay_root_cert_path"]).read_bytes(), re.S)]
root_sn = "_".join(serial(cert) for cert in root_certs
                   if cert.signature_algorithm_oid.dotted_string in {"1.2.840.113549.1.1.5", "1.2.840.113549.1.1.11"})
raw_key = environment["PAYMENT_ALIPAY_PRIVATE_KEY"]
private_key = (serialization.load_pem_private_key(raw_key.encode(), password=None)
               if "BEGIN" in raw_key else serialization.load_der_private_key(base64.b64decode(raw_key), password=None))
if private_key.public_key().public_numbers() != app_cert.public_key().public_numbers():
    raise ValueError("Merchant application certificate and signing key differ")
save_secret("alipay-private.pem", private_key.private_bytes(serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
save_secret("wechat-private.pem", path_of(wx["mch_private_key_path"]).read_text())
wx_api_key = environment["PAYMENT_WX_API_V3_KEY"]
wx_key_source = "platform_environment"
if len(wx_api_key.encode()) != 32:
    old = yaml.safe_load(subprocess.check_output([
        "docker", "exec", "hyapi-app-prod", "cat", "/app/configs/env.development.yaml"
    ]))["Wxpay"]
    if all(old.get(name) == wx.get(name) for name in [
        "app_id", "mch_id", "mch_certificate_serial_number", "mch_public_key_id"
    ]):
        wx_api_key = old.get("mch_apiv3_key", "")
        wx_key_source = "same_merchant_original_configuration"
wx_ready = len(wx_api_key.encode()) == 32
values = {
    "POLARIS_CREDIT_PAYMENTS_ENABLED": "true",
    "POLARIS_CREDIT_PAYMENT_PUBLIC_BASE_URL": "https://huipin.haiyushuke.com",
    "POLARIS_CREDIT_ALIPAY_MODE": "cert",
    "POLARIS_CREDIT_ALIPAY_APP_ID": environment.get("PAYMENT_ALIPAY_APP_ID") or ali["app_id"],
    "POLARIS_CREDIT_ALIPAY_SELLER_ID": seller_id,
    "POLARIS_CREDIT_ALIPAY_PRIVATE_KEY_PATH": "/run/huipin-payments/alipay-private.pem",
    "POLARIS_CREDIT_ALIPAY_PUBLIC_KEY": alipay_cert_path.read_text(),
    "POLARIS_CREDIT_ALIPAY_APP_CERT_SN": serial(app_cert),
    "POLARIS_CREDIT_ALIPAY_ROOT_CERT_SN": root_sn,
    "POLARIS_CREDIT_WECHAT_APP_ID": wx["app_id"],
    "POLARIS_CREDIT_WECHAT_MCH_ID": wx["mch_id"],
    "POLARIS_CREDIT_WECHAT_CERTIFICATE_SERIAL": wx["mch_certificate_serial_number"],
    "POLARIS_CREDIT_WECHAT_PRIVATE_KEY_PATH": "/run/huipin-payments/wechat-private.pem",
    "POLARIS_CREDIT_WECHAT_PUBLIC_KEYS": json.dumps({wx["mch_public_key_id"]: path_of(wx["mch_public_key_path"]).read_text()}),
    "POLARIS_CREDIT_WECHAT_API_V3_KEY": wx_api_key if wx_ready else "",
}
# Compose's single-quoted dotenv values preserve PEM newlines and literal dollar signs.
env_file = SECRETS / "payments.env"
env_file.write_text("\n".join(key + "='" + value.replace("'", "\\'") + "'" for key, value in values.items()) + "\n")
os.chmod(env_file, 0o600)
report = {"merchant_reused": True, "source_platform_unchanged": True,
          "private_keys_match_app_certificate": True, "channels": ["alipay", "wechat"] if wx_ready else ["alipay"],
          "wechat_api_v3_key_valid": wx_ready,
          "wechat_key_source": wx_key_source,
          "application_certificate_expires": app_cert.not_valid_after.replace(tzinfo=UTC).isoformat(),
          "public_base_url": values["POLARIS_CREDIT_PAYMENT_PUBLIC_BASE_URL"],
          "secret_values_exported_to_client": False}
(ROOT / "merchant-readiness.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
