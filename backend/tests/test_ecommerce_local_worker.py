from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.ecommerce_local_worker as local_worker
from app.ecommerce_local_worker import (
    LocalProfileStore,
    LocalWorkerError,
    LocalWorkerRuntime,
    WorkerApiClient,
)


def test_profile_store_normalizes_windows_extended_path_prefix() -> None:
    if os.name != "nt":
        pytest.skip("Windows path normalization")
    value = local_worker._normalise_windows_resolved_path(
        Path("//?/C:/polaris/profiles/account_1")
    )
    assert str(value).replace("\\", "/").casefold() == "c:/polaris/profiles/account_1"
    unc = local_worker._normalise_windows_resolved_path(
        Path("//?/UNC/server/share/profiles/account_1")
    )
    assert str(unc).replace("\\", "/").casefold() == "//server/share/profiles/account_1"


def test_profile_store_rejects_traversal_and_serializes_account_access(tmp_path: Path):
    store = LocalProfileStore(tmp_path / "profiles")
    profile = store.profile_path("account_1")
    assert profile.is_relative_to(store.root)

    with pytest.raises(LocalWorkerError, match="INVALID_ACCOUNT_ID"):
        store.profile_path("../outside")

    with store.lock("account_1") as held:
        assert held == profile
        with (
            pytest.raises(LocalWorkerError, match="PROFILE_BUSY"),
            store.lock("account_1"),
        ):
            pass
    with store.lock("account_1"):
        pass


def test_profile_store_reclaims_expired_lock_from_dead_process(tmp_path: Path):
    store = LocalProfileStore(tmp_path / "profiles")
    lock_path = store._locks / "account_1.lock"
    lock_path.write_text(str(os.getpid() + 999_999), encoding="ascii")
    old = time.time() - 10
    os.utime(lock_path, (old, old))

    with store.lock("account_1", stale_after_seconds=1):
        assert lock_path.exists()


def test_worker_api_requires_tls_and_rejects_sensitive_or_invalid_payloads(monkeypatch):
    with pytest.raises(LocalWorkerError, match="WORKER_API_REQUIRES_HTTPS"):
        WorkerApiClient("http://api.example.test", "worker-session")

    client = WorkerApiClient("http://127.0.0.1:8000", "worker-session")
    with pytest.raises(LocalWorkerError, match="SENSITIVE_FIELD_BLOCKED"):
        client._post("/api/test", {"cookies": "must-not-leave-machine"})

    class _InvalidResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"[]"

    monkeypatch.setattr(
        local_worker, "urlopen", lambda *_args, **_kwargs: _InvalidResponse()
    )
    with pytest.raises(LocalWorkerError, match="WORKER_API_INVALID_RESPONSE"):
        client._post("/api/test", {"items": ("safe",)})


class _FakeApi:
    def __init__(self):
        self.uploads: list[tuple[str, str, dict]] = []
        self.completions: list[tuple[str, str, dict]] = []
        self.heartbeats: list[dict] = []
        self.claims = 0
        self.renewals = 0

    def upload_result(self, worker_id: str, task_id: str, payload: dict) -> dict:
        self.uploads.append((worker_id, task_id, payload))
        return {"accepted": True}

    def complete(self, worker_id: str, task_id: str, payload: dict) -> dict:
        self.completions.append((worker_id, task_id, payload))
        return {"accepted": True}

    def heartbeat(self, _worker_id: str, payload: dict) -> dict:
        self.heartbeats.append(payload)
        return {"accepted": True}

    def register_worker(
        self,
        enrollment_token: str,
        device_id: str,
        *,
        protocol_version: str,
        agent_version: str,
        capabilities: list[str],
    ) -> dict:
        return {
            "data": {
                "session_token": "registered-session",
                "recovery_token": "registered-recovery-token",
                "worker": {"id": "worker_registered"},
                "enrollment_token": enrollment_token,
                "device_id": device_id,
                "protocol_version": protocol_version,
                "agent_version": agent_version,
                "capabilities": capabilities,
            }
        }

    def recover_worker(
        self,
        _recovery_token: str,
        worker_id: str,
        _device_id: str,
        *,
        protocol_version: str,
        agent_version: str,
        capabilities: list[str],
    ) -> dict:
        return {
            "data": {
                "session_token": "recovered-session",
                "recovery_token": "recovered-recovery-token",
                "worker": {
                    "id": worker_id,
                    "protocol_version": protocol_version,
                    "agent_version": agent_version,
                    "capabilities": capabilities,
                },
            }
        }

    def claim(self, _worker_id: str, _platforms: list[str] | None = None) -> dict:
        self.claims += 1
        return {"data": _task()} if self.claims == 1 else {"data": None}

    def renew_lease(self, _worker_id: str, _task_id: str, _fencing_token: int) -> dict:
        self.renewals += 1
        return {"accepted": True}


