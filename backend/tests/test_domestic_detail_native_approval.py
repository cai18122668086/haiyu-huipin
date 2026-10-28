from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
from app.domestic_detail.native_approval import native_approval_directory
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_execution import _aurora_approval_payload, _gateway
from tests.test_domestic_detail_plans import planned_job


def test_complete_native_document_is_persisted_shown_and_authorized_exactly(
    client, monkeypatch
):
    planned = planned_job(client)
    payload = planned["plan"]["payload"]
    native = payload["native_approval"]
    document = native["document_markdown"].encode("utf-8")
    assert hashlib.sha256(document).hexdigest() == native["document_sha256"]
    assert (
        native["document_markdown"]
        in payload["aurora_v4_3"]["approval_document_markdown"]
    )
    root = detail_workspace(planned["id"])
    assert (
        native_approval_directory(root, native) / "campaign-approval.md"
    ).read_bytes() == document
    assert _gateway(client).image_calls == []
    authorizations = []
    original = AuroraDetailHarnessRunner.run

    def observe(self, command, workspace, arguments):
        if command == "authorize":
            authorizations.append(dict(arguments))
            assert (workspace / "campaign-approval.md").read_bytes() == document
            assert arguments["approval-document-sha256"] == native["document_sha256"]
        return original(self, command, workspace, arguments)

    monkeypatch.setattr(AuroraDetailHarnessRunner, "run", observe)
    gateway = _gateway(client)
    original_edit = gateway.edit

    async def verify_model_request(request):
        signed = json.loads(
            (root / f"generation-{request.page_number:03d}.json").read_text(
                encoding="utf-8"
            )
        )
        assert request.prompt == signed["prompt"]
        assert request.single_dispatch is True
        return await original_edit(request)

    monkeypatch.setattr(gateway, "edit", verify_model_request)
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}"
    ).json()
    assert current["status"] == "awaiting_final_review", current
    assert len(authorizations) == 1 and len(_gateway(client).image_calls) == 4


@pytest.mark.parametrize(
    "artifact",
    [
        "input",
        "identity",
        "normalized_plan",
        "compiled_plan",
        "compiled_input",
        "compiled_identity",
        "document",
        "source_pixels",
    ],
)
def test_changed_native_artifact_cannot_be_confirmed_or_recreated(client, artifact):
    planned = planned_job(client)
    payload = planned["plan"]["payload"]
    native = payload["native_approval"]
    root = native_approval_directory(detail_workspace(planned["id"]), native)
    if artifact == "source_pixels":
        saved_input = json.loads((root / "input.json").read_text(encoding="utf-8"))
        target = Path(saved_input["product_images"][0]["path"])
    else:
        target = root / (
            "campaign-approval.md" if artifact == "document" else artifact + ".json"
        )
    target.write_bytes(b"changed after the user-visible compiler approval")
    before = target.read_bytes()
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert (
        response.status_code == 409
        and "AURORA_APPROVAL_BINDING_INVALID" in response.text
    )
    assert target.read_bytes() == before
    assert _gateway(client).image_calls == []
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}"
    ).json()
    assert current["status"] == "awaiting_confirmation"
    assert current["plan"]["confirmed_at"] is None


@pytest.mark.parametrize(
    "mutation",
    ["compiled_plan", "compiled_input", "compiled_identity", "input_file", "document"],
)
def test_generation_recompile_cannot_authorize_an_unseen_contract(
    client, monkeypatch, mutation
):
    planned = planned_job(client)
    root = detail_workspace(planned["id"])
    original = AuroraDetailHarnessRunner.run
    authorizations = []

    def alter_compile(self, command, workspace, arguments):
        result = original(self, command, workspace, arguments)
        if command == "authorize":
            authorizations.append(arguments)
        if command == "compile-plan" and workspace.resolve() == root.resolve():
            if mutation == "document":
                (root / "campaign-approval.md").write_bytes(b"unseen native document")
            elif mutation == "input_file":
                (root / "input.json").write_bytes(b"unseen native input file")
            else:
                session_path = root / "session.json"
                value = json.loads(session_path.read_text(encoding="utf-8"))
                if mutation == "compiled_plan":
                    value["plan"]["campaign_thesis"] = (
                        "A different unapproved campaign thesis"
                    )
                elif mutation == "compiled_input":
                    value["input"]["product_name"] = "A different unapproved product"
                else:
                    value["identity"]["canonical_name"] = (
                        "A different unapproved identity"
                    )
                session_path.write_text(
                    json.dumps(value, ensure_ascii=False), encoding="utf-8"
                )
        return result

    monkeypatch.setattr(AuroraDetailHarnessRunner, "run", alter_compile)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}"
    ).json()
    assert current["status"] in {"failed", "partial"}, current
    assert authorizations == [] and _gateway(client).image_calls == []


