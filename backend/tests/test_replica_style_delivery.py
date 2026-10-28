"""Synthetic style orchestration and file integrity; no live quality claims."""

import asyncio
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from shutil import rmtree
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.replica_native import (
    NativeReplicaRun,
    ReplicaNativeError,
    digest,
    read_json,
    write_json,
)
from app.replica_native_provider import (
    ReplicaCanvasMismatch,
    ReplicaImageOutcomeUnknown,
)
from app.replica_style_delivery import (
    POLICY,
    STATE_FILE,
    ReplicaDeliveryProvider,
    StyleDeliveryInvalid,
    activate,
    deliver,
    direct_state,
    progress_state,
    validate_source,
)
from app.storage import ImageValidator, LocalImageStorage

from .test_replica_resolution import result
from .test_replica_retry import json_record


def fixture(tmp_path, route="CAROUSEL_SET"):
    root = tmp_path / "run"
    root.mkdir()
    skill = (
        Path(__file__).parents[1] / "skill_sources/all-category-style-benchmark-still"
    )
    # Use the unchanged native fixture builders/validators in an isolated process.
    # These are explicit fixture preplanning receipts, never production AI passes.
    script = """
import json,sys
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/scripts',sys.argv[1]+'/tests']
from test_workflow import files,analysis,fact_file,plan_file
from workflow import (new_run,classify,compile_crops,passport,facts,plan,
                      audit_plan,save,dump)
root=Path(sys.argv[2]);route=sys.argv[3]
prod,ref,crop=files(root)
ws,d=new_run(root,[prod],[ref],fact_file(root))
classify(d,route)
a=root/'analysis.json';dump(a,analysis(crop))
compile_crops(d,a,ws);passport(d,a);facts(d,fact_file(root))
plan(d,plan_file(root,route),ws)
pa=root/'pre-plan-audit.json';dump(pa,{'review_kind':'fixture','route':route,'unit_ids':d['layout']['unit_ids'],'pass':True})
audit_plan(d,pa);save(ws,d)
print(json.dumps({'run':str(ws)}))
"""
    native = subprocess.run(
        [sys.executable, "-c", script, str(skill), str(root), route],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert native.returncode == 0, native.stderr
    storage = LocalImageStorage(
        tmp_path / "objects", ImageValidator(10_000_000, 20_000_000)
    )
    calls = []

    async def forbidden(*args, **kwargs):
        raise AssertionError("Post-generation AI calls are forbidden")

    async def edit(request):
        calls.append(request)
        return result((144, 384) if route == "DETAIL_PAGE" else (1080, 1440))

    gateway = SimpleNamespace(
        edit=edit,
        plan=forbidden,
        provider_name="synthetic",
        image_model="synthetic",
        planner_model="synthetic-planner",
        pricing_version="test",
    )
    run = NativeReplicaRun(
        "style", "rep_" + uuid4().hex, "ws_test", gateway, storage, root=root
    )
    run.run = Path(json.loads(native.stdout)["run"])
    run.context = {
        "user_facts": "Synthetic geometry facts",
        "references": [],
        "product_asset_ids": ["synthetic"],
    }
    write_json(root / "context.json", run.context)
    asyncio.run(run.snapshot())
    provider = ReplicaDeliveryProvider(None, gateway, storage)

    async def save(run, *args, **kwargs):
        await run.snapshot()

    provider._save = save
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_style",
        job_item_id="item_style",
        attempt_id="attempt_style",
    )
    return run, provider, request, calls


