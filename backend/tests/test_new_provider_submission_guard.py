from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import app.routes_v1 as routes_v1
from app.domain import JobType
from app.errors import ApiError
from app.routes_v1 import (
    _ensure_detail_skill_provider_enabled,
    _reserve_real_provider_job_if_enabled,
)

NEW_PROVIDER_FLAGS = {
    JobType.PRODUCT_IMAGE.value: "product_image_provider_enabled",
    JobType.REFERENCE_REMIX.value: "reference_remix_provider_enabled",
    JobType.MODEL_POSE_TRANSFER.value: "model_pose_transfer_provider_enabled",
    JobType.CREATIVE_IMAGE.value: "creative_image_provider_enabled",
    JobType.AMAZON_VISUAL_SUITE.value: "amazon_visual_suite_provider_enabled",
}


def _tengli_settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "task_runner": "tengli",
        "tengli_daily_budget_usd": 1.0,
        "tengli_monthly_budget_usd": 10.0,
        "tengli_daily_request_limit": 100,
        "tengli_planner_reserve_micros": 250_000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(settings: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings))
    )


@pytest.mark.parametrize("job_type", list(NEW_PROVIDER_FLAGS))
def test_tengli_rejects_unregistered_provider_before_quota_or_dispatch(
    job_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quota_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        routes_v1,
        "reserve_provider_job_quota",
        lambda *args, **kwargs: quota_calls.append(kwargs),
    )
    job = SimpleNamespace(type=job_type)

    with pytest.raises(ApiError) as captured:
        _reserve_real_provider_job_if_enabled(
            SimpleNamespace(),
            _request(_tengli_settings()),
            job,
            "user_test",
        )

    assert captured.value.status_code == 503
    assert captured.value.code == "REAL_PROVIDER_NOT_REGISTERED"
    assert captured.value.details == {
        "job_type": job_type,
        "provider_called": False,
    }
    assert quota_calls == []


@pytest.mark.parametrize(
    ("job_type", "flag_name"),
    list(NEW_PROVIDER_FLAGS.items()),
)
def test_enabled_single_image_provider_reserves_expected_provider_requests(
    job_type: str,
    flag_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quota_calls: list[dict[str, Any]] = []

    def record_quota(_session: object, **kwargs: Any) -> None:
        quota_calls.append(kwargs)

    monkeypatch.setattr(routes_v1, "reserve_provider_job_quota", record_quota)
    settings = _tengli_settings(**{flag_name: True})
    job = SimpleNamespace(
        type=job_type,
        configuration={"input": {}},
        expected_item_count=4,
        estimated_cost_max_micros=360_000,
    )

    _reserve_real_provider_job_if_enabled(
        SimpleNamespace(),
        _request(settings),
        job,
        "user_test",
    )

    assert len(quota_calls) == 1
    reservation = quota_calls[0]
    assert reservation["job"] is job
    amazon_suite = job_type == JobType.AMAZON_VISUAL_SUITE.value
    reference_remix = job_type == JobType.REFERENCE_REMIX.value
    expected_cost = (
        360_000 * 2 + settings.tengli_planner_reserve_micros * 9
        if amazon_suite
        else 360_000 * 2 + settings.tengli_planner_reserve_micros * 2
        if reference_remix or job_type == JobType.CREATIVE_IMAGE.value
        else 360_000 * 2
    )
    assert reservation["estimated_cost_micros"] == expected_cost
    assert reservation["provider_request_count"] == (
        51 if amazon_suite
        else 10 if reference_remix or job_type == JobType.CREATIVE_IMAGE.value
        else 8
    )
    assert reservation["image_budget_micros"] == 360_000
    assert reservation["group_count"] == int(amazon_suite or reference_remix)


@pytest.mark.parametrize(
    (
        "job_type",
        "configuration",
        "expected_item_count",
        "image_cost",
        "expected_cost",
        "expected_requests",
        "expected_group_count",
    ),
    [
        (
            JobType.LOCAL_EDIT.value,
            {"input": {}},
            1,
            90_000,
            1_180_000,
            6,
            0,
        ),
        (
            JobType.DETAIL_PACK.value,
            {
                "settings": {"market_region": "domestic"},
                "input": {
                    "product_groups": [
                        {"group_key": "front", "asset_ids": ["asset-1"]},
                        {"group_key": "back", "asset_ids": ["asset-2"]},
                    ]
                }
            },
            3,
            270_000,
            8_540_000,
            35,
            2,
        ),
    ],
)
def test_provider_quota_formulas_match_current_fail_closed_contract(
    job_type: str,
    configuration: dict[str, object],
    expected_item_count: int,
    image_cost: int,
    expected_cost: int,
    expected_requests: int,
    expected_group_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quota_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        routes_v1,
        "reserve_provider_job_quota",
        lambda _session, **kwargs: quota_calls.append(kwargs),
    )
    job = SimpleNamespace(
        type=job_type,
        configuration=configuration,
        expected_item_count=expected_item_count,
        estimated_cost_max_micros=image_cost,
    )

    _reserve_real_provider_job_if_enabled(
        SimpleNamespace(),
        _request(_tengli_settings()),
        job,
        "user_test",
    )

    assert len(quota_calls) == 1
    reservation = quota_calls[0]
    assert reservation["estimated_cost_micros"] == expected_cost
    assert reservation["provider_request_count"] == expected_requests
    assert reservation["group_count"] == expected_group_count


def test_mock_keeps_new_skill_submission_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quota_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        routes_v1,
        "reserve_provider_job_quota",
        lambda *args, **kwargs: quota_calls.append(kwargs),
    )

    _reserve_real_provider_job_if_enabled(
        SimpleNamespace(),
        _request(SimpleNamespace(task_runner="mock")),
        SimpleNamespace(type=JobType.PRODUCT_IMAGE.value),
        "user_test",
    )

    assert quota_calls == []