def _task() -> dict:
    return {
        "id": "task_1",
        "account_id": "account_1",
        "lease": {"fencing_token": 7},
        "request_payload": {"platform": "taobao"},
    }


def test_runtime_uses_platform_profile_when_task_has_no_account(tmp_path: Path):
    api = _FakeApi()
    runtime = LocalWorkerRuntime(api, "worker_1", LocalProfileStore(tmp_path))

    def crawl(profile_path: Path, _task: dict) -> dict:
        assert profile_path.name == "domestic-taobao-tmall"
        return {"platform": "taobao", "title": "公开商品"}

    task = {
        "id": "task_public",
        "account_id": None,
        "profile_id": "domestic-taobao-tmall",
        "lease": {"fencing_token": 3},
    }
    assert runtime.execute_claimed(task, crawl) == {"accepted": True}


def test_runtime_keeps_profile_local_and_uploads_structured_result(tmp_path: Path):
    api = _FakeApi()
    runtime = LocalWorkerRuntime(api, "worker_1", LocalProfileStore(tmp_path))
    observed: dict[str, Path] = {}

    def crawl(profile_path: Path, task: dict) -> dict:
        observed["profile"] = profile_path
        assert profile_path.is_relative_to(tmp_path.resolve())
        assert task["id"] == "task_1"
        return {"platform": "taobao", "title": "本地结果"}

    assert runtime.execute_claimed(_task(), crawl) == {"accepted": True}
    assert observed["profile"].is_relative_to(tmp_path.resolve())
    assert api.uploads == [
        (
            "worker_1",
            "task_1",
            {
                "fencing_token": 7,
                "result": {"platform": "taobao", "title": "本地结果"},
            },
        )
    ]


def test_runtime_reports_browser_crash_without_exposing_exception(tmp_path: Path):
    api = _FakeApi()
    runtime = LocalWorkerRuntime(api, "worker_1", LocalProfileStore(tmp_path))

    def crash(_profile_path: Path, _task: dict) -> dict:
        raise RuntimeError("cookie=secret should not be sent")

    with pytest.raises(LocalWorkerError, match="WORKER_BROWSER_CRASH"):
        runtime.execute_claimed(_task(), crash)
    assert api.completions[0][2] == {
        "fencing_token": 7,
        "outcome": "failed",
        "error_code": "WORKER_BROWSER_CRASH",
        "error_message_safe": "本机浏览器执行失败。",
    }


def test_worker_loop_claims_renews_and_stops_cleanly(tmp_path: Path):
    api = _FakeApi()
    runtime = LocalWorkerRuntime(api, "worker_1", LocalProfileStore(tmp_path))
    loop = local_worker.LocalWorkerLoop(
        api,
        runtime,
        capabilities=["browser.taobao"],
        lease_renew_interval_seconds=0.01,
    )

    def crawl(_profile_path: Path, _task: dict) -> dict:
        time.sleep(0.03)
        return {"platform": "taobao", "title": "结果"}

    assert loop.run_once(crawl) is True
    assert api.claims == 1
    assert api.renewals >= 1

    stop = threading.Event()
    stop.set()
    loop.run(crawl, stop)
    assert api.heartbeats[-1]["ready"] is False


