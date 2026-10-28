import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app import main as main_module
from app import outbox_worker, task_runner, tasks
from app.config import Settings
from app.database import build_database
from app.storage import build_image_storage
from app.task_runner import MockTaskRunner


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny_connect(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("runner selection tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", deny_connect)


def _settings(tmp_path: Path, **updates: object) -> Settings:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'runner.db'}",
        storage_backend="local",
        storage_dir=tmp_path / "storage",
        auto_create_schema=False,
        auto_process_jobs=False,
        task_dispatcher="inline",
        mock_job_delay_seconds=0,
        tengli_api_key=SecretStr("test-provider-key"),
        tengli_smoke_budget_usd=1,
        tengli_daily_budget_usd=1,
        tengli_monthly_budget_usd=5,
        tengli_daily_request_limit=20,
        xinghe_concurrency=6,
    )
    return settings.model_copy(update=updates)


def _factory_dependencies(
    settings: Settings,
) -> Iterator[tuple[Any, Any, Any]]:
    engine, session_factory = build_database(settings.database_url)
    try:
        yield engine, session_factory, build_image_storage(settings)
    finally:
        engine.dispose()


def test_real_runner_starts_at_3000_without_network(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        task_runner="tengli",
        xinghe_concurrency=3000,
        worker_async_runtime_enabled=True,
        provider_async_transport_enabled=True,
        image_capacity_redis_url="redis://127.0.0.1:6379/0",
    )
    task_runner.validate_task_runner_settings(settings)
    for _, session_factory, storage in _factory_dependencies(settings):
        runner = task_runner.build_task_runner(session_factory, settings, storage)
        assert runner.coordinator.max_concurrency == 3000


def test_factory_default_mock_reads_no_key_and_calls_no_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        tmp_path,
        task_runner="mock",
        tengli_api_key=SecretStr("must-not-be-read"),
    )

    def deny_secret_read(secret: SecretStr) -> str:
        del secret
        raise AssertionError("mock mode read the provider key")

    def deny_provider(*args: object, **kwargs: object) -> MockTaskRunner:
        del args, kwargs
        raise AssertionError("mock mode constructed a provider")

    monkeypatch.setattr(SecretStr, "get_secret_value", deny_secret_read)
    monkeypatch.setattr(task_runner, "build_tengli_task_runner", deny_provider)

    for _, session_factory, storage in _factory_dependencies(settings):
        runner = task_runner.build_task_runner(session_factory, settings, storage)

    assert isinstance(runner, MockTaskRunner)


def test_factory_passes_configured_xinghe_root_to_both_stages(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "published-xinghe-runtime"
    settings = _settings(
        tmp_path,
        task_runner="mock",
        xinghe_skill_root=skill_root,
    )

    for _, session_factory, storage in _factory_dependencies(settings):
        runner = task_runner.build_task_runner(session_factory, settings, storage)

    assert runner.coordinator.detail_planner is not None
    assert runner.coordinator.detail_planner.skill_root == skill_root
    detail_provider = runner.coordinator.provider.detail_provider
    assert detail_provider.skill_root == skill_root


def test_factory_tengli_selects_existing_real_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, task_runner="tengli")
    sentinel = object()
    calls: list[tuple[object, Settings, object, int]] = []

    def fake_real_builder(
        session_factory: object,
        received_settings: Settings,
        storage: object,
        *,
        concurrency: int,
    ) -> Any:
        calls.append((session_factory, received_settings, storage, concurrency))
        return sentinel

    monkeypatch.setattr(task_runner, "build_tengli_task_runner", fake_real_builder)

    for _, session_factory, storage in _factory_dependencies(settings):
        runner = task_runner.build_task_runner(session_factory, settings, storage)
        assert calls == [(session_factory, settings, storage, 6)]

    assert runner is sentinel


def test_production_tengli_quota_disabled_fails_before_provider_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        tmp_path,
        environment="production",
        task_runner="tengli",
        provider_quota_enabled=False,
    )
    provider_calls = 0

    def deny_provider(*args: object, **kwargs: object) -> MockTaskRunner:
        nonlocal provider_calls
        del args, kwargs
        provider_calls += 1
        raise AssertionError("provider construction must follow quota validation")

    monkeypatch.setattr(task_runner, "build_tengli_task_runner", deny_provider)

    for _, session_factory, storage in _factory_dependencies(settings):
        with pytest.raises(RuntimeError, match="provider quota"):
            task_runner.build_task_runner(session_factory, settings, storage)

    assert provider_calls == 0


