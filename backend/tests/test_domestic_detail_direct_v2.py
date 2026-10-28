"""The small website brief reaches the existing native generation/delivery path."""

import json
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.aurora_detail_runtime import detail_workspace
from app.domestic_detail import direct
from app.domestic_detail.plans import resolve_native_plan
from app.xinghe_agent import validate_editable_plan_payload
from tests.test_api import WORKSPACE_ID, idem
from tests.test_domestic_detail_execution import _gateway
from tests.test_domestic_detail_website_v1 import (
    explicit_synthetic_storage,  # noqa: F401
    generate,
    planned,
)


def test_direct_brief_compiles_once_and_persists_four_native_pairs(client):
    job = planned(client, profile=direct.VERSION)
    calls = _gateway(client).planner_calls
    assert len(calls) == 2
    assert [call.authoring_stage for call in calls] == ["", "direct"]
    assert resolve_native_plan(job["plan"]["payload"])["plan_format"] == direct.VERSION
    assert validate_editable_plan_payload(job["plan"]["payload"], 8) == []
    current = generate(client, job)
    assert current["status"] == "awaiting_final_review", current
    assert len(_gateway(client).image_calls) == 4
    root = detail_workspace(job["id"])
    state = json.loads((root / "session.json").read_bytes())
    assert state["plan"]["planning_mode"] == direct.VERSION
    assert len(state["modules"]) == 8
    assert all(module["accepted_asset"] for module in state["modules"])
    assert all(len(module["attempts"]) == 1 for module in state["modules"])
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/detail-final-review"
    )
    assert response.status_code == 200, response.text
    assert len(current["assets"]) > 8
    final = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/finalize",
        headers=idem(),
        json={
            "approval_text": (
                "我已逐屏审阅本次合成任务的最终结果，并接受明确列出的残差。"
            ),
            "final_review_sha256": response.json()["sha256"],
        },
    )
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "completed"
    archive = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/deliverables.zip"
    )
    assert archive.status_code == 200, archive.text
    with ZipFile(BytesIO(archive.content)) as package:
        assert (
            len([name for name in package.namelist() if name.startswith("screens/")])
            == 8
        )
    assert len(_gateway(client).image_calls) == 4


def test_direct_brief_cannot_add_fact_ids_or_silently_drop_user_points(client):
    job = planned(client, profile=direct.VERSION)
    request = _gateway(client).planner_calls[-1]
    value = deepcopy(resolve_native_plan(job["plan"]["payload"])["website_brief"])
    value["screens"][0]["fact_ids"] = ["INVENTED"]
    assert direct.errors(value, request)
    value = deepcopy(resolve_native_plan(job["plan"]["payload"])["website_brief"])
    first = direct.facts_for(request)[1][0]
    for screen in value["screens"]:
        screen["fact_ids"] = [first]
    assert any("coverage" in message for message in direct.errors(value, request))


def test_direct_retry_uses_original_brief_instead_of_invalid_legacy_candidate(client):
    from sqlalchemy import select

    from app.models import JobItemRecord, JobPlanRecord, JobRecord, utc_now
    from app.services import canonical_hash
    from tests.test_api import dispatch_pending

    first = planned(client, profile=direct.VERSION)
    gateway = _gateway(client)
    with client.app.state.session_factory() as db:
        record = db.get(JobRecord, first["id"])
        record.status, record.stage = "failed", "planning_failed"
        record.failed_item_count, record.finished_at = 8, utc_now()
        plan = db.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == first["id"])
        )
        candidate = deepcopy(resolve_native_plan(plan.payload))
        candidate["modules"][0]["expression"]["evidence_subject_policy"] = (
            "UNKNOWN_EVIDENCE_POLICY"
        )
        data = {**plan.payload, "state": "failed", "rejected_aurora_plan": candidate}
        plan.payload, plan.payload_hash = data, canonical_hash(data)
        plan.validation_status = "invalid"
        for item in db.scalars(
            select(JobItemRecord).where(JobItemRecord.job_id == record.id)
        ):
            item.status, item.stage = "failed", "planning_failed"
        db.commit()
    before = len(gateway.planner_calls)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{first['id']}/retry",
        headers=idem(), json={"scope": "failed"},
    )
    assert response.status_code == 201, response.text
    target_id = response.json()["id"]
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target_id}").json()
    assert result["status"] == "awaiting_confirmation", result
    assert (
        resolve_native_plan(result["plan"]["payload"])["plan_format"] == direct.VERSION
    )
    assert "planning_recovery" not in result["plan"]["payload"]
    assert len(gateway.planner_calls) == before
    assert not gateway.image_calls


