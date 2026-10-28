"""Native route, per-call accounting and legacy failover boundaries."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from app.domestic_detail.planner_dispatch import (
    PlannerDispatchError,
    result_with_dispatch,
    validate_dispatch_result,
)
from app.xinghe_agent import (
    PlannerRequest,
    TengliOpenAICompatibleGateway,
    XingheAgentError,
)

PROFILES = [
    "aurora_domestic_intake",
    "aurora_domestic_detail",
    "aurora_domestic_plan_repair",
    "aurora_domestic_modules_completion",
    "aurora_domestic_campaign_completion",
    "aurora_detail_pair_audit",
    "aurora_detail_campaign_audit",
    "aurora_detail_identity_audit",
    "aurora_detail_revision_scope",
]


@pytest.fixture(autouse=True)
def isolate_transport_from_prompt_contract(monkeypatch):
    # These tests target dispatch. Real native prompt contracts run separately.
    monkeypatch.setattr(
        "app.xinghe_agent._planner_prompt",
        lambda request: "SYNTHETIC dispatch evidence",
    )
    monkeypatch.setattr(
        "app.xinghe_agent._planner_response_format",
        lambda request: {"type": "json_object"},
    )


def request(profile, **kwargs):
    return PlannerRequest(
        workspace_id="synthetic",
        job_id="synthetic",
        group_key="synthetic",
        sources=(),
        page_count=2,
        language="zh-CN",
        product_name="Synthetic product",
        product_information=(),
        platform_code="domestic-detail",
        size="1440x3840",
        idempotency_key="synthetic-dispatch",
        planning_profile=profile,
        domestic_protocol=True,
        **kwargs,
    )


def gateway(transport, order="primary,failover"):
    return TengliOpenAICompatibleGateway(
        api_key="offline-primary-key",
        base_url="https://primary.offline.invalid/",
        planner_model="primary-model",
        image_model="image-model",
        transport=transport,
        failover_enabled=True,
        failover_api_key="offline-alternate-key",
        failover_base_url="https://alternate.offline.invalid/",
        failover_model="alternate-model",
        rotation_order=order,
    )


def response(model="reported-model"):
    return {
        "id": "synthetic-original",
        "model": model,
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"observed": True, "note": "合成观察"}, ensure_ascii=False
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


@pytest.mark.parametrize("profile", [*PROFILES, "aurora_v20_visual_audit"])
@pytest.mark.parametrize(
    "failure", ["server", "timeout", "rate_limit", "invalid_message"]
)
def test_native_errors_use_one_transport_and_legacy_keeps_failover(profile, failure):
    class Transport:
        calls = 0

        async def post_json(self, **kwargs):
            self.calls += 1
            if failure == "timeout":
                raise XingheAgentError(
                    "PROVIDER_TRANSPORT_FAILED", "Synthetic timeout", retryable=True
                )
            if failure == "invalid_message":
                return 200, {"choices": [{"message": {"content": "{"}}]}
            return (429 if failure == "rate_limit" else 502), {
                "error": {"message": "Synthetic rejection"}
            }

    transport = Transport()
    provider = gateway(transport)
    result = asyncio.run(provider.plan(request(profile)))
    assert result.error_code is not None
    assert transport.calls == (2 if profile == "aurora_v20_visual_audit" else 1)
    if profile in PROFILES:
        route = provider.native_planner_route()
        validate_dispatch_result(asdict(result), route)
        dispatch = result.provider_envelope["native_planner_dispatch"]
        assert dispatch["transport_attempts"] == 1
        assert (
            dispatch["outcome"]
            == {
                "server": "outcome_unknown",
                "timeout": "outcome_unknown",
                "rate_limit": "rejected",
                "invalid_message": "response_invalid",
            }[failure]
        )
    else:
        assert "native_planner_dispatch" not in (result.provider_envelope or {})


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("order", ["primary,failover", "failover,primary"])
def test_actual_first_route_and_reported_model_are_preserved(profile, order):
    class Transport:
        calls = []

        async def post_json(self, **kwargs):
            self.calls.append(kwargs)
            return 200, response()

    transport = Transport()
    provider = gateway(transport, order)
    route = provider.native_planner_route()
    result = asyncio.run(provider.plan(request(profile, native_dispatch=route)))
    assert result.error_code is None and len(transport.calls) == 1
    expected_model = (
        "alternate-model" if order.startswith("failover") else "primary-model"
    )
    assert transport.calls[0]["payload"]["model"] == expected_model
    assert route["model"] == expected_model
    dispatch = result.provider_envelope["native_planner_dispatch"]
    assert dispatch["routed_model"] == expected_model
    assert dispatch["reported_model"] == "reported-model"
    assert provider.planner_model == "primary-model"
    assert result.raw_usage == response()["usage"]
    assert json.loads(result.audit_raw_content)["note"] == "合成观察"
    assert (
        result_with_dispatch(asdict(result), route)["provider_envelope"]
        == result.provider_envelope
    )
    assert "offline-primary-key" not in json.dumps(route)
    assert "https://" not in json.dumps(dispatch)


def test_bound_route_drift_stops_before_any_transport():
    class Transport:
        calls = 0

        async def post_json(self, **kwargs):
            self.calls += 1
            return 200, response()

    transport = Transport()
    provider = gateway(transport)
    bound = provider.native_planner_route()
    provider._rotation_order = ("failover", "primary")
    result = asyncio.run(
        provider.plan(request("aurora_detail_pair_audit", native_dispatch=bound))
    )
    assert result.error_code == "NATIVE_PLANNER_ROUTE_CHANGED"
    assert transport.calls == 0 and result.external_request is False
    assert result.provider_envelope["native_planner_dispatch"]["outcome"] == "not_sent"
    validate_dispatch_result(asdict(result), bound)


@pytest.mark.parametrize("order", ["primary,failover", "failover,primary"])
def test_concurrent_native_calls_have_independent_attempt_counters(order):
    async def run():
        class Transport:
            calls = 0
            both_started = asyncio.Event()

            async def post_json(self, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    self.both_started.set()
                await self.both_started.wait()
                return 200, response()

        transport = Transport()
        provider = gateway(transport, order)
        results = await asyncio.gather(
            *[
                provider.plan(
                    replace(request(profile), idempotency_key=f"concurrent:{index}")
                )
                for index, profile in enumerate(PROFILES[:2])
            ]
        )
        assert transport.calls == 2 and provider.external_network_request_count == 2
        for result in results:
            assert (
                result.provider_envelope["native_planner_dispatch"][
                    "transport_attempts"
                ]
                == 1
            )
            validate_dispatch_result(asdict(result), provider.native_planner_route())

    asyncio.run(run())


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "route",
        "two_attempts",
        "bool_attempt",
        "bool_status",
        "model",
        "original_text",
    ],
)
def test_real_dispatch_evidence_cannot_be_invented_or_retyped(tamper):
    class Transport:
        async def post_json(self, **kwargs):
            return 200, response()

    provider = gateway(Transport())
    route = provider.native_planner_route()
    value = deepcopy(asdict(asyncio.run(provider.plan(request(PROFILES[0])))))
    dispatch = value["provider_envelope"]["native_planner_dispatch"]
    if tamper == "missing":
        value["provider_envelope"].pop("native_planner_dispatch")
    elif tamper == "route":
        dispatch["route_sha256"] = "a" * 64
    elif tamper == "two_attempts":
        dispatch["transport_attempts"] = 2
    elif tamper == "bool_attempt":
        dispatch["transport_attempts"] = True
    elif tamper == "bool_status":
        dispatch["http_status"] = True
    elif tamper == "model":
        dispatch["routed_model"] = "unselected-model"
    else:
        value["audit_raw_content"] += "changed original"
    with pytest.raises(PlannerDispatchError):
        result_with_dispatch(value, route)


@pytest.mark.parametrize("remaining", [1800.0, 240.0, 100.0, 0.0])
def test_supplier_203_second_reply_respects_shared_timeout_and_campaign_deadline(
    monkeypatch, remaining
):
    # Virtual elapsed time exercises the observed supplier latency without a wait.
    clock = [1000.0]
    monkeypatch.setattr("app.xinghe_agent.time.monotonic", lambda: clock[0])

    class Transport:
        calls = []

        async def post_json(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["timeout_seconds"] < 203:
                clock[0] += kwargs["timeout_seconds"]
                raise XingheAgentError(
                    "PROVIDER_TRANSPORT_FAILED", "Synthetic timeout", retryable=True
                )
            clock[0] += 203
            return 200, response()

    transport = Transport()
    provider = gateway(transport)
    result = asyncio.run(provider.plan(request(
        "aurora_domestic_detail", planning_deadline=1000.0 + remaining,
    )))
    dispatch = result.provider_envelope["native_planner_dispatch"]
    if remaining == 0:
        assert result.error_code == "AURORA_PLANNING_DEADLINE"
        assert not transport.calls and not result.external_request
        assert dispatch["outcome"] == "not_sent"
    else:
        assert len(transport.calls) == 1
        assert transport.calls[0]["timeout_seconds"] == min(
            provider.timeout_seconds, remaining
        )
        assert (result.error_code is None) == (remaining >= 203)
        assert dispatch["outcome"] == (
            "succeeded" if remaining >= 203 else "outcome_unknown"
        )
    validate_dispatch_result(asdict(result), provider.native_planner_route())


def test_serial_authoring_has_one_deadline_and_no_180_second_override(monkeypatch):
    from app.domestic_detail import authoring

    clock = [1000.0]
    monkeypatch.setattr("app.xinghe_agent.time.monotonic", lambda: clock[0])
    timeouts = []

    class Transport:
        async def post_json(self, **kwargs):
            timeouts.append(kwargs["timeout_seconds"])
            assert kwargs["timeout_seconds"] >= 203
            clock[0] += 203
            return 200, response()

    provider = gateway(Transport())
    deadline = clock[0] + authoring.PLANNING_SECONDS
    for index in range(6):  # story + one correction + four adjacent pairs
        value = request(
            "aurora_domestic_detail" if index < 2
            else "aurora_domestic_modules_completion",
            planning_deadline=deadline,
        )
        result = asyncio.run(provider.plan(value))
        assert result.error_code is None
    assert len(timeouts) == 6 and clock[0] < deadline
    # An explicitly configured shorter gateway limit must still be honored.
    provider.timeout_seconds = 240
    asyncio.run(provider.plan(request("aurora_domestic_detail")))
    assert timeouts[-1] == 240


@pytest.mark.parametrize("profile", ["xinghe_domestic_detail_4", *PROFILES])
def test_long_planner_wait_is_configurable_and_still_honors_deadline(
    profile, monkeypatch
):
    from app.config import Settings
    from app.xinghe_agent import build_tengli_gateway

    clock = [1000.0]
    monkeypatch.setattr("app.xinghe_agent.time.monotonic", lambda: clock[0])

    class Transport:
        calls = []

        async def post_json(self, **kwargs):
            self.calls.append(kwargs)
            return 200, response()

    provider = build_tengli_gateway(Settings(
        _env_file=None,
        tengli_api_key="synthetic-key",
        analysis_timeout_seconds=1200,
    ))
    transport = Transport()
    provider.transport = transport
    result = asyncio.run(provider.plan(request(profile)))
    assert result.error_code is None
    assert transport.calls[-1]["timeout_seconds"] == 1200
    # Image transport retains its existing timeout, and the native campaign
    # deadline can only shorten the longer planner socket timeout.
    assert provider.timeout_seconds == 600
    result = asyncio.run(provider.plan(request(profile, planning_deadline=1100.0)))
    assert result.error_code is None
    assert transport.calls[-1]["timeout_seconds"] == 100


def test_long_planner_timeout_does_not_replay_an_uncertain_request():
    class Transport:
        calls = 0

        async def post_json(self, **kwargs):
            self.calls += 1
            raise XingheAgentError(
                "PROVIDER_TRANSPORT_FAILED", "Timeout", retryable=True
            )

    transport = Transport()
    provider = gateway(transport)
    provider.planner_timeout_seconds = 1200
    result = asyncio.run(provider.plan(request("aurora_domestic_detail")))
    assert transport.calls == 1
    assert result.error_code == "PROVIDER_TRANSPORT_FAILED"
    assert (
        result.provider_envelope["native_planner_dispatch"]["outcome"]
        == "outcome_unknown"
    )
