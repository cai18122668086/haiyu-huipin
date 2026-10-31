"""Synthetic images validate isolation, one-pass planning and paid-call fences."""

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.execution import ExecutionCoordinator
from app.models import ReplicaDocumentRecord
from app.provider import ProviderArtifact, ProviderOutcome
from app.replica_fast import CONCURRENCY, SKILLS, compile_plan
from app.replica_fast_provider import FastReplicaProvider
from app.xinghe_agent import FakeXingheGateway, ImageCallResult

from .test_replica import upload

ROUTE = "/api/v1/workspaces/ws_test/replica"


def proposal(count=1, screens=1):
    return {
        "product_identity_lock": "Red product, preserve silhouette and original marks",
        "confirmed_facts": ["用户授权红色商品"],
        "style_lock": "One coherent material, light and typographic hierarchy",
        "references": [
            {
                "reference_index": index,
                "columns": [
                    {
                        "bbox": [0, 0, 1000, 1000],
                        "screens": [
                            {
                                "end_y": round((i + 1) * 1000 / screens),
                                "page_duty": f"Distinct buyer question {i}",
                                "visual_plan": (
                                    "A specific new commercial scene with product "
                                    "identity and differentiated visual proof"
                                ),
                                "visible_copy": ["真实商品"],
                                "forbidden_elements": (
                                    "No invented claims or source branding"
                                ),
                            }
                            for i in range(screens)
                        ],
                    }
                ],
            }
            for index in range(count)
        ],
    }


class Gateway(FakeXingheGateway):
    provider_name = "test"
    image_model = "synthetic"
    pricing_version = "zero"

    def __init__(self, raw, failure=None):
        super().__init__()
        self.raw, self.failure = raw, failure
        self.plans, self.images = [], []
        self.active = self.maximum = 0

    async def plan(self, request):
        self.plans.append(request)
        return SimpleNamespace(
            payload=deepcopy(self.raw),
            normalized_usage={"reported": True},
            actual_cost_micros=0,
            provider_request_id="plan-test",
            api_latency_seconds=0.01,
            error_code=None,
        )

    async def edit(self, request):
        self.images.append(request)
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        if request.group_key == self.failure:
            return ImageCallResult(
                outcome=ProviderOutcome.FAILED,
                provider_request_id=None,
                raw_usage={},
                normalized_usage={"reported": True},
                api_latency_seconds=0.01,
                actual_cost_micros=0,
                estimated_cost_micros=0,
                error_code="KNOWN_REJECTION",
            )
        if self.failure == "unknown":
            raise TimeoutError("synthetic unknown submission")
        w, h = map(int, request.size.split("x"))
        output = BytesIO()
        Image.new("RGB", (w, h), "red").save(output, "PNG")
        data = output.getvalue()
        return ImageCallResult(
            outcome=ProviderOutcome.SUCCEEDED,
            provider_request_id="image-" + uuid4().hex,
            raw_usage={},
            normalized_usage={"reported": True},
            api_latency_seconds=0.01,
            actual_cost_micros=0,
            estimated_cost_micros=0,
            artifact=ProviderArtifact(
                payload=data,
                filename="result.png",
                content_type="image/png",
                width=w,
                height=h,
                sha256=hashlib.sha256(data).hexdigest(),
            ),
        )


def create(client, gateway, method="exact", count=1, auto=False):
    client.app.state.settings.reference_remix_provider_enabled = True
    client.app.state.replica_gateway = gateway
    product = upload(client, "red")
    refs = [
        upload(client, ["blue", "green", "yellow", "purple"][i % 4])
        for i in range(count)
    ]
    response = client.post(
        ROUTE + "/sessions",
        json={
            "request_id": str(uuid4()),
            "replica_speed": "fast",
            "replication_method": method,
            "product_asset_ids": [product],
            "references": [{"asset_id": ref} for ref in refs],
            "facts": "用户授权红色商品",
            "auto_start_after_plan": auto,
        },
    )
    assert response.status_code == 202, response.text
    return client.get(ROUTE + "/documents/" + response.json()["id"]).json()


