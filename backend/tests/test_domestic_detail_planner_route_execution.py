"""A full synthetic campaign reports the model actually sent to the JSON route."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from app.aurora_detail_runtime import AuroraDetailHarnessRunner
from app.models import JobRecord
from app.provider_quota import reserve_provider_job_quota
from app.xinghe_agent import (
    TengliOpenAICompatibleGateway,
    XingheImageProvider,
    _artifact_from_bytes,
)
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_domestic_detail_execution import (
    _aurora_approval_payload,
    _create_detail_job,
    _gateway,
)


@pytest.mark.parametrize("brand_confirmation", [False, True])
def test_alternate_first_model_is_preserved_through_plan_generation_and_usage(
    client, monkeypatch, brand_confirmation
):
    gateway = _gateway(client)
    original_plan = gateway.plan

    class Transport:
        requests = []
        reply = None

        async def post_json(self, **kwargs):
            self.requests.append(kwargs)
            return 200, {
                "id": f"memory-json-{len(self.requests)}",
                "model": "reported-resolved-alternate",
                "choices": [
                    {"message": {"content": json.dumps(self.reply, ensure_ascii=False)}}
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                    "cost_micros": 41,
                },
            }

    transport = Transport()
    routed = TengliOpenAICompatibleGateway(
        api_key="offline-primary",
        base_url="https://primary.offline.invalid/",
        planner_model="primary-model",
        image_model="synthetic-image",
        transport=transport,
        failover_enabled=True,
        failover_api_key="offline-alternate",
        failover_base_url="https://alternate.offline.invalid/",
        failover_model="alternate-model",
        rotation_order="failover,primary",
    )
    monkeypatch.setattr(gateway, "provider_name", routed.provider_name)
    monkeypatch.setattr(gateway, "planner_model", "primary-model")
    monkeypatch.setattr(gateway, "native_planner_route", routed.native_planner_route)
    sampler = XingheImageProvider(
        client.app.state.session_factory,
        gateway,
        storage=client.app.state.storage,
        skill_root=AuroraDetailHarnessRunner().skill_root,
        max_concurrency=1,
        skill_id="aurora-domestic-ecommerce-detail-page",
    )

    async def plan(request):
        if request.planning_profile == "aurora_detail_pair_audit":
            context = json.loads(request.product_information[0])
            candidate = request.sources[-1]
            artifact = _artifact_from_bytes(
                candidate.payload,
                filename=candidate.filename,
                expected_size="1440x3840",
                preserve_provider_artifact=True,
            )
            transport.reply = sampler._native_detail_judgment(context, artifact)
        else:
            transport.reply = (await original_plan(request)).payload
        return await routed.plan(request)

    monkeypatch.setattr(gateway, "plan", plan)
    original_edit = gateway.edit

    async def paid_synthetic_image(request):
        # Nonzero cost detects rebilling when the same canvas supplies its second slice.
        result = await original_edit(request)
        return replace(result, actual_cost_micros=101)

    monkeypatch.setattr(gateway, "edit", paid_synthetic_image)
    source = upload_asset(client, filename="alternate-model-synthetic.png")
    created = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    # This adapter fixture selects its in-memory gateway after HTTP submission.
    # Use the normal durable quota function for this new isolated test job.
    with client.app.state.session_factory() as db:
        job = db.get(JobRecord, created["id"])
        if brand_confirmation:
            configuration = deepcopy(job.configuration)
            configuration["settings"]["brandless_confirmation"] = None
            job.configuration = configuration
        reserve_provider_job_quota(
            db,
            job=job,
            actor_user_id="usr_test",
            estimated_cost_micros=1_000_000,
            provider_request_count=20,
            daily_limit_micros=0,
            monthly_limit_micros=0,
            daily_request_limit=0,
            planner_reserve_micros=10_000,
            image_budget_micros=990_000,
            group_count=1,
        )
        db.commit()
    dispatch_pending(client)
    planned = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created['id']}"
    ).json()
    assert planned["status"] == "awaiting_confirmation", json.dumps(
        planned, ensure_ascii=False
    )
    if brand_confirmation:
        assert planned["stage"] == "intake_confirmation_required"
        assert len(transport.requests) == 1
        first_usage = planned["plan"]["payload"]["usage"]["planner"]
        assert len(first_usage) == 1 and first_usage[0]["actual_cost_micros"] == 41
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created['id']}/intake/resolve",
            headers=idem(),
            json={
                "expected_revision": planned["plan"]["revision"],
                "confirmation_text": "我确认此合成测试商品无品牌",
                "resolutions": [],
                "brandless_confirmation": "我确认此合成测试商品无品牌，不添加独立标志",
            },
        )
        assert response.status_code == 202, response.text
        dispatch_pending(client)
        planned = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created['id']}"
        ).json()
        assert planned["stage"] == "awaiting_confirmation", json.dumps(
            planned, ensure_ascii=False
        )
    initial_profiles = ["aurora_domestic_intake"] * (2 if brand_confirmation else 1)
    initial_profiles += ["aurora_bounded_story"] + ["aurora_bounded_pair"] * 4
    initial_count = len(initial_profiles)
    assert [
        r["payload"]["response_format"]["json_schema"]["name"]
        for r in transport.requests
    ] == initial_profiles
    payload = planned["plan"]["payload"]
    assert payload["provider"]["planner_model"] == "alternate-model"
    assert gateway.planner_model == "primary-model"
    assert len(transport.requests) == initial_count
    assert all(
        value["payload"]["model"] == "alternate-model" for value in transport.requests
    )
    initial = payload["usage"]["planner"]
    assert (
        len(initial) == initial_count
        and sum(value["actual_cost_micros"] for value in initial) == initial_count * 41
    )
    assert all(
        value["native_planner_dispatch"]["routed_model"] == "alternate-model"
        for value in initial
    )
    assert {key for value in initial for key in value if key.endswith("receipt")} == {
        "native_intake_receipt",
        "native_planner_receipt",
    }
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert confirmed.status_code == 202, confirmed.text
    dispatch_pending(client)
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{created['id']}"
    ).json()
    assert current["status"] == "awaiting_final_review", json.dumps(
        current, ensure_ascii=False
    )
    total_count = initial_count + 4
    assert len(gateway.image_calls) == 4 and len(transport.requests) == total_count
    planner_usage = current["plan"]["payload"]["usage"]["planner"]
    assert len(planner_usage) == total_count
    assert (
        sum(value["actual_cost_micros"] for value in planner_usage) == total_count * 41
    )
    assert all(
        value["native_planner_dispatch"]["routed_model"] == "alternate-model"
        for value in planner_usage
    )
    assert all(
        value["native_planner_dispatch"]["reported_model"]
        == "reported-resolved-alternate"
        for value in planner_usage
    )
    # Verify cost in the durable ledger behind the public job response.
    with client.app.state.session_factory() as db:
        assert (
            db.get(JobRecord, created["id"]).actual_cost_micros
            == total_count * 41 + 4 * 101
        )
    receipt_ids = [
        (kind, receipt["binding_sha256"])
        for row in planner_usage
        for kind in ("native_intake_receipt", "native_planner_receipt")
        if (receipt := row.get(kind)) is not None
    ]
    assert len(receipt_ids) == initial_count and len(set(receipt_ids)) == initial_count
    asyncio.run(client.app.state.task_runner.run(created["id"]))
    assert len(gateway.image_calls) == 4 and len(transport.requests) == total_count
    with client.app.state.session_factory() as db:
        assert (
            db.get(JobRecord, created["id"]).actual_cost_micros
            == total_count * 41 + 4 * 101
        )