@pytest.mark.parametrize(
    ("environment", "provider_quota_enabled"),
    [
        ("development", False),
        ("test", False),
        ("production", True),
    ],
)
def test_tengli_quota_policy_allows_provider_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    provider_quota_enabled: bool,
) -> None:
    settings = _settings(
        tmp_path,
        environment=environment,
        task_runner="tengli",
        provider_quota_enabled=provider_quota_enabled,
    )
    sentinel = object()
    provider_calls = 0

    def fake_provider(*args: object, **kwargs: object) -> Any:
        nonlocal provider_calls
        del args, kwargs
        provider_calls += 1
        return sentinel

    monkeypatch.setattr(task_runner, "build_tengli_task_runner", fake_provider)

    for _, session_factory, storage in _factory_dependencies(settings):
        runner = task_runner.build_task_runner(session_factory, settings, storage)

    assert runner is sentinel
    assert provider_calls == 1


@pytest.mark.parametrize(
    "provider_flag",
    [
        "product_image_provider_enabled",
        "reference_remix_provider_enabled",
        "model_pose_transfer_provider_enabled",
        "creative_image_provider_enabled",
        "amazon_visual_suite_provider_enabled",
        "crossborder_detail_provider_enabled",
    ],
)
def test_enabled_provider_feature_requires_quota_even_with_mock_runner(
    tmp_path: Path,
    provider_flag: str,
) -> None:
    settings = _settings(
        tmp_path,
        environment="production",
        task_runner="mock",
        provider_quota_enabled=False,
        **{provider_flag: True},
    )

    for _, session_factory, storage in _factory_dependencies(settings):
        with pytest.raises(RuntimeError, match="provider quota"):
            task_runner.build_task_runner(session_factory, settings, storage)


@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_default_mock_runner_still_allows_quota_disabled(
    tmp_path: Path,
    environment: str,
) -> None:
    settings = _settings(
        tmp_path,
        environment=environment,
        task_runner="mock",
        provider_quota_enabled=False,
    )

    for _, session_factory, storage in _factory_dependencies(settings):
        runner = task_runner.build_task_runner(session_factory, settings, storage)

    assert isinstance(runner, MockTaskRunner)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"tengli_api_key": SecretStr("")}, "API key"),
        ({"tengli_api_key": SecretStr("   ")}, "API key"),
        ({"tengli_smoke_budget_usd": 0}, "budget"),
        ({"tengli_smoke_budget_usd": 20_000_001}, "budget"),
        ({"tengli_smoke_budget_usd": float("nan")}, "budget"),
        ({"xinghe_concurrency": 0}, "concurrency"),
        ({"xinghe_concurrency": 3001}, "concurrency"),
        ({"tengli_monthly_budget_usd": 0.5}, "monthly budget"),
        ({"tengli_base_url": ""}, "base URL"),
        ({"tengli_base_url": "ftp://provider.example"}, "base URL"),
        ({"tengli_base_url": "http://["}, "base URL"),
        ({"tengli_image_model": "  "}, "image model"),
        ({"tengli_vision_model": ""}, "vision model"),
    ],
)
def test_tengli_configuration_fails_closed_before_provider_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    updates: dict[str, object],
    message: str,
) -> None:
    settings = _settings(tmp_path, task_runner="tengli", **updates)
    provider_calls = 0

    def deny_provider(*args: object, **kwargs: object) -> MockTaskRunner:
        nonlocal provider_calls
        del args, kwargs
        provider_calls += 1
        raise AssertionError("provider construction must follow validation")

    monkeypatch.setattr(task_runner, "build_tengli_task_runner", deny_provider)

    for _, session_factory, storage in _factory_dependencies(settings):
        with pytest.raises(RuntimeError, match=message):
            task_runner.build_task_runner(session_factory, settings, storage)

    assert provider_calls == 0


def test_tengli_failure_does_not_expose_api_key(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_key = "super-secret-provider-key"
    settings = _settings(
        tmp_path,
        task_runner="tengli",
        tengli_api_key=SecretStr(api_key),
        xinghe_concurrency=0,
    )

    with pytest.raises(RuntimeError) as captured:
        task_runner.validate_task_runner_settings(settings)

    exposed_text = " ".join(
        [
            str(captured.value),
            repr(captured.value),
            str(settings),
            repr(settings),
            caplog.text,
        ]
    )
    assert api_key not in exposed_text


def test_tengli_default_recurring_limits_are_unlimited(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        task_runner="tengli",
        tengli_api_key=SecretStr("test-provider-key"),
        xinghe_concurrency=6,
        database_url=f"sqlite:///{tmp_path / 'runner.db'}",
        storage_backend="local",
        storage_dir=tmp_path / "storage",
    )

    assert settings.tengli_smoke_budget_usd == 200
    assert settings.tengli_daily_budget_usd == 0
    assert settings.tengli_monthly_budget_usd == 0
    assert settings.tengli_daily_request_limit == 0
    task_runner.validate_task_runner_settings(settings)


def test_task_runner_mode_uses_explicit_prefixed_environment_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_TASK_RUNNER", "tengli")

    settings = Settings(_env_file=None)

    assert settings.task_runner == "tengli"


def test_xinghe_concurrency_uses_prefixed_environment_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_XINGHE_CONCURRENCY", "6")

    settings = Settings(_env_file=None)

    assert settings.xinghe_concurrency == 6


def test_settings_accept_maximum_provider_allowance() -> None:
    maximum = 20_000_000
    settings = Settings(
        _env_file=None,
        tengli_smoke_budget_usd=maximum,
        tengli_daily_budget_usd=maximum,
        tengli_monthly_budget_usd=maximum,
        tengli_daily_request_limit=maximum,
    )

    assert settings.tengli_smoke_budget_usd == maximum
    assert settings.tengli_daily_budget_usd == maximum
    assert settings.tengli_monthly_budget_usd == maximum
    assert settings.tengli_daily_request_limit == maximum


@pytest.mark.parametrize(
    "field",
    (
        "tengli_daily_budget_usd",
        "tengli_monthly_budget_usd",
        "tengli_daily_request_limit",
        "tengli_smoke_budget_usd",
    ),
)
def test_settings_reject_provider_allowance_above_maximum(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 20_000_001})