def test_confirmed_history_keeps_human_evidence_and_rejects_changed_truth():
    from app.domestic_detail.facts import DetailIntakeError
    from app.domestic_detail.intake import preserve_confirmed_history

    conflict = {
        "id": "C01",
        "topic": "颜色",
        "alternative_fact_ids": ["F01", "O01"],
        "why_material": "原始颜色冲突说明",
        "status": "UNRESOLVED",
    }
    prior = {
        "source_conflicts": [conflict],
        "observed_facts": [{"id": "O01", "text": "近黑鞋面"}],
    }
    resolution = {
        "confirmation_text": "确认深棕色",
        "source_intake": prior,
        "resolutions": [
            {
                "conflict_id": "C01",
                "retained_fact_ids": ["F01"],
                "rejected_fact_ids": ["O01"],
            }
        ],
    }
    reply = deepcopy(prior)
    reply["source_conflicts"][0].update(
        why_material="模型改写了历史说明",
        resolution={
            "retained_fact_ids": ["F01"],
            "rejected_fact_ids": ["O01"],
            "confirmation_text": "确认深棕色",
        },
    )
    raw = deepcopy(reply)
    settings = {"website_contract_version": direct.VERSION}
    canonical, receipt = preserve_confirmed_history(reply, settings, resolution)
    assert canonical == prior
    assert reply == raw
    assert receipt["version"] == "website-confirmed-history-v1"
    assert preserve_confirmed_history(reply, {}, resolution) == (raw, None)
    for field in ("fact", "alternative", "choice"):
        changed = deepcopy(reply)
        if field == "fact":
            changed["observed_facts"][0]["text"] = "模型换成红色鞋面"
        elif field == "alternative":
            changed["source_conflicts"][0]["alternative_fact_ids"] = ["F01", "O02"]
        else:
            changed["source_conflicts"][0]["resolution"]["retained_fact_ids"] = ["O01"]
        with pytest.raises(DetailIntakeError, match="changed"):
            preserve_confirmed_history(changed, settings, resolution)


def test_adjacent_edges_map_to_actual_pair_seams_without_rewriting(client):
    from dataclasses import replace

    planned(client, profile=direct.VERSION)
    original_request = _gateway(client).planner_calls[-1]
    for count in (8, 10, 12):
        request = replace(original_request, page_count=count)
        brief = direct.mock_brief(request)
        pair_order = deepcopy(brief)
        assert direct.errors(pair_order, request) == []
        edges = [f"屏幕 {i + 1} 到 {i + 2} 的背景衔接" for i in range(count - 1)]
        brief["pair_transitions"] = edges
        assert direct.errors(brief, request) == []
        plan = direct.build(brief, request)
        assert brief["pair_transitions"] == edges
        pairs = plan["output_strategy"]["continuous_pair_rendering"]["pair_ids"]
        assert [pair["internal_seam_carrier"] for pair in pairs] == edges[::2]
        direct.validate_plan(plan, count)
        brief["pair_transitions"] = edges[:-1]
        assert direct.errors(brief, request)


def test_direct_input_preserves_observations_without_legacy_operating_gate(client):
    from app.aurora_detail_runtime import build_native_input

    planned(client, profile=direct.VERSION)
    request = _gateway(client).planner_calls[-1]
    intake = deepcopy(request.detail_intake)
    intake["operating_analysis"] = {"retained_original_legacy_ledger": True}
    originals = deepcopy(intake)
    sources = [
        {"id": source.asset_id, "role": source.source_role, "sha256": source.sha256}
        for source in request.sources
    ]
    paths = [f"/original/{index}.png" for index, _ in enumerate(sources)]
    value = build_native_input(
        request.intake_settings,
        product_images=sources,
        image_paths=paths,
        intake=intake,
    )
    assert "operating_analysis" not in value
    assert value["identity_observations"] == intake["identity_observations"]
    assert value["website_contract_version"] == direct.VERSION
    assert intake == originals
    assert value["verified_facts"] == [*intake["user_facts"], *intake["observed_facts"]]
    legacy = dict(request.intake_settings)
    legacy.pop("website_contract_version")
    historical = build_native_input(
        legacy,
        product_images=sources,
        image_paths=paths,
        intake=intake,
    )
    assert historical["operating_analysis"] == intake["operating_analysis"]
    assert "website_contract_version" not in historical