@pytest.mark.parametrize("route,count", [("CAROUSEL_SET", 3), ("DETAIL_PAGE", 10)])
def test_native_generation_and_split_without_audit_or_repair(tmp_path, route, count):
    run, provider, request, calls = fixture(tmp_path, route)
    original = deepcopy(run.state())
    original_files = {
        p: p.read_bytes() for p in (run.run / "artifacts").rglob("*") if p.is_file()
    }
    outputs = asyncio.run(deliver(provider, run, request))
    state = direct_state(run)
    assert len(outputs) == count and len(calls) == (
        count // 2 if route == "DETAIL_PAGE" else count
    )
    assert run.state() == original
    assert all(p.read_bytes() == data for p, data in original_files.items())
    assert state["state"] == "DELIVERED" and state["review_status"] == "NOT_REVIEWED"
    assert all(r["status"] == "GENERATED" for r in state["targets"])
    for r, job in zip(state["targets"], state["jobs"], strict=True):
        native = r["request"]
        assert native["generation_scope"]["unit_ids"] == job["unit_ids"]
        assert (
            native["product_image_bindings"] and native["sanitized_style_crop_bindings"]
        )
        assert native["revision_index"] == 0
    assert all(
        Image.open(p).size == ((144, 192) if route == "DETAIL_PAGE" else (1080, 1440))
        for p in outputs
    )
    from app.replica_progress import native_progress

    progress = native_progress(progress_state(state))
    assert (
        progress["generated"] == count
        and progress["accepted"] == progress["reviewed"] == 0
    )
    snap = asyncio.run(run.snapshot())
    validate_source(
        snap,
        {**snap, "files": {**snap["files"]}},
        f"ws_test/replica/{run.document_id}/",
    )
    before = len(calls)
    rmtree(run.root)
    recovered = NativeReplicaRun(
        "style", run.document_id, "ws_test", run.gateway, run.storage, root=run.root
    )
    asyncio.run(recovered.restore(snap))
    assert asyncio.run(deliver(provider, recovered, request)) == outputs
    assert len(calls) == before and recovered.state() == original


@pytest.mark.parametrize(
    "route,size",
    [
        ("CAROUSEL_SET", (540, 720)),
        ("DETAIL_PAGE", (144, 386)),
        ("DETAIL_PAGE", (145, 387)),
    ],
)
def test_wrong_native_dimensions_fail_without_second_generation(tmp_path, route, size):
    run, provider, request, calls = fixture(tmp_path, route)

    async def wrong(request):
        calls.append(request)
        return result(size)

    run.gateway.edit = wrong
    with pytest.raises(ReplicaCanvasMismatch):
        asyncio.run(deliver(provider, run, request))
    assert len(calls) == 1
    assert direct_state(run)["state"] != "DELIVERED"


@pytest.mark.parametrize("recorded", [False, True])
def test_legacy_failed_or_returned_unrecorded_canvas_reused_after_temp_loss(
    tmp_path, recorded
):
    run, provider, request, calls = fixture(tmp_path)
    approved = asyncio.run(run.snapshot())
    state = run.state()
    canvas = run.root / "artwork/real-legacy.png"
    canvas.parent.mkdir()
    Image.new("RGB", (1080, 1440), "purple").save(canvas)
    sha = digest(canvas.read_bytes())
    row = {"target_id": "U01", "path": str(canvas), "sha256": sha, "token": "legacy"}
    state["artwork"].append(row) if recorded else None
    state["units"]["U01"].update(
        status="revision_allowed" if recorded else "awaiting_artwork",
        generation_token="legacy",
    )
    state.update(
        state="REVISION_ALLOWED" if recorded else "GENERATION_ALLOWED",
        active_token="legacy",
    )
    state["audits"].append(
        {
            "target_id": "U01",
            "pass": False,
            "artwork_sha256": sha,
            "review_kind": "visual_model",
        }
    )
    write_json(run.run / "artifacts/workflow.json", state)
    write_json(
        run.root / "invocations.json",
        {
            "legacy": {
                "state": "returned",
                "path": str(canvas),
                "sha256": sha,
                "target": "U01",
                "job_id": "job_old",
            }
        },
    )
    source = asyncio.run(run.snapshot())
    validate_source(source, approved, f"ws_test/replica/{run.document_id}/")
    rmtree(run.root)
    asyncio.run(run.restore(source))
    outputs = asyncio.run(deliver(provider, run, request))
    assert len(calls) == 2 and Path(outputs[0]).read_bytes() == canvas.read_bytes()
    assert run.state() == state and run.state()["audits"][0]["pass"] is False


def test_unknown_result_fence_survives_restore_and_never_resends(tmp_path):
    run, provider, request, calls = fixture(tmp_path)
    write_json(
        run.root / "invocations.json",
        {"unknown": {"state": "invoked", "target": "U01"}},
    )
    snap = asyncio.run(run.snapshot())
    rmtree(run.root)
    asyncio.run(run.restore(snap))
    with pytest.raises(ReplicaImageOutcomeUnknown):
        asyncio.run(deliver(provider, run, request))
    assert calls == []


