"""Recovery uses real hash-bound pixels and always requests a fresh audit."""

import asyncio
import json
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.provider import ProviderOutcome
from app.replica_native import ReplicaNativeError, digest, read_json, write_json
from app.replica_native_provider import (
    NativeReplicaProvider,
    ReplicaImageOutcomeUnknown,
)
from app.replica_retry import retry_artwork_candidates

from .test_replica_transport import run_at


def json_record(value):
    text = json.dumps(value)
    return {"text": text, "sha256": digest(text.encode())}


def snapshots(tmp_path):
    output = BytesIO()
    Image.new("RGB", (64, 80), "pink").save(output, "PNG")
    data = output.getvalue()
    sha = digest(data)
    module = {
        "id": "M001",
        "reference_unit_id": "R001",
        "reference_path": str(tmp_path / "reference.png"),
        "copy_units": [],
    }
    current_state = {
        "runtime_version": "6.0.0",
        "state": "READY",
        "plan_sha256": "a" * 64,
        "input": {"product_images": ["original"], "selling_points": ["Real facts"]},
        "plan": {"modules": [module]},
    }
    current = {
        "schema": "replica-native-v1",
        "method": "exact",
        "skill_id": "native",
        "source_hash": "b" * 64,
        "root": str(tmp_path),
        "files": {
            "session.json": json_record(current_state),
            "context.json": json_record({"user_facts": "Real facts"}),
        },
    }
    previous = deepcopy(current)
    state = deepcopy(current_state)
    state.update(
        state="SET_COMPLETE",
        modules=[
            {
                "id": "M001",
                "status": "REJECTED_BEST_EFFORT",
                "attempts": [
                    {
                        "artwork": str(tmp_path / "artwork/real.png"),
                        "judgment": {
                            "decision": "FAIL",
                            "derived_decision": "FAIL",
                            "artwork_sha256": sha,
                            "major_mismatches": ["Real eye defect"],
                        },
                    }
                ],
            }
        ],
    )
    previous["files"]["session.json"] = json_record(state)
    previous["files"]["artwork/real.png"] = {
        "sha256": sha,
        "object_key": f"ws_test/replica/rep_fixture/{sha}.png",
    }
    return previous, current, data


def candidates(previous, current):
    return retry_artwork_candidates(
        previous, current, "job_old", "ws_test/replica/rep_fixture/"
    )


def test_real_rejected_canvas_is_reusable_without_changing_its_failed_verdict(tmp_path):
    previous, current, data = snapshots(tmp_path)
    original = deepcopy(previous)
    result = candidates(previous, current)
    assert result["M001"].sha256 == digest(data)
    assert result["M001"].source_job_id == "job_old"
    assert previous == original
    assert (
        json.loads(previous["files"]["session.json"]["text"])["modules"][0]["attempts"][
            0
        ]["judgment"]["derived_decision"]
        == "FAIL"
    )


@pytest.mark.parametrize(
    "change", ["plan_hash", "plan_body", "input", "context", "root", "skill", "pending"]
)
def test_retry_never_borrows_pixels_from_a_different_or_unfinished_contract(
    tmp_path, change
):
    previous, current, _ = snapshots(tmp_path)
    state = json.loads(previous["files"]["session.json"]["text"])
    if change == "plan_hash":
        state["plan_sha256"] = "c" * 64
    elif change == "plan_body":
        state["plan"]["modules"][0]["copy_units"] = ["Different approved copy"]
    elif change == "input":
        state["input"]["selling_points"] = ["Different facts"]
    elif change == "context":
        previous["files"]["context.json"] = json_record({"user_facts": "Changed"})
    elif change == "root":
        previous["root"] = str(tmp_path / "another-case")
    elif change == "skill":
        previous["source_hash"] = "c" * 64
    else:
        state["state"] = "AWAITING_ARTWORK"
    previous["files"]["session.json"] = json_record(state)
    assert candidates(previous, current) == {}