def test_worker_loop_registers_before_using_the_bootstrap_worker_id(tmp_path: Path):
    api = _FakeApi()
    runtime = LocalWorkerRuntime(
        api, "device-bootstrap", LocalProfileStore(tmp_path / "profiles")
    )
    persisted: list[str] = []
    loop = local_worker.LocalWorkerLoop(
        api,
        runtime,
        enrollment_token="one-time-token",
        device_id="device-bootstrap",
        on_worker_id_persisted=persisted.append,
    )

    assert loop._recover_registration() is True
    assert runtime.worker_id == "worker_registered"
    assert api.session_token == "registered-session"
    assert persisted == ["worker_registered"]
    assert loop.enrollment_token == ""


def test_worker_keeps_heartbeating_while_browser_work_is_blocked(tmp_path: Path):
    api = _FakeApi()
    stop = threading.Event()
    runtime = LocalWorkerRuntime(api, "worker_1", LocalProfileStore(tmp_path))
    loop = local_worker.LocalWorkerLoop(
        api,
        runtime,
        heartbeat_interval_seconds=0.01,
        poll_interval_seconds=0.01,
        lease_renew_interval_seconds=0.01,
    )

    def crawl(_profile_path: Path, _task: dict) -> dict:
        initial_count = len(api.heartbeats)
        deadline = time.monotonic() + 1
        while len(api.heartbeats) < initial_count + 2:
            assert time.monotonic() < deadline, "heartbeat stopped during browser work"
            time.sleep(0.005)
        stop.set()
        return {"platform": "taobao", "title": "结果"}

    loop.run(crawl, stop)
    assert api.uploads
    assert api.heartbeats[-1]["ready"] is False


def test_worker_loop_runs_different_account_tasks_in_parallel(tmp_path: Path):
    class _TwoTaskApi(_FakeApi):
        def __init__(self):
            super().__init__()
            self._claim_lock = threading.Lock()

        def claim(self, _worker_id: str, _platforms: list[str] | None = None) -> dict:
            with self._claim_lock:
                self.claims += 1
                number = self.claims
            if number > 2:
                return {"data": None}
            task = _task()
            task["id"] = f"task_{number}"
            task["account_id"] = f"account_{number}"
            return {"data": task}

    api = _TwoTaskApi()
    runtime = LocalWorkerRuntime(api, "worker_1", LocalProfileStore(tmp_path))
    loop = local_worker.LocalWorkerLoop(
        api,
        runtime,
        concurrency=2,
        heartbeat_interval_seconds=0.01,
        poll_interval_seconds=0.01,
        lease_renew_interval_seconds=0.01,
    )
    stop = threading.Event()
    active = 0
    maximum = 0
    state_lock = threading.Lock()

    def crawl(_profile_path: Path, _task: dict) -> dict:
        nonlocal active, maximum
        with state_lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                stop.set()
        time.sleep(0.04)
        with state_lock:
            active -= 1
        return {"platform": "taobao", "title": "并发结果"}

    watchdog = threading.Timer(2, stop.set)
    watchdog.start()
    try:
        loop.run(crawl, stop)
    finally:
        watchdog.cancel()
    assert maximum == 2
    assert {upload[1] for upload in api.uploads} == {"task_1", "task_2"}


def test_browser_enrollment_takes_priority_over_saved_recovery(tmp_path: Path):
    api = _FakeApi()
    runtime = LocalWorkerRuntime(api, "worker_existing", LocalProfileStore(tmp_path))
    loop = local_worker.LocalWorkerLoop(
        api,
        runtime,
        recovery_token="saved-recovery-token",
        device_id="device-existing",
    )
    loop.accept_enrollment("fresh-enrollment", "device-existing")
    assert loop._recover_registration() is True
    assert api.session_token == "registered-session"
    assert loop._enrollment_requested is False
    assert loop.enrollment_token == ""