@pytest.mark.parametrize("unknown", [False, True])
def test_inflight_direct_request_survives_process_loss(tmp_path, unknown):
    run, provider, request, calls = fixture(tmp_path)
    if unknown:
        from app.provider import ProviderOutcome

        async def uncertain(request):
            calls.append(request)
            return result((1080, 1440), ProviderOutcome.OUTCOME_UNKNOWN)

        run.gateway.edit = uncertain
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(deliver(provider, run, request))
    else:
        render = provider._render

        async def interrupted(*args, **kwargs):
            await render(*args, **kwargs)
            raise RuntimeError("Synthetic loss before recording a returned canvas")

        provider._render = interrupted
        with pytest.raises(RuntimeError):
            asyncio.run(deliver(provider, run, request))
        provider._render = render
    snap = asyncio.run(run.snapshot())
    rmtree(run.root)
    asyncio.run(run.restore(snap))
    if unknown:
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(deliver(provider, run, request))
        assert len(calls) == 1
    else:
        assert len(asyncio.run(deliver(provider, run, request))) == 3
        assert len(calls) == 3


@pytest.mark.parametrize("bad", ["plan", "facts", "scope", "hash", "order"])
def test_changed_approved_source_is_rejected(tmp_path, bad):
    run, _, _, _ = fixture(tmp_path)
    approved = asyncio.run(run.snapshot())
    previous = deepcopy(approved)
    if bad == "facts":
        previous["files"]["context.json"] = json_record({"user_facts": "changed"})
    elif bad == "scope":
        name = next(k for k, v in previous["files"].items() if "object_key" in v)
        previous["files"][name]["object_key"] = "other/replica/a.png"
    else:
        name = (run.run / "artifacts/workflow.json").relative_to(run.root).as_posix()
        state = run.state()
        if bad == "order":
            state["layout"]["unit_ids"].reverse()
        else:
            state["units"]["U01"]["primary_duty"] += " Changed"
        previous["files"][name] = json_record(state)
        if bad == "hash":
            previous["files"][name]["sha256"] = "0" * 64
    with pytest.raises(ReplicaNativeError):
        validate_source(previous, approved, f"ws_test/replica/{run.document_id}/")


def test_explicit_retry_of_known_provider_failure_keeps_completed_artwork(tmp_path):
    from app.provider import ProviderOutcome

    run, provider, request, calls = fixture(tmp_path)
    edit = run.gateway.edit

    async def fail_second(request):
        if len(calls) == 1:
            calls.append(request)
            return result((1080, 1440), ProviderOutcome.FAILED)
        return await edit(request)

    run.gateway.edit = fail_second
    with pytest.raises(ReplicaNativeError):
        asyncio.run(deliver(provider, run, request))
    assert len(calls) == 2
    old_token = direct_state(run)["targets"][1]["request"]["token"]
    snap = asyncio.run(run.snapshot())
    rmtree(run.root)
    asyncio.run(run.restore(snap))
    # Same job cannot automatically dispatch a known failed request again.
    with pytest.raises(ReplicaNativeError):
        asyncio.run(deliver(provider, run, request))
    assert len(calls) == 2
    request.job_id = "job_explicit_retry"
    assert len(asyncio.run(deliver(provider, run, request))) == 3
    assert len(calls) == 4  # First known image retained; only two remaining new.
    assert read_json(run.root / "invocations.json")[old_token]["state"] == "rejected"
    assert direct_state(run)["targets"][1]["request"]["token"] != old_token


def test_preplanning_gate_is_required_and_corrupt_artwork_fails(tmp_path):
    run, provider, request, calls = fixture(tmp_path)
    state = run.state()
    state["plan_audit"]["pass"] = False
    write_json(run.run / "artifacts/workflow.json", state)
    with pytest.raises(ReplicaNativeError, match="生图前"):
        activate(run)
    assert not calls
    from app.replica_style_delivery import inspect_artwork

    file = run.root / "corrupt.png"
    file.write_bytes(b"invalid image")
    with pytest.raises(StyleDeliveryInvalid):
        inspect_artwork(run, str(file), state["layout"]["jobs"][0], False)


def test_exact_v5_binding_and_execution_paths_are_unchanged():
    import sys

    from app.replica_exact_compact import run_class

    legacy = run_class("exact", "exact-compact-v5")
    protocol_binding = sys.modules[legacy.__module__].protocol_binding
    from app.replica_native_provider import NativeReplicaProvider

    assert protocol_binding() == {
        "version": "exact-compact-v5",
        "content_sha256": (
            "82dddb7fffe4f733d3a5879c91ed8ba9f21dd40a4abaa3d05168c31f9ca8e438"
        ),
    }
    assert ReplicaDeliveryProvider._exact is NativeReplicaProvider._exact
    assert POLICY == "style-approved-plan-direct-delivery-v1"
    assert STATE_FILE != "direct-session.json"