def test_edited_native_plan_requires_new_approval_without_model_calls(
    client,
):
    planned = planned_job(client)
    payload = deepcopy(planned["plan"]["payload"])
    thesis = "修改后的真实商品购买叙事，保留原始来源并重新审阅完整方案。"

    def edit_views(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "aurora_plan":
                    item["campaign_thesis"] = thesis
                else:
                    edit_views(item)
        elif isinstance(value, list):
            for item in value:
                edit_views(item)

    edit_views(payload)
    calls = len(_gateway(client).planner_calls)
    response = client.put(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan",
        json={"expected_revision": planned["plan"]["revision"], "payload": payload},
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["validation_status"] == "valid", edited["validation_errors"]
    assert edited["revision"] == planned["plan"]["revision"] + 1
    assert edited["confirmed_at"] is None
    assert (
        edited["payload"]["native_approval"]["document_sha256"]
        != payload["native_approval"]["document_sha256"]
    )
    assert thesis in edited["payload"]["native_approval"]["document_markdown"]
    assert (
        len(_gateway(client).planner_calls) == calls
        and _gateway(client).image_calls == []
    )
    stale = _aurora_approval_payload(planned)
    stale["expected_revision"] = edited["revision"]
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=stale,
    )
    assert response.status_code == 409
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}"
    ).json()
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(current),
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    assert len(_gateway(client).image_calls) == 4


@pytest.mark.parametrize(
    "missing", ["both", "protocol", "native_sha", "wrong_native_sha"]
)
def test_legacy_or_incomplete_client_cannot_authorize_native_plan(client, missing):
    planned = planned_job(client)
    packet = _aurora_approval_payload(planned)
    if missing in {"both", "protocol"}:
        packet.pop("approval_protocol")
    if missing in {"both", "native_sha"}:
        packet.pop("native_approval_document_sha256")
    if missing == "wrong_native_sha":
        packet["native_approval_document_sha256"] = "0" * 64
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=packet,
    )
    assert (
        response.status_code == 409
        and "AURORA_NATIVE_APPROVAL_REQUIRED" in response.text
    )
    assert _gateway(client).image_calls == []
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}"
    ).json()
    assert (
        current["plan"]["confirmed_at"] is None
        and current["status"] == "awaiting_confirmation"
    )


def test_invalid_edit_clears_stale_document_and_cannot_be_confirmed(client):
    planned = planned_job(client)
    payload = deepcopy(planned["plan"]["payload"])

    def break_module_ids(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "aurora_plan":
                    item["modules"][0]["id"] = "INVALID_MODULE_ID"
                else:
                    break_module_ids(item)
        elif isinstance(value, list):
            for item in value:
                break_module_ids(item)

    break_module_ids(payload)
    calls = len(_gateway(client).planner_calls)
    response = client.put(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan",
        json={"expected_revision": planned["plan"]["revision"], "payload": payload},
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["validation_status"] == "invalid"
    assert edited["payload"]["aurora_v4_3"]["approval_document_markdown"] == ""
    assert edited["payload"]["aurora_v4_3"]["approval_document_sha256"] is None
    stale = _aurora_approval_payload(planned)
    stale["expected_revision"] = edited["revision"]
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=stale,
    )
    assert response.status_code == 409
    assert (
        len(_gateway(client).planner_calls) == calls
        and _gateway(client).image_calls == []
    )