def test_tengli_rejects_international_detail_without_crossborder_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quota_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        routes_v1,
        "reserve_provider_job_quota",
        lambda *args, **kwargs: quota_calls.append(kwargs),
    )
    job = SimpleNamespace(
        type=JobType.DETAIL_PACK.value,
        configuration={"settings": {"market_region": "international"}},
        expected_item_count=1,
        estimated_cost_max_micros=90_000,
    )

    with pytest.raises(ApiError) as captured:
        _reserve_real_provider_job_if_enabled(
            SimpleNamespace(),
            _request(_tengli_settings(crossborder_detail_provider_enabled=False)),
            job,
            "user_test",
        )

    assert captured.value.status_code == 503
    assert captured.value.code == "REAL_PROVIDER_NOT_REGISTERED"
    assert captured.value.details == {
        "job_type": JobType.DETAIL_PACK.value,
        "capability": "crossborder_detail",
        "provider_called": False,
    }
    assert quota_calls == []


def test_tengli_allows_domestic_detail_without_crossborder_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quota_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        routes_v1,
        "reserve_provider_job_quota",
        lambda _session, **kwargs: quota_calls.append(kwargs),
    )
    job = SimpleNamespace(
        type=JobType.DETAIL_PACK.value,
        configuration={
            "settings": {"market_region": "domestic"},
            "input": {"product_groups": [{"group_key": "front"}]},
        },
        expected_item_count=1,
        estimated_cost_max_micros=90_000,
    )

    _reserve_real_provider_job_if_enabled(
        SimpleNamespace(),
        _request(_tengli_settings(crossborder_detail_provider_enabled=False)),
        job,
        "user_test",
    )

    assert len(quota_calls) == 1


def test_confirm_boundary_guard_uses_same_crossborder_capability_gate() -> None:
    job = SimpleNamespace(
        type=JobType.DETAIL_PACK.value,
        configuration={"settings": {"market_region": "international"}},
    )

    with pytest.raises(ApiError) as captured:
        _ensure_detail_skill_provider_enabled(
            _request(_tengli_settings(crossborder_detail_provider_enabled=False)),
            job,
        )

    assert captured.value.code == "REAL_PROVIDER_NOT_REGISTERED"
