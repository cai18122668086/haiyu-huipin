"""Original intake text and event identity remain authoritative on replay."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict

import pytest

from app.domestic_detail.facts import DetailIntakeError
from app.domestic_detail.intake import IntakeStore, intake_binding
from app.domestic_detail.plans import canonical_hash
from app.xinghe_agent import TengliOpenAICompatibleGateway
from tests.test_domestic_detail_planning_calls import Transport, request


@pytest.fixture
def original_intake(tmp_path, monkeypatch):
    monkeypatch.setattr("app.xinghe_agent._planner_prompt", lambda req: "SYNTHETIC")
    monkeypatch.setattr(
        "app.xinghe_agent._planner_response_format", lambda req: {"type": "json_object"}
    )
    transport = Transport()
    gateway = TengliOpenAICompatibleGateway(
        api_key="offline",
        base_url="https://memory.offline.invalid/",
        planner_model="selected-model",
        image_model="synthetic",
        transport=transport,
    )
    binding = intake_binding(
        settings={"synthetic": True},
        sources=[],
        skill_hash="a" * 64,
        model="selected-model",
        dispatch_route=gateway.native_planner_route(),
        resolution=None,
    )
    store = IntakeStore(tmp_path / "intake", binding, "job_original_intake")
    value = asdict(asyncio.run(gateway.plan(request("aurora_domestic_intake"))))
    value["provider_envelope"]["native_intake_receipt"] = {
        "version": "native-intake-call-v1",
        "binding_sha256": binding["sha256"],
        "request_file": "intake/" + store.path.name,
        "response_file": "intake/" + store.path.with_suffix(".response.json").name,
        "source_job_id": "job_original_intake",
    }
    store.begin("job_original_intake")
    return store, value, transport


def test_intake_original_replay_preserves_text_route_cost_and_event(original_intake):
    store, value, transport = original_intake
    store.finish(value)
    path = store.path.with_suffix(".response.json")
    original = path.read_bytes()
    assert canonical_hash(store.completed()) == canonical_hash(value)
    assert store.completed()["actual_cost_micros"] == 37
    assert len(transport.calls) == 1 and path.read_bytes() == original


@pytest.mark.parametrize(
    ("tamper", "code"),
    [
        ("payload", "INTAKE_RAW_REPLY_CHANGED"),
        ("raw_hash", "NATIVE_PLANNER_RAW_REPLY_CHANGED"),
        ("event_stamp", "INTAKE_RECEIPT_INVALID"),
        ("request_and_event_owner", "INTAKE_RECEIPT_INVALID"),
        ("response_hash", "INTAKE_RESPONSE_CHANGED"),
    ],
)
def test_intake_replay_rejects_changed_originals(original_intake, tamper, code):
    store, value, transport = original_intake
    store.finish(value)
    path = store.path.with_suffix(".response.json")
    saved = json.loads(path.read_bytes())
    if tamper == "payload":
        saved["result"]["payload"] = {"observed": False}
    elif tamper == "raw_hash":
        saved["result"]["audit_raw_content"] = '{"observed":false}'
    elif tamper == "event_stamp":
        saved["result"]["provider_envelope"]["native_intake_receipt"][
            "source_job_id"
        ] = "another-job"
    elif tamper == "request_and_event_owner":
        marker = json.loads(store.path.read_bytes())
        marker["job_id"] = "another-job"
        store.path.write_text(json.dumps(marker), encoding="utf-8")
        saved["result"]["provider_envelope"]["native_intake_receipt"][
            "source_job_id"
        ] = "another-job"
    else:
        saved["sha256"] = "b" * 64
    if tamper != "response_hash":
        saved["sha256"] = canonical_hash(saved["result"])
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises((DetailIntakeError, ValueError)) as error:
        store.completed()
    assert error.value.code == code
    assert len(transport.calls) == 1


def test_fresh_intake_mismatch_is_retained_before_rejection(original_intake):
    store, value, transport = original_intake
    changed = deepcopy(value)
    changed["payload"] = {"observed": False}
    with pytest.raises(DetailIntakeError) as error:
        store.finish(changed)
    assert error.value.code == "INTAKE_RAW_REPLY_CHANGED"
    original = json.loads(store.path.with_suffix(".response.json").read_bytes())
    assert (
        canonical_hash(original["result"]) == canonical_hash(changed)
        and original["result"]["actual_cost_micros"] == 37
    )
    assert len(transport.calls) == 1


def test_cross_runtime_intake_reuse_revalidates_immutable_evidence(original_intake):
    store, value, transport = original_intake
    store.finish(value)
    changed = {**store.binding, "intake_runtime_sha256": "new-code"}
    changed["sha256"] = canonical_hash(
        {k: v for k, v in changed.items() if k != "sha256"}
    )
    current = IntakeStore(store.root, changed, store.job_id)
    with pytest.raises(DetailIntakeError):
        current.completed()
    assert canonical_hash(current.recover_for_revalidation()) == canonical_hash(value)
    assert len(transport.calls) == 1
    for field in (
        "settings",
        "sources",
        "resolution",
        "dispatch_route",
        "skill_content_hash",
    ):
        different = {**changed, field: "changed-input"}
        different["sha256"] = canonical_hash(
            {k: v for k, v in different.items() if k != "sha256"}
        )
        assert (
            IntakeStore(store.root, different, store.job_id).recover_for_revalidation()
            is None
        )


def test_cross_runtime_intake_does_not_ignore_missing_original_reply(original_intake):
    store, _, _ = original_intake
    changed = {**store.binding, "intake_runtime_sha256": "new-code"}
    changed["sha256"] = canonical_hash(
        {k: v for k, v in changed.items() if k != "sha256"}
    )
    with pytest.raises(DetailIntakeError, match="Reconcile the original intake"):
        IntakeStore(store.root, changed, store.job_id).recover_for_revalidation()