def test_api_rejects_invalid_tengli_before_building_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        tmp_path,
        task_runner="tengli",
        tengli_api_key=SecretStr(""),
    )

    def deny_database_build(database_url: str) -> None:
        del database_url
        raise AssertionError("dependencies were built before runner validation")

    monkeypatch.setattr(main_module, "build_database", deny_database_build)

    with pytest.raises(RuntimeError, match="API key"):
        main_module.create_app(settings)


def test_api_startup_uses_default_mock_runner(tmp_path: Path) -> None:
    settings = _settings(tmp_path, task_runner="mock")
    application = main_module.create_app(settings)

    assert isinstance(application.state.task_runner, MockTaskRunner)
    application.state.engine.dispose()


def test_celery_startup_path_uses_default_mock_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, task_runner="mock")
    monkeypatch.setattr(tasks, "settings", settings)
    monkeypatch.setattr(tasks, "_engine", None)
    monkeypatch.setattr(tasks, "_session_factory", None)
    monkeypatch.setattr(tasks, "_runner", None)
    monkeypatch.setattr(tasks, "_storage", None)
    monkeypatch.setattr(tasks, "_owner_pid", None)

    runner = tasks._process_runner()

    assert isinstance(runner, MockTaskRunner)
    assert tasks._engine is not None
    tasks._engine.dispose()


def test_outbox_startup_path_uses_default_mock_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, task_runner="mock")
    observed: list[MockTaskRunner] = []

    class StopWorker(Exception):
        pass

    def stop_after_first_publish(
        received_settings: Settings,
        session_factory: object,
        runner: MockTaskRunner,
        *,
        limit: int,
    ) -> int:
        del received_settings, session_factory
        assert limit == settings.outbox_batch_size
        observed.append(runner)
        raise StopWorker

    monkeypatch.setattr(outbox_worker, "Settings", lambda: settings)
    monkeypatch.setattr(
        outbox_worker,
        "publish_pending_events",
        stop_after_first_publish,
    )

    with pytest.raises(StopWorker):
        outbox_worker.main()

    assert len(observed) == 1
    assert isinstance(observed[0], MockTaskRunner)


def test_all_startup_paths_delegate_tengli_construction_to_shared_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, task_runner="tengli")
    calls: list[tuple[str, Settings]] = []
    sentinel = object()

    def record_api(
        session_factory: object,
        received_settings: Settings,
        storage: object,
    ) -> Any:
        del session_factory, storage
        calls.append(("api", received_settings))
        return sentinel

    monkeypatch.setattr(main_module, "build_task_runner", record_api)
    application = main_module.create_app(settings)
    assert application.state.task_runner is sentinel
    application.state.engine.dispose()

    def record_celery(
        session_factory: object,
        received_settings: Settings,
        storage: object,
    ) -> Any:
        del session_factory, storage
        calls.append(("celery", received_settings))
        return sentinel

    monkeypatch.setattr(tasks, "settings", settings)
    monkeypatch.setattr(tasks, "build_task_runner", record_celery)
    monkeypatch.setattr(tasks, "_engine", None)
    monkeypatch.setattr(tasks, "_session_factory", None)
    monkeypatch.setattr(tasks, "_runner", None)
    monkeypatch.setattr(tasks, "_storage", None)
    monkeypatch.setattr(tasks, "_owner_pid", None)
    assert tasks._process_runner() is sentinel
    assert tasks._engine is not None
    tasks._engine.dispose()

    class StopWorker(Exception):
        pass

    def record_outbox(
        session_factory: object,
        received_settings: Settings,
        storage: object,
    ) -> Any:
        del session_factory, storage
        calls.append(("outbox", received_settings))
        return sentinel

    def stop_publish(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise StopWorker

    monkeypatch.setattr(outbox_worker, "Settings", lambda: settings)
    monkeypatch.setattr(outbox_worker, "build_task_runner", record_outbox)
    monkeypatch.setattr(outbox_worker, "publish_pending_events", stop_publish)
    with pytest.raises(StopWorker):
        outbox_worker.main()

    assert calls == [("api", settings), ("celery", settings), ("outbox", settings)]