@pytest.mark.parametrize(
    "change", ["text_hash", "artwork_hash", "workspace", "path_escape", "missing_audit"]
)
def test_corrupt_or_cross_workspace_retry_sources_stop_before_rendering(
    tmp_path, change
):
    previous, current, _ = snapshots(tmp_path)
    state = json.loads(previous["files"]["session.json"]["text"])
    if change == "text_hash":
        previous["files"]["context.json"]["sha256"] = "c" * 64
    elif change == "artwork_hash":
        previous["files"]["artwork/real.png"]["sha256"] = "c" * 64
    elif change == "workspace":
        previous["files"]["artwork/real.png"]["object_key"] = "another/secret.png"
    elif change == "path_escape":
        state["modules"][0]["attempts"][0]["artwork"] = str(
            tmp_path.parent / "secret.png"
        )
    else:
        state["modules"][0]["attempts"][0]["judgment"] = {}
    previous["files"]["session.json"] = json_record(state)
    with pytest.raises(ReplicaNativeError):
        candidates(previous, current)


class Storage:
    def __init__(self, data, events):
        self.data, self.events = data, events

    async def read_bytes(self, key):
        self.events.append("cache_read")
        return self.data


def render_fixture(tmp_path, *, altered=False):
    previous, current, data = snapshots(tmp_path)
    candidate = candidates(previous, current)["M001"]
    events = []

    async def edit(request):
        events.append("network")
        raise AssertionError("Recovery must not regenerate an unchanged initial canvas")

    run = run_at(tmp_path, "exact", SimpleNamespace(edit=edit))
    run.state = lambda: {"plan_sha256": "a" * 64}
    provider = NativeReplicaProvider(
        None, run.gateway, Storage(data + b"changed" if altered else data, events)
    )

    async def save(*args, **kwargs):
        events.append("save")

    provider._save = save
    request = SimpleNamespace(
        job_id="job_new",
        workspace_id="ws_test",
        job_item_id="item_new",
        attempt_id="attempt_new",
    )
    native = {"token": "fresh-native-token", "mode": "NEW", "module_id": "M001"}
    return run, provider, request, native, candidate, data, events


def test_retry_canvas_keeps_exact_bytes_and_zero_new_image_requests(tmp_path):
    run, provider, request, native, candidate, data, events = render_fixture(tmp_path)
    first = asyncio.run(
        provider._render(
            run, request, native, "Native prompt", [], [64, 80], "M001", candidate
        )
    )
    second = asyncio.run(
        provider._render(
            run, request, native, "Native prompt", [], [64, 80], "M001", candidate
        )
    )
    assert first == second and first.read_bytes() == data
    assert events == ["cache_read", "save"]
    assert run.usage[0]["network_requests"] == 0
    ledger = read_json(tmp_path / "invocations.json")[native["token"]]
    assert ledger["source_job_id"] == "job_old" and ledger["job_id"] == "job_new"


def test_retry_cannot_hide_changed_bytes_or_bypass_unknown_invocation_fence(tmp_path):
    run, provider, request, native, candidate, _, events = render_fixture(
        tmp_path, altered=True
    )
    with pytest.raises(ReplicaNativeError, match="哈希"):
        asyncio.run(
            provider._render(
                run, request, native, "Native prompt", [], [64, 80], "M001", candidate
            )
        )
    assert "network" not in events
    write_json(tmp_path / "invocations.json", {native["token"]: {"state": "invoked"}})
    events.clear()
    with pytest.raises(ReplicaImageOutcomeUnknown):
        asyncio.run(
            provider._render(
                run, request, native, "Native prompt", [], [64, 80], "M001", candidate
            )
        )
    assert events == []


def test_native_repair_invokes_image_editor_instead_of_returning_retry_canvas(tmp_path):
    run, provider, request, native, candidate, data, events = render_fixture(tmp_path)
    draft, reference = tmp_path / "draft.png", tmp_path / "reference.png"
    draft.write_bytes(data)
    Image.new("RGB", (64, 80), "blue").save(reference)

    async def edit(request):
        events.append("network")
        return SimpleNamespace(
            outcome=ProviderOutcome.SUCCEEDED,
            artifact=SimpleNamespace(payload=data, locally_resampled=False),
            normalized_usage={"reported": False},
            actual_cost_micros=None,
            estimated_cost_micros=None,
            http_status=200,
            error_code=None,
            raw_usage={},
        )

    run.gateway.edit = edit
    asyncio.run(
        provider._render(
            run,
            request,
            {**native, "mode": "REPAIR"},
            "Reference image 1 is the binding composition blueprint;",
            [draft, reference],
            [64, 80],
            "M001",
            candidate,
        )
    )
    assert events.count("network") == 1 and "cache_read" not in events
    assert run.usage[-1]["network_requests"] == 1


