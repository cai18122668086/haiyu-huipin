"""Original native planning result replay, uncertainty and one-charge accounting."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from app.aurora_detail_runtime import AuroraDetailHarnessRunner
from app.domestic_detail.planner_dispatch import PlannerDispatchError
from app.domestic_detail.planning_calls import NativePlanningStore
from app.xinghe_agent import (
    PlannerRequest,
    TengliOpenAICompatibleGateway,
    XingheAgentError,
    XinghePlanningCoordinator,
    _planner_call_results,
    _planner_usage_record,
)

PROFILES = [
    "aurora_domestic_detail",
    "aurora_domestic_campaign_completion",
    "aurora_domestic_modules_completion",
    "aurora_domestic_plan_repair",
]


@pytest.fixture(autouse=True)
def journal_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.xinghe_agent.detail_workspace", lambda job_id: tmp_path / "jobs" / job_id
    )
    monkeypatch.setattr(
        "app.xinghe_agent._planner_prompt",
        lambda req: "SYNTHETIC " + json.dumps(list(req.product_information)),
    )
    monkeypatch.setattr(
        "app.xinghe_agent._planner_response_format", lambda req: {"type": "json_object"}
    )


def request(profile="aurora_domestic_detail", **kwargs):
    return PlannerRequest(
        workspace_id="ws_test",
        job_id="job_journal",
        group_key="primary",
        sources=(),
        page_count=8,
        language="zh-CN",
        product_name="Synthetic journal",
        product_information=(),
        platform_code="domestic-detail",
        size="1440x3840",
        idempotency_key="logical-original",
        planning_profile=profile,
        domestic_protocol=True,
        **kwargs,
    )


def coordinator(client, transport):
    provider = TengliOpenAICompatibleGateway(
        api_key="offline-primary",
        base_url="https://primary.offline.invalid/",
        planner_model="primary-model",
        image_model="image-model",
        transport=transport,
        failover_enabled=True,
        failover_api_key="offline-secondary",
        failover_base_url="https://alternate.offline.invalid/",
        failover_model="alternate-model",
        rotation_order="failover,primary",
    )
    return XinghePlanningCoordinator(
        client.app.state.session_factory,
        provider,
        storage=client.app.state.storage,
        skill_root=AuroraDetailHarnessRunner().skill_root,
        base_url_signature="synthetic",
        skill_id="aurora-domestic-ecommerce-detail-page",
    )


class Transport:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    async def post_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure == "timeout":
            raise XingheAgentError(
                "PROVIDER_TRANSPORT_FAILED",
                "Synthetic timeout after send",
                retryable=True,
            )
        if self.failure == "server_with_cost":
            return 502, {
                "id": "original-paid",
                "usage": {"cost_micros": 37},
                "error": {"message": "Synthetic unknown completion"},
            }
        if self.failure == "server":
            return 502, {"error": {"message": "Synthetic server error"}}
        if self.failure == "malformed":
            return 200, {"choices": [{"message": {"content": "{"}}]}
        return 200, {
            "id": f"original-{len(self.calls)}",
            "model": "reported-alternate",
            "choices": [{"message": {"content": '{"observed":true}'}}],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
                "cost_micros": 37,
            },
        }


@pytest.mark.parametrize("profile", PROFILES)
def test_native_replay_uses_one_original_call_and_charges_once(
    client, tmp_path, profile
):
    transport = Transport()
    planner = coordinator(client, transport)
    req = request(profile)
    first = asyncio.run(planner._native_planner_call(req))
    files = list(
        (tmp_path / "jobs" / req.job_id / "planner-calls").glob("*.response.json")
    )
    assert first.error_code is None and len(files) == 1
    original = files[0].read_bytes()
    # A new coordinator instance models a lost database completion/reclaimed execution.
    replay = asyncio.run(coordinator(client, transport)._native_planner_call(req))
    assert replay.error_code is None and len(transport.calls) == 1
    assert replay.payload == first.payload == {"observed": True}
    assert first.actual_cost_micros == replay.actual_cost_micros == 37
    assert replay.provider_envelope["native_planner_receipt"]["replayed"] is True
    assert files[0].read_bytes() == original
    assert len(_planner_call_results([first, replay])) == 1
    usage = [
        _planner_usage_record(value) for value in _planner_call_results([first, replay])
    ]
    assert sum(value["actual_cost_micros"] for value in usage) == 37
    assert usage[0]["native_planner_dispatch"]["routed_model"] == "alternate-model"
    assert (
        tmp_path
        / "jobs"
        / req.job_id
        / usage[0]["native_planner_receipt"]["request_file"]
    ).is_file()


@pytest.mark.parametrize(
    "failure", ["timeout", "malformed", "server", "server_with_cost"]
)
def test_uncertain_native_call_never_resends_even_with_changed_context(client, failure):
    transport = Transport(failure)
    planner = coordinator(client, transport)
    req = request()
    first = asyncio.run(planner._native_planner_call(req))
    second = asyncio.run(planner._native_planner_call(req))
    changed = asyncio.run(
        planner._native_planner_call(
            replace(req, product_information=("different context",))
        )
    )
    assert first.error_code is not None and second.error_code == first.error_code
    assert changed.error_code == "NATIVE_PLANNING_OUTCOME_UNKNOWN"
    assert len(transport.calls) == 1
    assert len(_planner_call_results([first, second])) == 1
    assert first.actual_cost_micros == (37 if failure == "server_with_cost" else None)


def test_lost_native_completion_receipt_does_not_create_a_second_dispatch(
    client, monkeypatch, tmp_path
):
    transport = Transport()
    planner = coordinator(client, transport)
    original_finish = NativePlanningStore.finish
    monkeypatch.setattr(
        NativePlanningStore,
        "finish",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("synthetic disk failure")
        ),
    )
    first = asyncio.run(planner._native_planner_call(request()))
    monkeypatch.setattr(NativePlanningStore, "finish", original_finish)
    second = asyncio.run(planner._native_planner_call(request()))
    assert first.error_code == second.error_code == "NATIVE_PLANNING_OUTCOME_UNKNOWN"
    assert len(transport.calls) == 1 and first.actual_cost_micros == 37
    root = tmp_path / "jobs" / "job_journal" / "planner-calls"
    assert len(list(root.glob("*.request.json"))) == 1
    assert list(root.glob("*.response.json")) == []


def test_completed_new_correction_has_a_distinct_bound_wire_key(client):
    transport = Transport()
    planner = coordinator(client, transport)
    req = request("aurora_domestic_plan_repair")
    first = asyncio.run(planner._native_planner_call(req))
    second = asyncio.run(
        planner._native_planner_call(
            replace(req, product_information=("new bounded correction",))
        )
    )
    assert first.error_code is second.error_code is None
    assert len(transport.calls) == 2
    assert (
        transport.calls[0]["headers"]["Idempotency-Key"]
        != transport.calls[1]["headers"]["Idempotency-Key"]
    )
    assert (
        transport.calls[0]["payload"]["messages"]
        != transport.calls[1]["payload"]["messages"]
    )
    assert len(_planner_call_results([first, second])) == 2


def test_completed_same_phase_cannot_switch_route_or_model(client):
    transport = Transport()
    planner = coordinator(client, transport)
    first = asyncio.run(planner._native_planner_call(request()))
    planner.gateway._rotation_order = ("primary", "failover")
    second = asyncio.run(planner._native_planner_call(request()))
    assert first.error_code is None
    assert second.error_code == "NATIVE_PLANNING_BINDING_CHANGED"
    assert len(transport.calls) == 1


def test_concurrent_same_logical_call_has_one_exclusive_dispatch_marker(client):
    async def run():
        class Blocking(Transport):
            entered = asyncio.Event()
            release = asyncio.Event()

            async def post_json(self, **kwargs):
                self.entered.set()
                await self.release.wait()
                return await super().post_json(**kwargs)

        transport = Blocking()
        planner = coordinator(client, transport)
        original = asyncio.create_task(planner._native_planner_call(request()))
        await transport.entered.wait()
        duplicate = await planner._native_planner_call(request())
        assert duplicate.error_code == "NATIVE_PLANNING_OUTCOME_UNKNOWN"
        transport.release.set()
        first = await original
        assert first.error_code is None and len(transport.calls) == 1

    asyncio.run(run())


def test_stored_planning_evidence_type_change_stops_replay(client, tmp_path):
    transport = Transport()
    planner = coordinator(client, transport)
    first = asyncio.run(planner._native_planner_call(request()))
    root = tmp_path / "jobs" / "job_journal" / "planner-calls"
    path = next(root.glob("*.response.json"))
    original = json.loads(path.read_bytes())
    original["response"]["observed"] = 1
    path.write_text(json.dumps(original), encoding="utf-8")
    second = asyncio.run(planner._native_planner_call(request()))
    assert first.error_code is None
    assert second.error_code == "NATIVE_PLANNING_OUTCOME_UNKNOWN"
    assert len(transport.calls) == 1


def test_source_uncertain_phase_requires_reconciliation(tmp_path):
    from app.domestic_detail.planner_dispatch import route_descriptor
    from app.domestic_detail.planning_calls import call_binding

    route = route_descriptor(
        provider="synthetic",
        route="synthetic",
        model="synthetic",
        endpoint="offline://synthetic",
        credential="synthetic",
        kind="synthetic",
    )

    def binding(key):
        return call_binding(
            profile="aurora_domestic_modules_completion",
            group_key="primary",
            target="M03",
            logical_key=key,
            semantics={"facts": ["synthetic"]},
            prompt="synthetic",
            schema={"type": "object"},
            route=route,
            skill_hash="a" * 64,
            runtime_hash="b" * 64,
        )

    source = NativePlanningStore(
        tmp_path / "source" / "planner-calls", binding("old job phase")
    )
    source.begin()
    current = NativePlanningStore(
        tmp_path / "current" / "planner-calls", binding("new job phase")
    )
    with pytest.raises(PlannerDispatchError, match="Reconcile|completion|original"):
        current.assert_no_unknown_source(source.root)
    assert not current.store.request_path.exists()


@pytest.mark.parametrize("failure", ["timeout", "server_with_cost", "lost_completion"])
def test_coordinator_retry_ancestry_stops_an_uncertain_original_phase(
    client, monkeypatch, failure
):
    from app.models import JobRecord, utc_now
    from tests.test_api import upload_asset
    from tests.test_domestic_detail_execution import _create_detail_job

    transport = Transport(
        "timeout"
        if failure == "timeout"
        else "server_with_cost"
        if failure == "server_with_cost"
        else None
    )
    planner = coordinator(client, transport)
    asset = upload_asset(client, filename="retry-ancestry-synthetic.png")
    jobs = [
        _create_detail_job(
            client, asset["id"], market_region="domestic", auto_start_after_plan=False
        )
        for _ in range(3)
    ]
    original_finish = NativePlanningStore.finish
    if failure == "lost_completion":

        def lose_completion(*args, **kwargs):
            raise OSError("synthetic completion write interruption")

        monkeypatch.setattr(NativePlanningStore, "finish", lose_completion)
    first = asyncio.run(
        planner._native_planner_call(replace(request(), job_id=jobs[0]["id"]))
    )
    assert first.error_code is not None and len(transport.calls) == 1
    monkeypatch.setattr(NativePlanningStore, "finish", original_finish)
    with client.app.state.session_factory() as db:
        for index, job in enumerate(jobs):
            record = db.get(JobRecord, job["id"])
            if index:
                record.retry_scope = "failed"
                record.retry_of_job_id = jobs[index - 1]["id"]
            if index < 2:
                record.status = "failed"
                record.stage = "planning_failed"
                record.finished_at = utc_now()
                record.progress = 100
        db.commit()
    for job in jobs[1:]:
        result = asyncio.run(
            planner._native_planner_call(
                replace(
                    request(), job_id=job["id"], idempotency_key=f"new-job-{job['id']}"
                )
            )
        )
        assert result.error_code == "NATIVE_PLANNING_OUTCOME_UNKNOWN"
        assert result.external_request is False and len(transport.calls) == 1


def test_unusable_original_planning_text_is_saved_before_rejection(
    client, monkeypatch, tmp_path
):
    transport = Transport()
    planner = coordinator(client, transport)
    original_plan = planner.gateway.plan

    async def contradict_original(req):
        result = await original_plan(req)
        return replace(result, payload={"observed": False})

    monkeypatch.setattr(planner.gateway, "plan", contradict_original)
    first = asyncio.run(planner._native_planner_call(request()))
    assert first.error_code == "NATIVE_PLANNING_RAW_REPLY_CHANGED"
    assert first.actual_cost_micros == 37 and len(transport.calls) == 1
    root = tmp_path / "jobs" / "job_journal" / "planner-calls"
    original = next(root.glob("*.response.json")).read_bytes()
    assert json.loads(original)["result"]["payload"] == {"observed": False}
    assert next(root.glob("*.reply.txt")).read_bytes() == b'{"observed":true}'
    second = asyncio.run(planner._native_planner_call(request()))
    assert second.error_code == "NATIVE_PLANNING_RAW_REPLY_CHANGED"
    assert len(transport.calls) == 1
    assert next(root.glob("*.response.json")).read_bytes() == original
