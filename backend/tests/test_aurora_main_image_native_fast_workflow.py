"""Synthetic API acceptance for original-style domestic fast option B.

This exercises native compilation, delivery and paid-call recovery. Synthetic
images do not establish visual equivalence or real-provider performance.
"""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.aurora_main_image_fast_provider import (
    NATIVE_SCHEMA_VERSION,
    NATIVE_SKILL_ID,
    NATIVE_VERSION,
    RECEIPTS_KEY,
)
from app.aurora_main_image_planner import PAGE_ROLES
from app.models import (
    CreditReservationRecord,
    JobPlanRecord,
    JobRecord,
    WorkspaceCreditAccountRecord,
)
from app.services import canonical_hash
from app.task_runner import MockTaskRunner
from app.xinghe_agent import FakeXingheGateway

from .test_api import dispatch_pending, idem, upload_asset
from .test_aurora_main_image_fast_workflow import (
    BASE,
    assert_delivery,
    confirm,
    detail,
    retry,
)
from .test_aurora_main_image_fast_workflow import (
    Gateway as FirstFastGateway,
)
from .test_aurora_main_image_fast_workflow import (
    create as create_first_fast,
)

PROFILE = "aurora_native_fast_identity"


class NativeGateway(FirstFastGateway):
    async def plan(self, request):
        assert request.planning_profile == PROFILE, (
            "Native fast must only call its isolated one-pass identity profile"
        )
        result = await FakeXingheGateway.plan(
            self, replace(request, planning_profile="aurora_identity")
        )
        # Fake returns native identity without invoking a real provider. Keep
        # observations attached to the actual website request, not its adapter.
        self.planner_calls[-1] = request
        return result

    async def edit(self, request):
        result = await super().edit(request)
        # Separate synthetic gateways otherwise emit the same page-only IDs.
        return replace(
            result, provider_request_id=f"{result.provider_request_id}:{request.job_id}"
        )