def test_worker_loop_recovers_with_persisted_recovery_token(tmp_path: Path):
    api = _FakeApi()
    runtime = LocalWorkerRuntime(api, "worker_existing", LocalProfileStore(tmp_path))
    refreshed: list[str] = []
    recoveries: list[str] = []
    loop = local_worker.LocalWorkerLoop(
        api,
        runtime,
        recovery_token="recovery-token",
        device_id="device-existing",
        on_token_refreshed=refreshed.append,
        on_recovery_token_refreshed=recoveries.append,
    )

    assert loop._recover_registration() is True
    assert api.session_token == "recovered-session"
    assert loop.recovery_token == "recovered-recovery-token"
    assert refreshed == ["recovered-session"]
    assert recoveries == ["recovered-recovery-token"]


def test_worker_loop_reenrolls_formal_worker_after_session_auth_failure(tmp_path: Path):
    stop = threading.Event()

    class _ExpiredSessionApi(_FakeApi):
        def __init__(self):
            super().__init__()
            self.registration_attempts = 0

        def heartbeat(self, worker_id: str, payload: dict) -> dict:
            super().heartbeat(worker_id, payload)
            if payload["ready"] and self.registration_attempts == 0:
                raise LocalWorkerError("WORKER_AUTH_INVALID")
            if not payload["ready"]:
                stop.set()
            return {"accepted": True}

        def register_worker(self, *args, **kwargs) -> dict:
            self.registration_attempts += 1
            return super().register_worker(*args, **kwargs)

        def claim(self, _worker_id: str, _platforms: list[str] | None = None) -> dict:
            if self.registration_attempts:
                stop.set()
            return {"data": None}

    api = _ExpiredSessionApi()
    runtime = LocalWorkerRuntime(
        api, "worker_existing", LocalProfileStore(tmp_path / "profiles")
    )
    loop = local_worker.LocalWorkerLoop(
        api,
        runtime,
        enrollment_token="recovery-token",
        device_id="device-existing",
        heartbeat_interval_seconds=0.01,
        poll_interval_seconds=0.01,
    )

    loop.run(lambda _profile_path, _task: {}, stop)

    assert api.registration_attempts == 1
    assert runtime.worker_id == "worker_registered"
    assert api.heartbeats[-1]["ready"] is False


def test_worker_loop_accepts_browser_delivered_enrollment(tmp_path: Path):
    api = _FakeApi()
    runtime = LocalWorkerRuntime(api, "worker_existing", LocalProfileStore(tmp_path))
    loop = local_worker.LocalWorkerLoop(
        api,
        runtime,
        enrollment_token="stale-token",
        device_id="device-existing",
    )

    loop.accept_enrollment("fresh-token", "device-existing")
    assert loop.enrollment_token == "fresh-token"
    assert loop._enrollment_requested is True
    assert loop._recover_registration() is True
    assert runtime.worker_id == "worker_registered"
    with pytest.raises(LocalWorkerError, match="WORKER_DEVICE_MISMATCH"):
        loop.accept_enrollment("another-token", "other-device")