def test_pair_and_identity_review_transport_has_supported_object_root():
    from types import SimpleNamespace

    from app.xinghe_agent import _planner_response_format

    for profile in ("aurora_detail_pair_audit", "aurora_detail_identity_audit"):
        request = SimpleNamespace(
            authoring_stage="",
            planning_profile=profile,
            product_information=(json.dumps({"review_profile": "WEBSITE_LEAN_V1"}),),
        )
        schema = _planner_response_format(request)["json_schema"]["schema"]
        assert schema["type"] == "object"
        assert schema["properties"]
        assert not set(schema) & {"allOf", "anyOf", "oneOf", "enum", "const", "not"}


def test_explicit_retry_keeps_original_authority_but_rejects_changed_scope(tmp_path):
    from app.domestic_detail.generation import original_retry_scope
    from app.domestic_detail.plans import canonical_hash

    original = {
        "runtime_source_sha256": "a" * 64,
        "source_assets": ["original"],
        "normalized_plan_sha256": "p" * 64,
    }
    original["sha256"] = canonical_hash(original)
    (tmp_path / "generation-binding.json").write_text(json.dumps(original))
    current = {**original, "runtime_source_sha256": "b" * 64}
    current["sha256"] = canonical_hash(
        {k: v for k, v in current.items() if k != "sha256"}
    )
    assert original_retry_scope(current, tmp_path) == original
    changed = {**current, "source_assets": ["changed"]}
    assert original_retry_scope(changed, tmp_path) == changed
    assert json.loads((tmp_path / "generation-binding.json").read_bytes()) == original