def create(client, tmp_path, *, auto=True, gateway=None, concurrency=6):
    gateway = gateway or NativeGateway()
    state = client.app.state
    state.settings.task_runner = "tengli"
    state.settings.domestic_main_image_provider_enabled = True
    state.task_runner = MockTaskRunner(
        state.session_factory,
        0,
        storage=state.storage,
        xinghe_gateway=gateway,
        xinghe_concurrency=concurrency,
        domestic_main_image_provider_enabled=True,
        aurora_work_root=tmp_path / "native-fast-main",
    )
    asset = upload_asset(client)
    response = client.post(
        BASE + "/jobs",
        headers=idem(),
        json={
            "type": "domestic_main_image",
            "intent": "submit",
            "input": {"product_asset_ids": [asset["id"]]},
            "configuration": {
                "main_image_speed": "native_fast",
                "product_name": "轻量随行杯",
                "category": "保温杯",
                "selling_points": ["杯盖防误开"],
                "auto_start_after_plan": auto,
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["id"], gateway


def private_plan(client, job_id):
    with client.app.state.session_factory() as db:
        row = db.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == job_id))
        return deepcopy(row.payload)


def assert_native_closure(client, job_id):
    public = detail(client, job_id)["plan"]["payload"]
    private = private_plan(client, job_id)
    assert public["harness_state"] == "FINALIZED"
    assert public["technical_delivery"]["status"] == "COMPLETE"
    assert public["technical_delivery"]["visual_review"] == "NOT_REVIEWED"
    assert public["technical_delivery"]["set_audit_performed"] is False
    assert public["technical_delivery"]["page_count"] == 6
    assert private["_native_final_job_id"] == job_id
    manifest = private["_native_final_manifest"]
    session = private["_native_final_session"]
    assert manifest["status"] == session["state"] == "FINALIZED"
    assert session["accepted_pages"] == list(range(6))
    assert manifest["visual_review_status"] == "NOT_REVIEWED"
    assert manifest["set_audit_performed"] is False
    assert manifest["automatic_repairs_used"] == 0
    versions = session["generated_versions"]
    assert len(versions) == len(manifest["pages"]) == 6
    for rows in [versions, manifest["pages"]]:
        assert [row["page_index"] for row in rows] == list(range(6))
        assert all(row["status"] == "GENERATED_NOT_REVIEWED" for row in rows)
        assert all(row["visual_review_status"] == "NOT_REVIEWED" for row in rows)
        assert [row["sha256"] for row in rows] == public["technical_delivery"]["sha256"]
    for value in [public, session, manifest]:
        assert "set_audit_passed" not in value
        assert "final_user_approval" not in value
    assert (
        manifest["delivery_authorization"]["scope"]
        == "INITIAL_SUBMISSION_AUTHORIZATION"
    )
    return private


def assert_no_native_closure(payload):
    assert not any(key.startswith("_native_final") for key in payload)
    assert payload.get("harness_state") != "FINALIZED"
    assert payload.get("technical_delivery", {}).get("status") != "COMPLETE"


@pytest.mark.parametrize("auto", [True, False])
def test_native_fast_native_plan_six_downloads_billing_and_exact_prompts(
    client, tmp_path, auto, monkeypatch
):
    job_id, gateway = create(client, tmp_path, auto=auto)
    dispatch_pending(client)
    if not auto:
        current = detail(client, job_id)
        assert current["status"] == "awaiting_confirmation"
        assert not gateway.image_calls
        confirm(client, job_id)
        dispatch_pending(client)
    current = assert_delivery(client, job_id)
    payload = current["plan"]["payload"]
    assert current["configuration"]["settings"]["main_image_speed"] == "native_fast"
    assert current["plan"]["schema_version"] == NATIVE_SCHEMA_VERSION
    assert payload["version"] == NATIVE_VERSION
    assert payload["skill_id"] == NATIVE_SKILL_ID
    assert payload["validation"]["visual_review"] == "NOT_REVIEWED"
    assert isinstance(payload["product_identity_lock"], dict)
    assert isinstance(payload["campaign_style"], dict)
    assert [page["page_role"] for page in payload["pages"]] == list(PAGE_ROLES)
    assert all(page["main_selling_point"] for page in payload["pages"])
    assert all(page["composition_and_subject"] for page in payload["pages"])
    # Public website responses expose reviewable contracts and hide executable
    # prompts, private native compiler state, paths and material provenance IDs.
    public_text = str(payload)
    assert "_native" not in public_text
    assert "prompt" not in public_text
    assert str(tmp_path) not in public_text
    assert all("source_asset_ids" not in page for page in payload["pages"])
    stored = assert_native_closure(client, job_id)
    assert [page["page_index"] for page in stored["pages"]] == list(range(6))
    assert len(gateway.planner_calls) == 1
    assert gateway.planner_calls[0].planning_profile == PROFILE
    assert len(gateway.image_calls) == 6
    for request in gateway.image_calls:
        page = stored["pages"][request.page_number - 1]
        assert request.prompt == page["prompt"]
        assert page["source_asset_ids"] == [s.asset_id for s in request.sources]
        assert request.quality == "high" and request.size == "1024x1024"
        assert request.preserve_provider_artifact and request.single_dispatch
    assert current["api_request_count"] == 7
    assert current["cost"]["actual_credits"] == 90
    with client.app.state.session_factory() as db:
        reservation = db.scalar(
            select(CreditReservationRecord).where(
                CreditReservationRecord.job_id == job_id
            )
        )
        assert reservation.status == "settled" and reservation.settled_credits == 90
        assert db.get(WorkspaceCreditAccountRecord, "ws_test").reserved_credits == 0
    writes, commands = [], []

    async def no_original_write(*args, **kwargs):
        writes.append((args, kwargs))
        raise AssertionError("A completed replay must never write another original")

    def no_native_rewrite(*args, **kwargs):
        commands.append((args, kwargs))
        raise AssertionError("A completed replay must preserve its technical finalizer")

    monkeypatch.setattr(client.app.state.storage, "save_validated", no_original_write)
    monkeypatch.setattr(
        "app.aurora_main_image_native_fast.NativeFastHarnessRunner.run",
        no_native_rewrite,
    )
    assert asyncio.run(client.app.state.task_runner.run(job_id)) == "completed"
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 6
    assert not writes and not commands
    assert (
        private_plan(client, job_id)["_native_final_session"]
        == stored["_native_final_session"]
    )


def test_native_fast_and_first_fast_keep_separate_plan_and_history_identity(
    client, tmp_path
):
    native_id, native_gateway = create(client, tmp_path)
    dispatch_pending(client)
    native = assert_delivery(client, native_id)
    first_id, first_gateway = create_first_fast(client, tmp_path / "first")
    dispatch_pending(client)
    first = assert_delivery(client, first_id)
    restored = detail(client, native_id)
    assert restored["configuration"]["settings"]["main_image_speed"] == "native_fast"
    assert restored["plan"]["payload"]["version"] == NATIVE_VERSION
    assert native["plan"]["payload"]["skill_id"] != first["plan"]["payload"]["skill_id"]
    assert first["configuration"]["settings"]["main_image_speed"] == "fast"
    assert isinstance(first["plan"]["payload"]["product_identity_lock"], str)
    assert native_gateway.planner_calls[0].planning_profile == PROFILE
    assert (
        first_gateway.planner_calls[0].planning_profile
        == "aurora_main_image_fast_suite"
    )


@pytest.mark.parametrize("binding", ["speed", "version", "schema", "skill"])
def test_native_fast_rejects_plan_variant_binding_mismatch(client, tmp_path, binding):
    job_id, gateway = create(client, tmp_path, auto=False)
    dispatch_pending(client)
    confirm(client, job_id)
    with client.app.state.session_factory() as db:
        job = db.get(JobRecord, job_id)
        plan = db.scalar(select(JobPlanRecord).where(JobPlanRecord.job_id == job_id))
        if binding == "speed":
            config = deepcopy(job.configuration)
            config["settings"]["main_image_speed"] = "fast"
            job.configuration = config
        elif binding == "schema":
            plan.schema_version = "aurora-main-image-fast-plan-v1"
        else:
            payload = deepcopy(plan.payload)
            payload[binding if binding == "version" else "skill_id"] = (
                "aurora-main-image-fast-v1"
                if binding == "version"
                else "aurora-domestic-ecommerce-main-image-fast"
            )
            plan.payload = payload
            plan.payload_hash = canonical_hash(payload)
        db.commit()
    result = asyncio.run(client.app.state.task_runner.run(job_id))
    assert result in {"partial", "failed"}, detail(client, job_id)
    assert len(gateway.planner_calls) == 1 and not gateway.image_calls
    assert not [a for a in detail(client, job_id)["assets"] if a["is_deliverable"]]


def test_native_fast_failed_page_retry_uses_frozen_native_prompt_only(client, tmp_path):
    job_id, gateway = create(client, tmp_path, gateway=NativeGateway(failure_page=3))
    dispatch_pending(client)
    initial = detail(client, job_id)
    assert initial["status"] == "partial" and initial["succeeded_item_count"] == 5
    original = private_plan(client, job_id)
    assert_no_native_closure(original)
    child_id = retry(client, job_id)
    assert_no_native_closure(private_plan(client, child_id))
    dispatch_pending(client)
    current = assert_delivery(client, child_id)
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 7
    assert gateway.image_calls[-1].page_number == 3
    assert gateway.image_calls[-1].prompt == original["pages"][2]["prompt"]
    assert current["cost"]["actual_credits"] == 15
    assert sum(item["status"] == "reused" for item in current["items"]) == 5
    assert assert_native_closure(client, child_id)["pages"] == original["pages"]
    assert_no_native_closure(private_plan(client, job_id))


def test_native_fast_clone_generates_six_without_identity_replanning(client, tmp_path):
    job_id, gateway = create(client, tmp_path)
    dispatch_pending(client)
    assert_delivery(client, job_id)
    frozen = assert_native_closure(client, job_id)
    response = client.post(BASE + f"/jobs/{job_id}/clone", headers=idem())
    assert response.status_code == 201, response.text
    child_id = response.json()["id"]
    assert_no_native_closure(private_plan(client, child_id))
    with client.app.state.session_factory() as db:
        assert RECEIPTS_KEY not in db.get(JobRecord, child_id).configuration
    dispatch_pending(client)
    current = assert_delivery(client, child_id)
    assert len(gateway.planner_calls) == 1 and len(gateway.image_calls) == 12
    assert current["api_request_count"] == 6
    assert current["cost"]["actual_credits"] == 90
    assert assert_native_closure(client, child_id)["pages"] == frozen["pages"]
    assert private_plan(client, job_id)["_native_final_job_id"] == job_id
    assert all(request.job_id == child_id for request in gateway.image_calls[6:])
    with client.app.state.session_factory() as db:
        receipts = db.get(JobRecord, child_id).configuration[RECEIPTS_KEY]
        assert all(receipt["job_id"] == child_id for receipt in receipts.values())


def test_native_fast_unknown_call_blocks_retry_and_clone(client, tmp_path):
    class Unknown(NativeGateway):
        unknown_calls = 0

        async def edit(self, request):
            if request.page_number == 3:
                self.unknown_calls += 1
                raise TimeoutError("synthetic uncertain paid submission")
            return await super().edit(request)

    gateway = Unknown()
    job_id, _ = create(client, tmp_path, gateway=gateway)
    dispatch_pending(client)
    assert detail(client, job_id)["status"] == "partial"
    for suffix, body in [
        ("retry", {"scope": "failed", "item_ids": []}),
        ("clone", None),
    ]:
        response = client.post(
            BASE + f"/jobs/{job_id}/{suffix}", headers=idem(), json=body
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROVIDER_OUTCOME_UNKNOWN"
    assert gateway.unknown_calls == 1 and len(gateway.image_calls) == 5
    assert len(gateway.planner_calls) == 1


def test_native_fast_workers_share_six_active_claims(client, tmp_path):
    job_id, gateway = create(client, tmp_path, auto=False, concurrency=3000)
    dispatch_pending(client)
    confirm(client, job_id)
    first = client.app.state.task_runner.coordinator
    from app.execution import build_fake_coordinator

    second = build_fake_coordinator(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        xinghe_concurrency=3000,
        domestic_main_image_provider_enabled=True,
    )
    assert first.prepare(job_id) == "processing"
    for index in range(6):
        assert (first if index % 2 else second).claim_next_item(job_id) is not None
    assert first.claim_next_item(job_id) is None
    assert second.claim_next_item(job_id) is None


def test_paid_acceptance_script_dry_variants_and_output_directory_guard(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    script = repo / "backend/scripts/verify_main_image_fast_local.py"
    for variant, profile, directory in [
        ("fast", "aurora_main_image_fast_suite", "main-image-fast-local"),
        ("native_fast", PROFILE, "main-image-native-fast-local"),
    ]:
        result = subprocess.run(
            [sys.executable, str(script), "--variant", variant],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "no requests, no charges, no files written" in result.stdout
        assert profile in result.stdout and directory in result.stdout
    blocked = subprocess.run(
        [
            sys.executable,
            str(script),
            "--variant",
            "native_fast",
            "--output-dir",
            str(repo / "backend/.data/main-image-fast-local"),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert blocked.returncode == 2
    assert "requires its own directory" in blocked.stderr


def test_native_acceptance_observer_fences_other_profiles_and_duplicate_planning(
    tmp_path,
):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    script = (
        Path(__file__).resolve().parents[1] / "scripts/verify_main_image_fast_local.py"
    )
    spec = importlib.util.spec_from_file_location("native_fast_acceptance", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Counter:
        calls = 0

        async def plan(self, request):
            self.calls += 1
            return SimpleNamespace(
                payload={"synthetic": True},
                api_latency_seconds=0,
                error_code=None,
                normalized_usage={},
                actual_cost_micros=None,
            )

    counter = Counter()
    observer = module.Observer(counter, tmp_path, "native_fast")
    wrong = SimpleNamespace(planning_profile="aurora_identity")
    with pytest.raises(RuntimeError, match="Acceptance budget"):
        asyncio.run(observer.plan(wrong))
    assert counter.calls == 0
    assert not (tmp_path / "planning-request-private.json").exists()
    observer = module.Observer(counter, tmp_path, "native_fast")
    valid = SimpleNamespace(
        planning_profile=PROFILE,
        system_prompt="synthetic",
        product_information=(),
        sources=(),
    )
    asyncio.run(observer.plan(valid))
    with pytest.raises(RuntimeError, match="Acceptance budget"):
        asyncio.run(observer.plan(valid))
    assert counter.calls == 1


@pytest.mark.parametrize("flag", [False, True])
def test_native_identity_runtime_flag_only_accepts_explicit_false(
    client, tmp_path, flag
):
    class IdentityFlag(NativeGateway):
        async def plan(self, request):
            result = await super().plan(request)
            return replace(
                result, payload={**result.payload, "profile01_semantics_read": flag}
            )

    gateway = IdentityFlag()
    job_id, _ = create(client, tmp_path, gateway=gateway)
    dispatch_pending(client)
    assert len(gateway.planner_calls) == 1
    if flag is False:
        assert_delivery(client, job_id)
        private = assert_native_closure(client, job_id)
        assert "profile01_semantics_read" not in private["_native_session"]["identity"]
        assert len(gateway.image_calls) == 6
    else:
        current = detail(client, job_id)
        assert current["status"] == "failed"
        assert current.get("plan") is None
        assert not gateway.image_calls