def test_local_product_crawler_uses_only_claimed_profile(monkeypatch, tmp_path: Path):
    fake_product = SimpleNamespace(
        platform="taobao",
        product_id="123",
        title="本机商品",
        price="19.9",
        shop_name="店铺",
        sales=None,
        images=[SimpleNamespace(url="https://img.example/1.jpg", role="main")],
        sku_prop_groups=[],
        skus=[],
        props_text="",
        services=[],
        image_diagnostics={},
        warnings=[],
        access_state=None,
    )
    observed: dict[str, object] = {}

    def fake_browser(url: str, timeout_ms: int, profile_dir: str):
        observed.update(url=url, timeout_ms=timeout_ms, profile_dir=profile_dir)
        return fake_product

    monkeypatch.setattr("app.ecommerce_fetcher.crawl_one_browser", fake_browser)
    callback = local_worker.build_local_product_crawler(timeout_ms=1234)
    result = callback(
        tmp_path / "accounts" / "account_1",
        {
            "platform": "taobao",
            "canonical_url": "https://item.taobao.com/item.htm?id=123",
            "request_payload": {"includeDescImages": False},
        },
    )
    assert result["platform"] == "taobao"
    assert result["title"] == "本机商品"
    assert result["descImageUrls"] == []
    assert observed == {
        "url": "https://item.taobao.com/item.htm?id=123",
        "timeout_ms": 1234,
        "profile_dir": str(tmp_path / "accounts" / "account_1"),
    }


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("product", "IMAGE_EXTRACTION_INCOMPLETE"),
        ("risk_controlled", "RISK_CONTROL_PAGE"),
    ],
)
def test_local_jd_crawler_rejects_empty_images_and_preserves_risk_status(
    monkeypatch, tmp_path: Path, status: str, expected_code: str
):
    from app.ecommerce_jd import JDProduct

    url = "https://item.jd.com/10031245841078.html"
    product = JDProduct(
        status=status,
        source_url=url,
        resolved_url=url,
        product_id="a00014",
        title="中间页",
    )
    monkeypatch.setattr("app.ecommerce_jd.crawl_one_jd_browser", lambda *_args: product)
    with pytest.raises(local_worker.LocalWorkerTaskError, match=expected_code):
        local_worker.build_local_product_crawler()(
            tmp_path / "account_jd", {"platform": "jd", "canonical_url": url}
        )


@pytest.mark.parametrize("role", ["main", "detail", "sku"])
def test_local_jd_crawler_accepts_valid_product_image_roles(
    monkeypatch, tmp_path: Path, role: str
):
    from app.ecommerce_jd import JDImage, JDProduct

    url = "https://item.jd.com/10031245841078.html"
    product = JDProduct(
        status="product",
        source_url=url,
        resolved_url=url,
        product_id="10031245841078",
        title="京东商品",
        images=(JDImage("https://img10.360buyimg.com/n1/jfs/product.jpg", role=role),),
    )
    monkeypatch.setattr("app.ecommerce_jd.crawl_one_jd_browser", lambda *_args: product)
    result = local_worker.build_local_product_crawler()(
        tmp_path / "account_jd", {"platform": "jd", "canonical_url": url}
    )
    image_key = {"main": "imageUrls", "detail": "descImageUrls", "sku": "skuImageUrls"}[
        role
    ]
    assert result[image_key] == [product.images[0].url]