@pytest.mark.parametrize("lost_object_upload", [False, True])
def test_direct_failed_retry_reuses_prefix_after_review_adapter_update(
    client, monkeypatch, lost_object_upload
):
    from sqlalchemy import select

    from app.domestic_detail import generation
    from app.models import JobItemRecord, JobRecord, utc_now
    from tests.test_api import dispatch_pending

    job = planned(client, profile=direct.VERSION)
    source = generate(client, job)
    root = detail_workspace(job["id"])
    checkpoint_path = root / "session.json"
    checkpoint = json.loads(checkpoint_path.read_bytes())
    checkpoint.update(state="AWAITING_GENERATED_MODULE", current_index=2)
    for module in checkpoint["modules"][2:4]:
        module.update(
            status="AWAITING_GENERATION",
            accepted_asset=None,
            repair_plan={
                "material_failures": [{"visible_evidence": "修正原画布接缝"}],
                "expected_visible_change": "保留商品并修正接缝",
            },
        )
    for module in checkpoint["modules"][4:]:
        module.update(
            status="PENDING", accepted_asset=None, attempts=[], cycle_attempt_count=0
        )
    if lost_object_upload:
        original = json.loads(checkpoint_path.read_bytes())
        checkpoint.update(state="AUTHORIZED", current_index=4)
        checkpoint["modules"][2:4] = original["modules"][2:4]
        storage = client.app.state.storage
        from app.xinghe_agent import XingheImageProvider

        key = XingheImageProvider._aurora_pair_canvas_object_key(
            WORKSPACE_ID, job["id"], _gateway(client).image_calls[0].group_key, "P02"
        )
        original_object = storage.root / key
        assert original_object.is_file()
        original_object.unlink()
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf8")
    from app.xinghe_agent import XingheImageProvider

    XingheImageProvider._reset_native_detail_generation(checkpoint_path)
    checkpoint = json.loads(checkpoint_path.read_bytes())
    assert [m["status"] for m in checkpoint["modules"][2:4]] == (
        ["COMPLETE"] if lost_object_upload else ["REPAIR_PREPARED"]
    ) * 2
    with client.app.state.session_factory() as db:
        record = db.get(JobRecord, job["id"])
        record.status, record.stage = "partial", "partial"
        record.succeeded_item_count, record.failed_item_count = 2, 1
        record.finished_at = utc_now()
        items = db.scalars(
            select(JobItemRecord)
            .where(JobItemRecord.job_id == job["id"])
            .order_by(JobItemRecord.ordinal)
        ).all()
        for index, item in enumerate(items[2:], 2):
            item.status = "failed" if index == 2 else "skipped"
            item.error_code = (
                "AURORA_NATIVE_AUDIT_INVALID"
                if index == 2
                else "AURORA_DETAIL_SEQUENCE_HALTED"
            )
        db.commit()
    before = len(_gateway(client).image_calls)
    original_scope = json.loads((root / "generation-binding.json").read_bytes())
    monkeypatch.setattr(generation, "runtime_source_hash", lambda: "b" * 64)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source['id']}/retry",
        headers=idem(),
        json={"scope": "failed"},
    )
    assert response.status_code == 201, response.text
    target = response.json()
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target['id']}").json()
    assert result["status"] == "awaiting_final_review", result
    if lost_object_upload:
        assert original_object.is_file()
    assert [item["status"] for item in result["items"]] == ["reused"] * 2 + [
        "succeeded"
    ] * 6
    assert len(_gateway(client).image_calls) - before == (
        2 if lost_object_upload else 3
    )
    edit_sources = [
        source
        for source in _gateway(client).image_calls[before].sources
        if source.source_role == "generated_edit_canvas"
    ]
    if lost_object_upload:
        assert not edit_sources
    else:
        assert len(edit_sources) == 1
        assert (
            edit_sources[0].sha256
            == checkpoint["modules"][2]["attempts"][0]["normalized_image_sha256"]
        )
    original_ids = {
        source.asset_id
        for source in _gateway(client).image_calls[0].sources
        if source.source_role == "product_reference"
    }
    for call in _gateway(client).image_calls[before:]:
        assert {
            source.asset_id
            for source in call.sources
            if source.source_role == "product_reference"
        } == original_ids
    target_root = detail_workspace(target["id"])
    assert (
        json.loads((target_root / "generation-binding.json").read_bytes())
        == original_scope
    )
    for path in (target_root / "generation-calls").glob("*.json"):
        if not path.name.endswith(".completion.json"):
            assert (
                json.loads(path.read_bytes())["execution_runtime_source_sha256"]
                == "b" * 64
            )
    assert json.loads(checkpoint_path.read_bytes()) == checkpoint
    review = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target['id']}/detail-final-review"
    )
    assert review.status_code == 200, review.text
    final = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target['id']}/finalize",
        headers=idem(),
        json={
            "approval_text": "我已审阅恢复后的八张合成图片并确认交付",
            "final_review_sha256": review.json()["sha256"],
        },
    )
    assert final.status_code == 200, final.text
    archive = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{target['id']}/deliverables.zip"
    )
    assert archive.status_code == 200, archive.text
    with ZipFile(BytesIO(archive.content)) as package:
        assert (
            len([name for name in package.namelist() if name.startswith("screens/")])
            == 8
        )
    assert len(_gateway(client).image_calls) - before == (
        2 if lost_object_upload else 3
    )


def test_checkpoint_pixel_origins_follow_multiple_explicit_retries(tmp_path):
    from app.domestic_detail.generation import (
        DetailGenerationError,
        checkpoint_workspaces,
    )
    from app.domestic_detail.plans import canonical_hash

    binding = {"runtime_source_sha256": "a" * 64}
    binding["sha256"] = canonical_hash(binding)
    roots = [tmp_path / name for name in ("job_current", "job_parent", "job_original")]
    for index, root in enumerate(roots):
        root.mkdir()
        (root / "generation-binding.json").write_text(json.dumps(binding))
        state = {"checkpoint_source_job_id": roots[index + 1].name} if index < 2 else {}
        (root / "session.json").write_text(json.dumps(state))
    assert checkpoint_workspaces(roots[0], binding) == roots
    (roots[-1] / "session.json").write_text(
        json.dumps({"checkpoint_source_job_id": roots[0].name})
    )
    with pytest.raises(DetailGenerationError, match="cycle"):
        checkpoint_workspaces(roots[0], binding)