def test_http_worker_retry_restores_style_without_replanning_or_repeat_images(
    client, monkeypatch, tmp_path
):
    from sqlalchemy import select

    from app.models import ReplicaDocumentRecord, UsageEventRecord
    from app.task_runner import MockTaskRunner

    from .test_api import dispatch_pending

    prepared, _, _, calls = fixture(tmp_path)
    gateway = prepared.gateway
    client.app.state.settings.reference_remix_provider_enabled = True
    client.app.state.replica_gateway = gateway
    client.app.state.task_runner = MockTaskRunner(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        reference_remix_provider_enabled=True,
        aurora_work_root=tmp_path / "worker",
    )
    ids = []
    for path in (prepared.root / "product.png", prepared.root / "reference.png"):
        uploaded = client.post(
            "/api/v1/workspaces/ws_test/assets",
            headers={"Idempotency-Key": str(uuid4())},
            files={"file": (path.name, path.read_bytes(), "image/png")},
        )
        assert uploaded.status_code == 201
        ids.append(uploaded.json()["id"])

    async def prepare(self, payload, records):
        assert set(records) == set(ids)
        self.root, self.run = prepared.root, prepared.run
        self.context = {
            **prepared.context,
            "facts": payload["facts"],
            "product_asset_ids": payload["product_asset_ids"],
            "references": payload["references"],
        }
        write_json(self.root / "context.json", self.context)
        return [
            {
                "reference": payload["references"][0],
                "analysis": {},
                "prompt": "Synthetic native plan",
                "unit_id": uid,
            }
            for uid in self.state()["layout"]["unit_ids"]
        ]

    monkeypatch.setattr(NativeReplicaRun, "prepare", prepare)
    original_save = ReplicaDeliveryProvider._save
    interrupted = False

    async def save(self, run, job_id, **kwargs):
        nonlocal interrupted
        await original_save(self, run, job_id, **kwargs)
        if (
            run.method == "style"
            and (run.root / STATE_FILE).exists()
            and direct_state(run)["targets"][0]["status"] == "GENERATED"
            and not interrupted
        ):
            interrupted = True
            raise RuntimeError("Synthetic process loss after durable first image")

    monkeypatch.setattr(ReplicaDeliveryProvider, "_save", save)
    route = "/api/v1/workspaces/ws_test/replica"
    created = client.post(
        route + "/sessions",
        json={
            "request_id": str(uuid4()),
            "replication_method": "style",
            "product_asset_ids": ids[:1],
            "references": [{"asset_id": ids[1]}],
            "facts": "Synthetic native plan",
            "auto_start_after_plan": True,
        },
    )
    assert created.status_code == 202, created.text
    document_id = created.json()["id"]
    doc = client.get(route + "/documents/" + document_id).json()
    job_id = doc["payload"]["job_ids"][0]
    dispatch_pending(client)
    failed = client.get(route + "/documents/" + document_id).json()
    assert failed["status"] == "failed", failed
    assert failed["payload"]["execution_resume_available"]
    assert len(calls) == 1
    with client.app.state.session_factory() as session:
        values = session.get(ReplicaDocumentRecord, document_id).payload
        source = deepcopy(values["native_executions"][job_id]["snapshot"])
        approved = deepcopy(values["native_snapshot"])
    rmtree(prepared.root)
    retry = client.post(
        route + f"/sessions/{document_id}/jobs/{job_id}/retry",
        json={"request_id": str(uuid4())},
    )
    assert retry.status_code == 202, retry.text
    dispatch_pending(client)
    final = client.get(route + "/documents/" + document_id).json()
    assert final["status"] == "completed", final
    assert len(calls) == 3  # First image retained, only two unfinished singles new.
    assert final["payload"]["execution_progress"]["generated"] == 3
    assert final["payload"]["execution_progress"]["reviewed"] == 0
    new_id = final["payload"]["job_ids"][0]
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{new_id}").json()
    assert job["succeeded_item_count"] == 3
    with client.app.state.session_factory() as session:
        values = session.get(ReplicaDocumentRecord, document_id).payload
        assert values["native_snapshot"] == approved
        assert values["native_executions"][job_id]["snapshot"] == source
        usage = session.scalars(
            select(UsageEventRecord).where(UsageEventRecord.job_id == new_id)
        ).all()
        assert len(usage) == 3
        assert all(
            u.raw_usage["native_visual_audited"] is False
            and u.raw_usage["review_status"] == "NOT_REVIEWED"
            for u in usage
        )