def test_local_worker_config_requires_env_and_never_requires_cli_secret(monkeypatch):
    with pytest.raises(LocalWorkerError, match="WORKER_CONFIG_REQUIRED"):
        local_worker.LocalWorkerConfig.from_env({})

    config = local_worker.LocalWorkerConfig.from_env(
        {
            "POLARIS_WORKER_API_URL": "https://api.example.test",
            "POLARIS_WORKER_SESSION_TOKEN": "session-secret",
            "POLARIS_WORKER_ID": "worker_1",
            "POLARIS_WORKER_PROFILE_ROOT": "C:/Polaris/profiles",
            "POLARIS_WORKER_CAPABILITIES": "browser.taobao, browser.jd",
            "POLARIS_WORKER_BROWSER_TIMEOUT_MS": "15000",
            "POLARIS_WORKER_CONCURRENCY": "2",
        }
    )
    assert config.worker_id == "worker_1"
    assert config.capabilities == ("browser.taobao", "browser.jd")
    assert config.browser_timeout_ms == 15000
    assert config.concurrency == 2
    for name in (
        "POLARIS_WORKER_API_URL",
        "POLARIS_WORKER_SESSION_TOKEN",
        "POLARIS_WORKER_ID",
        "POLARIS_WORKER_PROFILE_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    assert local_worker.main(["--check-config"]) == 2


def test_local_worker_config_bootstraps_from_enrollment_token(tmp_path: Path):
    config = local_worker.LocalWorkerConfig.from_env(
        {
            "POLARIS_WORKER_API_URL": "https://api.example.test",
            "POLARIS_WORKER_ENROLLMENT_TOKEN": "one-time-token",
            "POLARIS_WORKER_DEVICE_ID": "device-bootstrap",
            "POLARIS_WORKER_PROFILE_ROOT": str(tmp_path / "profiles"),
        }
    )
    assert config.worker_id == "device-bootstrap"
    config.persist_worker_id("wrk_registered")
    assert config.worker_id_file is not None
    assert config.worker_id_file.read_text(encoding="utf-8").strip() == "wrk_registered"

    restored = local_worker.LocalWorkerConfig.from_env(
        {
            "POLARIS_WORKER_API_URL": "https://api.example.test",
            "POLARIS_WORKER_SESSION_TOKEN": "session-token",
            "POLARIS_WORKER_ENROLLMENT_TOKEN": "recovery-token",
            "POLARIS_WORKER_DEVICE_ID": "device-bootstrap",
            "POLARIS_WORKER_PROFILE_ROOT": str(tmp_path / "profiles"),
            "POLARIS_WORKER_ID_FILE": str(config.worker_id_file),
        }
    )
    assert restored.worker_id == "wrk_registered"
    assert restored.enrollment_token == "recovery-token"


def test_local_worker_config_loads_recovery_token_file(tmp_path: Path):
    recovery_file = tmp_path / "worker-recovery-token"
    recovery_file.write_text("recovery-file-token\n", encoding="utf-8")
    config = local_worker.LocalWorkerConfig.from_env(
        {
            "POLARIS_WORKER_API_URL": "https://api.example.test",
            "POLARIS_WORKER_RECOVERY_TOKEN_FILE": str(recovery_file),
            "POLARIS_WORKER_ID": "worker_1",
            "POLARIS_WORKER_PROFILE_ROOT": str(tmp_path / "profiles"),
        }
    )
    assert config.recovery_token == "recovery-file-token"


def test_local_worker_config_loads_and_persists_token_file(tmp_path: Path):
    token_file = tmp_path / "worker-token"
    token_file.write_text("file-token\n", encoding="utf-8")
    config = local_worker.LocalWorkerConfig.from_env(
        {
            "POLARIS_WORKER_API_URL": "https://api.example.test",
            "POLARIS_WORKER_SESSION_TOKEN": "bootstrap-token",
            "POLARIS_WORKER_ID": "worker_1",
            "POLARIS_WORKER_PROFILE_ROOT": str(tmp_path / "profiles"),
            "POLARIS_WORKER_SESSION_TOKEN_FILE": str(token_file),
            "POLARIS_WORKER_TOKEN_REFRESH_INTERVAL": "60",
        }
    )
    assert config.session_token == "file-token"
    assert config.token_refresh_interval_seconds == 60
    config.persist_session_token("rotated-token")
    assert token_file.read_text(encoding="utf-8").strip() == "rotated-token"


def test_local_login_uses_account_scoped_profile_without_api_credentials(
    monkeypatch, tmp_path: Path
):
    observed: dict[str, object] = {}

    class _Manager:
        def __init__(self, profile_path, **kwargs):
            observed["profile_path"] = profile_path
            observed["kwargs"] = kwargs

        def start_login(self, target_url, **kwargs):
            observed["target_url"] = target_url
            observed["start"] = kwargs

        def status(self):
            return {"state": "ready", "profileReady": True}

    monkeypatch.setattr("app.ecommerce_session.EcommerceSessionManager", _Manager)
    status = local_worker.run_local_login(
        tmp_path,
        "account_1",
        "tmall",
        target_url="https://login.taobao.com",
        timeout_seconds=5,
    )
    assert status["state"] == "ready"
    profile_path = Path(observed["profile_path"])
    assert profile_path.parts[-2:] == ("accounts", "account_1")
    assert observed["kwargs"]["platform"] == "taobao"
    assert observed["start"]["headless"] is False