def confirm(client, doc):
    response = client.post(
        ROUTE + "/sessions/" + doc["id"] + "/confirm",
        json={"plan_hash": doc["payload"]["plan_hash"]},
    )
    assert response.status_code == 202, response.text
    return response.json()["payload"]["job_ids"][0]


def execute(client, gateway, job_id):
    state = client.app.state
    coordinator = ExecutionCoordinator(
        state.session_factory,
        FastReplicaProvider(state.session_factory, gateway, state.storage),
        max_concurrency=20,
        artifact_storage=state.storage,
    )
    return asyncio.run(coordinator.run(job_id))


@pytest.mark.parametrize("method", ["exact", "style"])
def test_one_pass_split_parallel_delivery_and_original_isolation(
    client, method, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Original native planning must not run for fast suites")

    monkeypatch.setattr("app.replica.NativeReplicaRun.prepare", forbidden)
    gateway = Gateway(proposal(count=4))
    doc = create(client, gateway, method, count=4)
    assert doc["status"] == "awaiting_confirmation", doc
    assert len(gateway.plans) == 1
    assert gateway.plans[0].planning_profile == "replica_fast_suite"
    assert doc["payload"]["skill_id"] == SKILLS[method]
    assert doc["payload"]["planning_rounds"] == 1
    assert (
        not {"_fast_plan", "_fast_analysis_receipt", "_fast_invocations"}
        & doc["payload"].keys()
    )
    job_id = confirm(client, doc)
    assert confirm(client, doc) == job_id
    assert execute(client, gateway, job_id) == "completed", [
        (i["error_code"], i["error_message"])
        for i in client.get(f"/api/v1/workspaces/ws_test/jobs/{job_id}").json()["items"]
    ]
    assert len(gateway.plans) == 1 and len(gateway.images) == 4
    assert gateway.maximum == 4
    assert all(
        r.single_dispatch and r.preserve_provider_artifact for r in gateway.images
    )
    assert all(
        [s.source_role for s in r.sources]
        == ["style_reference", "product_reference", "format_canvas"]
        for r in gateway.images
    )
    restored = client.get(ROUTE + "/documents/" + doc["id"]).json()
    assert restored["status"] == "completed"
    assert restored["payload"]["replica_speed"] == "fast"
    progress = restored["payload"]["execution_progress"]
    assert (
        progress["generated"] == 4 and progress["reviewed"] == progress["accepted"] == 0
    )
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{job_id}").json()
    assert (
        len(
            [
                a
                for a in job["assets"]
                if a["direction"] == "output" and a["is_deliverable"]
            ]
        )
        == 4
    )


def test_long_reference_is_split_into_complete_ordered_independent_screens(client):
    gateway = Gateway(proposal(screens=2))
    doc = create(client, gateway)
    assert len(doc["payload"]["plans"]) == 2
    job_id = confirm(client, doc)
    assert execute(client, gateway, job_id) == "completed", [
        (i["error_code"], i["error_message"])
        for i in client.get(f"/api/v1/workspaces/ws_test/jobs/{job_id}").json()["items"]
    ]
    assert len(gateway.images) == 2
    with Image.open(BytesIO(gateway.images[0].sources[0].payload)) as crop:
        assert crop.size == (80, 45)


def test_invalid_coverage_stops_before_image_submission_and_preserves_receipt(client):
    raw = proposal(screens=2)
    raw["references"][0]["columns"][0]["screens"][-1]["end_y"] = 900
    gateway = Gateway(raw)
    doc = create(client, gateway)
    assert doc["status"] == "failed"
    assert doc["payload"]["error_code"] == "FAST_REPLICA_PLAN_INVALID"
    assert len(gateway.plans) == 1 and not gateway.images
    with client.app.state.session_factory() as session:
        saved = session.get(ReplicaDocumentRecord, doc["id"])
        assert saved.payload["_fast_analysis_receipt"]["actual_cost_micros"] == 0


def test_failed_screen_retry_does_not_replan_or_regenerate_successes(client):
    gateway = Gateway(proposal(count=2), failure="F002")
    doc = create(client, gateway, count=2)
    job_id = confirm(client, doc)
    assert execute(client, gateway, job_id) == "partial"
    gateway.failure = None
    response = client.post(
        ROUTE + f"/sessions/{doc['id']}/jobs/{job_id}/retry",
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    replacement = response.json()["payload"]["job_ids"][0]
    assert execute(client, gateway, replacement) == "completed"
    assert len(gateway.plans) == 1
    assert [r.group_key for r in gateway.images] == ["F001", "F002", "F002"]


def test_unknown_image_outcome_blocks_resubmission(client):
    gateway = Gateway(proposal(), failure="unknown")
    doc = create(client, gateway)
    job_id = confirm(client, doc)
    assert execute(client, gateway, job_id) == "failed"
    response = client.post(
        ROUTE + f"/sessions/{doc['id']}/jobs/{job_id}/retry",
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 409, response.text
    assert len(gateway.images) == 1


def test_real_router_selects_fast_provider_without_extra_planning(client):
    from app.execution import build_fake_coordinator

    gateway = Gateway(proposal())
    doc = create(client, gateway, auto=True)
    assert doc["payload"]["job_ids"]
    assert doc["payload"]["plans"][0]["prompt"] == ""
    state = client.app.state
    coordinator = build_fake_coordinator(
        state.session_factory,
        0,
        storage=state.storage,
        xinghe_gateway=gateway,
        reference_remix_provider_enabled=True,
        xinghe_concurrency=20,
    )
    job_id = doc["payload"]["job_ids"][0]
    assert asyncio.run(coordinator.run(job_id)) == "completed"
    assert len(gateway.plans) == len(gateway.images) == 1
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{job_id}").json()
    delivered = [
        asset for asset in job["assets"]
        if asset["direction"] == "output" and asset["is_deliverable"]
    ]
    assert len(delivered) == 1
    assert job["output_asset_count"] == 1


def test_duplicate_workers_share_the_twenty_image_claim_limit(client):
    gateway = Gateway(proposal(count=11, screens=2))
    doc = create(client, gateway, count=11)
    job_id = confirm(client, doc)
    state = client.app.state
    provider = FastReplicaProvider(state.session_factory, gateway, state.storage)
    first = ExecutionCoordinator(state.session_factory, provider, max_concurrency=3000)
    second = ExecutionCoordinator(state.session_factory, provider, max_concurrency=3000)
    assert first.prepare(job_id) == "processing"
    for index in range(CONCURRENCY):
        assert (first if index % 2 == 0 else second).claim_next_item(job_id) is not None
    assert second.claim_next_item(job_id) is None
    assert first.claim_next_item(job_id) is None
    assert first.max_concurrency == second.max_concurrency == 3000


def test_fast_suite_refills_without_waiting_for_a_slow_screen(client):
    class RollingGateway(Gateway):
        async def edit(self, request):
            if request.group_key == "F001":
                await asyncio.wait_for(self.third_started.wait(), timeout=3)
            if request.group_key == "F003":
                self.third_started.set()
            return await super().edit(request)

    gateway = RollingGateway(proposal(count=4))
    doc = create(client, gateway, count=4)
    job_id = confirm(client, doc)
    state = client.app.state
    coordinator = ExecutionCoordinator(
        state.session_factory,
        FastReplicaProvider(state.session_factory, gateway, state.storage),
        max_concurrency=2,
        artifact_storage=state.storage,
    )

    async def run():
        gateway.third_started = asyncio.Event()
        return await coordinator.run(job_id)

    assert asyncio.run(run()) == "completed"
    assert [r.group_key for r in gateway.images].index("F003") < [
        r.group_key for r in gateway.images
    ].index("F001")
    assert len(gateway.images) == 4


def test_large_fast_suite_uses_twenty_slots_and_delivers_every_screen(client):
    gateway = Gateway(proposal(count=11, screens=2))
    doc = create(client, gateway, count=11)
    job_id = confirm(client, doc)
    assert execute(client, gateway, job_id) == "completed"
    assert gateway.maximum == CONCURRENCY == 20
    assert len(gateway.images) == 22


@pytest.mark.parametrize("size,canvas", [
    ((1254, 1254), [1024, 1024]),
    ((960, 1639), [1024, 1748]),
    ((959, 1641), [1024, 1753]),
    ((957, 1643), [1024, 1758]),
    ((887, 1774), [1024, 2048]),
])
def test_native_auto_resolution_preserves_original_bytes(size, canvas):
    output = BytesIO()
    Image.new("RGB", size, "red").save(output, "PNG")
    raw = output.getvalue()
    artifact = FastReplicaProvider._artifact(raw, canvas)
    assert artifact.payload == raw
    assert (artifact.width, artifact.height) == size
    assert not artifact.locally_resampled


@pytest.mark.parametrize("size", [(512, 1024), (768, 1024), (700, 2800), (1254, 1254)])
def test_low_resolution_or_wrong_aspect_stays_blocked(size):
    from app.errors import ApiError

    output = BytesIO()
    Image.new("RGB", size, "red").save(output, "PNG")
    with pytest.raises(ApiError, match="画幅"):
        FastReplicaProvider._artifact(output.getvalue(), [1024, 2048])


def test_previous_size_rejection_recovers_native_image_without_another_call(
    client, monkeypatch
):
    from app.errors import ApiError

    raw = proposal()
    raw["references"][0]["columns"][0]["bbox"] = [0, 0, 562, 1000]
    gateway = Gateway(raw)
    doc = create(client, gateway)
    job_id = confirm(client, doc)
    validate = FastReplicaProvider._artifact

    # Distinguish the gateway's creation from the delivery validator below.
    async def native_edit(request):
        result = await Gateway.edit(gateway, request)
        output = BytesIO()
        Image.new("RGB", (887, 1774), "red").save(output, "PNG")
        return replace(result, artifact=validate(output.getvalue(), [1024, 2048]))

    gateway.edit = native_edit

    def reject_old(data, canvas):
        artifact = validate(data, canvas)
        if min(artifact.width, artifact.height) < 1024:
            raise ApiError(502, "FAST_REPLICA_CANVAS_INVALID", "旧尺寸门槛")
        return artifact

    monkeypatch.setattr(FastReplicaProvider, "_artifact", staticmethod(reject_old))
    assert execute(client, gateway, job_id) == "failed"
    monkeypatch.setattr(FastReplicaProvider, "_artifact", staticmethod(validate))
    retried = client.post(
        ROUTE + f"/sessions/{doc['id']}/jobs/{job_id}/retry",
        json={"request_id": str(uuid4())},
    )
    assert retried.status_code == 202, retried.text
    replacement = retried.json()["payload"]["job_ids"][0]
    assert execute(client, gateway, replacement) == "completed"
    assert len(gateway.images) == len(gateway.plans) == 1


def test_still_invalid_cached_canvas_regenerates_only_on_explicit_retry(client):
    class WrongCanvasGateway(Gateway):
        async def edit(self, request):
            result = await super().edit(request)
            if len(self.images) == 1:
                output = BytesIO()
                Image.new("RGB", (1024, 1024), "red").save(output, "PNG")
                return replace(result, artifact=FastReplicaProvider._artifact(
                    output.getvalue(), [1024, 1024]
                ))
            return result

    gateway = WrongCanvasGateway(proposal())
    doc = create(client, gateway)
    job_id = confirm(client, doc)
    assert execute(client, gateway, job_id) == "failed"
    assert len(gateway.images) == 1
    response = client.post(
        ROUTE + f"/sessions/{doc['id']}/jobs/{job_id}/retry",
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    replacement = response.json()["payload"]["job_ids"][0]
    assert execute(client, gateway, replacement) == "completed"
    assert len(gateway.images) == 2 and len(gateway.plans) == 1


def test_generated_receipt_recovers_after_delivery_storage_failure(client, monkeypatch):
    gateway = Gateway(proposal())
    doc = create(client, gateway)
    job_id = confirm(client, doc)
    storage = client.app.state.storage
    save = storage.save_validated

    async def fail_delivery(image):
        if "/generated/" in image.object_key:
            raise OSError("synthetic final-delivery failure")
        return await save(image)

    monkeypatch.setattr(storage, "save_validated", fail_delivery)
    assert execute(client, gateway, job_id) == "failed"
    monkeypatch.setattr(storage, "save_validated", save)
    retried = client.post(
        ROUTE + f"/sessions/{doc['id']}/jobs/{job_id}/retry",
        json={"request_id": str(uuid4())},
    )
    assert retried.status_code == 202, retried.text
    replacement = retried.json()["payload"]["job_ids"][0]
    assert execute(client, gateway, replacement) == "completed"
    assert len(gateway.images) == len(gateway.plans) == 1


def test_tampered_plan_and_direct_job_bypass_are_rejected(client):
    gateway = Gateway(proposal())
    doc = create(client, gateway)
    with client.app.state.session_factory() as session:
        row = session.get(ReplicaDocumentRecord, doc["id"])
        changed = deepcopy(row.payload)
        changed["_fast_plan"]["units"][0]["visible_copy"] = ["tampered"]
        row.payload = changed
        session.commit()
    response = client.post(
        ROUTE + f"/sessions/{doc['id']}/confirm",
        json={"plan_hash": doc["payload"]["plan_hash"]},
    )
    assert response.status_code == 409
    request = {
        "type": "reference_remix",
        "intent": "submit",
        "input": {
            "product_asset_ids": gateway.plans[0].intake_settings or ["missing"],
            "reference_asset_ids": ["other"],
        },
        "configuration": {"reference_skill": SKILLS["exact"], "replica_speed": "fast"},
    }
    response = client.post(
        "/api/v1/workspaces/ws_test/jobs",
        json=request,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 409


def test_original_default_and_native_protocol_unchanged():
    from app.replica import AnalysisRequest

    body = AnalysisRequest(
        request_id="legacy-input",
        product_asset_ids=["product"],
        references=[{"asset_id": "reference"}],
        replication_method="exact",
    )
    assert body.replica_speed == "standard"
    import sys

    from app.replica_direct_delivery import V15_BINDING, V16_BINDING
    from app.replica_exact_compact import protocol_binding, run_class

    assert protocol_binding()["version"] == "exact-compact-v17"
    current_v16 = run_class("exact", V16_BINDING)
    assert sys.modules[current_v16.__module__].protocol_binding() == {
        "version": "exact-compact-v16",
        "content_sha256": (
            "63df401796fde30686b4485af519a2f5ee0f3a3577cfd2225dfc352d2cd2c59c"
        ),
    }
    historical = run_class("exact", V15_BINDING)
    assert sys.modules[historical.__module__].protocol_binding() == V15_BINDING


@pytest.mark.parametrize("mutate", ["overlap", "duplicate", "out_of_bounds"])
def test_malformed_geometry_cannot_be_compiled(mutate):
    from app.errors import ApiError

    raw = proposal()
    if mutate == "overlap":
        raw["references"][0]["columns"] *= 2
    elif mutate == "duplicate":
        raw = proposal(screens=2)
        raw["references"][0]["columns"][0]["screens"][0]["end_y"] = 1000
    else:
        raw["references"][0]["columns"][0]["bbox"][0] = -1
    with pytest.raises(ApiError):
        compile_plan(
            raw,
            {"references": [{"asset_id": "r", "aspect_ratio": "follow-reference"}]},
            {"r": ("r.png", "image/png", "0" * 64, "r", 80, 90)},
            "doc",
            "ws",
            "1" * 64,
        )