def test_reused_canvas_still_enters_the_fresh_pixel_audit_before_any_acceptance(
    tmp_path,
):
    run, provider, request, native, candidate, _, events = render_fixture(tmp_path)
    reference = tmp_path / "reference.png"
    Image.new("RGB", (64, 80), "blue").save(reference)
    module = {
        "id": "M001",
        "reference_path": str(reference),
        "copy_units": [],
        "output_canvas": {"width": 64, "height": 80},
    }
    state = {
        "state": "AWAITING_ARTWORK",
        "module_index": 0,
        "plan_sha256": "a" * 64,
        "input": {"product_geometry": []},
        "plan": {"modules": [module]},
        "modules": [{"id": "M001", "attempts": [{}]}],
    }
    run.state = lambda: state
    run.context = {"user_facts": "Real facts"}
    write_json(
        tmp_path / "request.json",
        {
            **native,
            "canvas": {"width": 64, "height": 80},
            "prompt": "Native prompt",
            "reference_images": [str(reference)],
        },
    )

    def cli(command, **kwargs):
        assert command == "record-artwork"
        events.append("record-artwork")
        state["modules"][0]["attempts"][0]["artwork"] = str(kwargs["image"])
        state["state"] = "AWAITING_AUDIT"

    class FreshAuditObserved(Exception):
        pass

    async def audit(stage, instruction, context, *args, **kwargs):
        assert stage.startswith("exact-pixel-audit-")
        assert context["artwork_sha256"] == candidate.sha256
        assert "previous_decision" not in context
        assert context["prior_pixel_reviews"] == [
            {
                "source_job_id": "job_old",
                "artwork_sha256": candidate.sha256,
                "observations": ["Real eye defect"],
            }
        ]
        events.append("fresh-audit")
        raise FreshAuditObserved()

    run.cli, run.contract = cli, audit
    with pytest.raises(FreshAuditObserved):
        asyncio.run(provider._exact(run, request, {"M001": candidate}))
    assert events == ["cache_read", "save", "record-artwork", "save", "fresh-audit"]
    assert state["state"] == "AWAITING_AUDIT"


@pytest.mark.parametrize(
    "bad", [None, "completed", "active", "workspace", "plan", "event"]
)
def test_reuse_requires_an_explicit_same_workspace_failed_parent_job(tmp_path, bad):
    previous, current, _ = snapshots(tmp_path)
    config = {
        "replica_session_id": "rep_fixture",
        "settings": {"replica_plan_hash": "bound-plan"},
    }
    job = SimpleNamespace(
        id="job_new",
        workspace_id="ws_test",
        retry_of_job_id="job_old",
        configuration=deepcopy(config),
    )
    parent = SimpleNamespace(
        id="job_old",
        workspace_id="ws_test",
        status="failed",
        configuration=deepcopy(config),
    )
    values = {
        "plan_hash": "bound-plan",
        "retry_events": [{"from": "job_old", "to": "job_new"}],
        "native_executions": {
            "job_old": {"state": "SET_COMPLETE", "snapshot": previous}
        },
    }
    if bad is None:
        values["native_executions"]["job_ancestor"] = {
            "state": "SET_COMPLETE", "snapshot": deepcopy(previous)
        }
        state = json.loads(previous["files"]["session.json"]["text"])
        state["modules"][0]["status"] = "COMPLETE"
        state["modules"][0]["attempts"][0]["judgment"].update(
            decision="PASS", derived_decision="PASS", major_mismatches=[]
        )
        previous["files"]["session.json"] = json_record(state)
        values["retry_events"].append({"from": "job_ancestor", "to": "job_old"})
    if bad == "completed":
        parent.status = "completed"
    elif bad == "active":
        parent.status = "processing"
    elif bad == "workspace":
        parent.workspace_id = "another-workspace"
    elif bad == "plan":
        parent.configuration["settings"]["replica_plan_hash"] = "another-plan"
    elif bad == "event":
        values["retry_events"] = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, model, identity):
            return {"job_new": job, "job_old": parent}.get(identity)

    provider = NativeReplicaProvider(Session, None, None)
    result = provider._retry_candidates(
        values, SimpleNamespace(job_id="job_new", workspace_id="ws_test"), current
    )
    assert bool(result) is (bad is None)
    if bad is None:
        assert result["M001"].prior_reviews[0].source_job_id == "job_ancestor"
