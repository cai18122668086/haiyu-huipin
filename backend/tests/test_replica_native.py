"""Native guards use synthetic pixels here; these tests do not assert visual quality."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.new_detail_skill_provider import BundledSkillManifestValidator
from app.replica_native import (
    NativeReplicaRun,
    ReplicaNativeError,
    ReplicaPlanAuditRejected,
    contained,
    digest,
    native_accounting,
    write_json,
)
from app.replica_native_provider import NativeReplicaProvider

from .test_replica import upload


def test_unknown_native_cost_keeps_tokens_and_conservative_quota(tmp_path):
    from app.models import JobRecord, UsageEventRecord
    from app.provider_quota_ledger import _usage_charge

    run = run_for(tmp_path)
    run.record_usage(
        SimpleNamespace(
            normalized_usage={
                "reported": True,
                "input_tokens": 100,
                "output_tokens": 200,
            },
            actual_cost_micros=None,
            estimated_cost_micros=5,
        ),
        "image",
        3,
    )
    accounting = native_accounting(run.usage)
    assert accounting["actual_cost_micros"] is None
    assert accounting["input_tokens"] == 100 and accounting["output_tokens"] == 200
    assert accounting["network_requests"] == 3
    usage = UsageEventRecord(
        actual_cost_micros=None,
        estimated_cost_micros=accounting["estimated_cost_micros"],
        raw_usage=accounting,
    )
    charge, basis = _usage_charge(JobRecord(type="reference_remix"), usage, 3)
    assert charge == 15 and basis == "estimated_fallback"


def test_native_cost_aggregation_distinguishes_cache_and_incomplete_legacy_usage():
    assert native_accounting([])["actual_cost_micros"] == 0
    assert native_accounting([])["network_requests"] == 0
    assert native_accounting([{"reported": True}])["actual_cost_micros"] is None
    rows = [
        {
            "reported": True,
            "actual_cost_micros": 4,
            "estimated_cost_micros": 6,
            "input_tokens": 2,
            "output_tokens": 3,
        }
    ] * 2
    assert native_accounting(rows)["actual_cost_micros"] == 8
    assert native_accounting(rows)["input_tokens"] == 4


def run_for(tmp_path, method="exact", gateway=None):
    return NativeReplicaRun(
        method, "rep_" + uuid4().hex, "ws_test", gateway, None, root=tmp_path
    )


@pytest.mark.parametrize(
    "skill", ["aurora-ecommerce-reference-remix", "all-category-style-benchmark-still"]
)
def test_both_complete_sources_are_locked(skill):
    assert len(BundledSkillManifestValidator().validate(skill)) == 64


def test_native_commands_and_paths_are_bounded(tmp_path):
    run = run_for(tmp_path)
    with pytest.raises(ReplicaNativeError):
        run.cli("execute", script="arbitrary.py")
    with pytest.raises(ReplicaNativeError):
        run.cli(
            "start",
            input=tmp_path.parent / "outside.json",
            session=tmp_path / "session.json",
        )
    with pytest.raises(ReplicaNativeError):
        contained(tmp_path, "../outside.json")


def test_a_real_plan_rejection_is_not_retried_as_a_format_error(tmp_path):
    run = run_for(tmp_path, "style")
    calls = []

    async def ask(*args, **kwargs):
        calls.append("fresh_review")
        return {
            "pass": False,
            "review_kind": "visual_model",
            "evidence": ["actual product geometry mismatch"],
        }

    def blocked(*args, **kwargs):
        raise ReplicaNativeError("plan audit requires explicit passing review")

    run.ask, run.cli = ask, blocked
    with pytest.raises(ReplicaPlanAuditRejected) as error:
        asyncio.run(
            run.contract("fixture-plan-audit", "audit", {}, "audit-plan", "audit")
        )
    assert error.value.judgment["pass"] is False
    assert calls == ["fresh_review"]


def test_snapshot_preserves_native_bytes_and_rejects_tampering(tmp_path):
    from app.storage import ImageValidator, LocalImageStorage

    run = run_for(tmp_path / "run")
    run.storage = LocalImageStorage(
        tmp_path / "private-objects", ImageValidator(1_000_000, 1_000_000)
    )
    path = run.root / "inputs" / "product-00.img"
    path.parent.mkdir()
    Image.new("RGB", (64, 64), "pink").save(path, "PNG")
    write_json(run.root / "context.json", {"fixture": "structural snapshot test"})
    before = path.read_bytes()
    snapshot = asyncio.run(run.snapshot())
    assert snapshot["files"]["inputs/product-00.img"]["object_key"].endswith(".png")
    path.unlink()
    asyncio.run(run.restore(snapshot))
    assert path.read_bytes() == before
    snapshot["files"]["context.json"]["sha256"] = "0" * 64
    with pytest.raises(ReplicaNativeError, match="哈希"):
        asyncio.run(run.restore(snapshot))


def test_exact_native_guard_requires_plan_audit_and_one_active_token(tmp_path):
    run = run_for(tmp_path)
    product, reference = tmp_path / "product.png", tmp_path / "reference.png"
    Image.new("RGB", (600, 600), "red").save(product)
    Image.new("RGB", (600, 800), "blue").save(reference)
    write_json(
        tmp_path / "input.json",
        {
            "product_name": "synthetic fixture",
            "product_images": [str(product)],
            "reference_images": [str(reference)],
        },
    )
    run.cli("start", input=tmp_path / "input.json", session=tmp_path / "session.json")
    with pytest.raises(ReplicaNativeError):
        run.cli(
            "generate-next",
            session=tmp_path / "session.json",
            out=tmp_path / "request.json",
        )
    fields = [
        "subject_and_scale",
        "layout_and_reading_path",
        "carrier_morphology",
        "commercial_function",
        "camera_light_depth",
        "typography_structure",
        "brand_and_human_presence",
        "meaningful_absences",
        "cross_screen_role",
    ]
    observation = {key: "Synthetic fixture observes " + key for key in fields}
    unit = {
        "id": "R01",
        "crop_path": str(reference),
        "crop_sha256": digest(reference.read_bytes()),
        "pixel_width": 600,
        "pixel_height": 800,
        "column_index": 0,
        "screen_index": 0,
        "screen_count_in_column": 1,
        "screen_boundary_reason": "Synthetic fixture single physical screen",
        "ai_reference_observation": observation,
    }
    plan = {
        "plan_contract_version": "6",
        "fidelity_mode": "REMIX_STRICT",
        "reference_units": [unit],
        "sequence_contract": {
            "segmentation_basis": "SCREEN_NOT_COLUMN",
            "reading_order": "COLUMN_MAJOR_SCREEN_TOP_TO_BOTTOM",
            "declared_screen_count": 1,
            "source_unit_order": ["R01"],
        },
        "modules": [
            {
                "id": "M01",
                "reference_unit_id": "R01",
                "reference_path": str(reference),
                "output_canvas": {"width": 600, "height": 800},
                "visual_contract": observation,
                "page_duty": "Synthetic fixture page duty",
                "buyer_conclusion": "Synthetic fixture conclusion",
                "generation_brief": "Synthetic fixture generation brief",
                "copy_units": [],
            }
        ],
    }
    write_json(tmp_path / "plan.json", plan)
    run.cli(
        "compile-plan", session=tmp_path / "session.json", plan=tmp_path / "plan.json"
    )
    with pytest.raises(ReplicaNativeError):
        run.cli(
            "generate-next",
            session=tmp_path / "session.json",
            out=tmp_path / "request.json",
        )
    dimensions = [
        "layout_geometry",
        "carrier_morphology",
        "commercial_function",
        "expression",
        "brand_human_presence",
        "typography_structure",
        "meaningful_absences",
        "aspect_ratio",
    ]
    audit = {
        "method": "AI_FRESH_REFERENCE_AUDIT",
        "unit_audits": [
            {
                "unit_id": "R01",
                "reference_path": str(reference),
                "reference_sha256": unit["crop_sha256"],
                "fresh_pixel_observation": {
                    key: "Second synthetic fixture observation " + key for key in fields
                },
                "comparisons": [
                    {
                        "dimension": d,
                        "match": True,
                        "evidence": "Synthetic structural test evidence: " + d,
                    }
                    for d in dimensions
                ],
                "material_mismatches": [],
                "decision": "PASS",
            }
        ],
    }
    write_json(tmp_path / "audit.json", audit)
    run.cli(
        "audit-plan",
        session=tmp_path / "session.json",
        judgment=tmp_path / "audit.json",
    )
    run.cli(
        "generate-next",
        session=tmp_path / "session.json",
        out=tmp_path / "request.json",
    )
    assert run.state()["state"] == "AWAITING_ARTWORK"
    with pytest.raises(ReplicaNativeError):
        run.cli(
            "generate-next",
            session=tmp_path / "session.json",
            out=tmp_path / "duplicate.json",
        )
    with pytest.raises(ReplicaNativeError):
        run.cli(
            "finalize",
            session=tmp_path / "session.json",
            output=tmp_path / "delivery.json",
        )


def test_geometry_failure_is_a_visual_failure_instead_of_a_schema_retry(tmp_path):
    import importlib.util

    from app.config import default_skill_root

    path = default_skill_root("aurora-ecommerce-reference-remix") / "scripts/harness.py"
    spec = importlib.util.spec_from_file_location("exact_fixture_harness", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    reference, artwork = tmp_path / "reference.png", tmp_path / "artwork.png"
    Image.new("RGB", (600, 800), "blue").save(reference)
    Image.new("RGB", (600, 800), "pink").save(artwork)
    observation = {
        key: "Synthetic observed reference " + key for key in harness.OBSERVATION_FIELDS
    }
    value = {
        "method": "AI_FRESH_SIDE_BY_SIDE_PIXEL_AUDIT",
        "reference_path": str(reference),
        "artwork_path": str(artwork),
        "reference_sha256": digest(reference.read_bytes()),
        "artwork_sha256": digest(artwork.read_bytes()),
        "fresh_reference_observation": observation,
        "fresh_artwork_observation": {
            key: "Different synthetic artwork " + key
            for key in harness.OBSERVATION_FIELDS
        },
        "comparisons": [
            {
                "dimension": key,
                "match": key != "product_identity",
                "evidence": "Distinct synthetic fixture evidence: " + key,
            }
            for key in harness.ARTWORK_AUDIT_DIMENSIONS
        ],
        "major_mismatches": ["Synthetic fixture product cap has different geometry"],
        "product_geometry_match": False,
        "decision": "FAIL",
    }
    _, decision = harness.validate_artwork_audit(
        value,
        {"reference_path": str(reference), "visual_contract": {}},
        str(artwork),
        ["a locked geometry observation"],
    )
    assert decision == "FAIL"


def test_style_explicit_two_reference_count_wins_over_default_three():
    import importlib.util

    from app.config import default_skill_root

    path = (
        default_skill_root("all-category-style-benchmark-still")
        / "scripts/route_contracts.py"
    )
    spec = importlib.util.spec_from_file_location("style_fixture_routes", path)
    routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routes)
    assert routes.layout("CAROUSEL_SET")["count"] == 3
    assert (
        routes.layout("CAROUSEL_SET", 2, "user selected two references")["count"] == 2
    )
    with pytest.raises(ValueError):
        routes.layout("CAROUSEL_SET", 2)
    with pytest.raises(ValueError):
        routes.layout("MARKETING_VISUAL_SET", 2, "user requested two")


def test_style_repair_binds_actual_failed_artwork_and_observed_deltas(tmp_path):
    run = run_for(tmp_path, "style")
    draft = run.root / "draft.png"
    Image.new("RGB", (64, 64), "pink").save(draft)
    prompt = run.root / "prompt.txt"
    prompt.write_text("original native handoff", encoding="utf-8")
    original_hash = digest(draft.read_bytes())
    run.state = lambda: {
        "audits": [
            {
                "target_id": "U01",
                "pass": False,
                "artwork_sha256": original_hash,
                "evidence": {
                    "failure_reasons": ["missing confirmed copy"],
                    "revision_direction": ["add the approved material caption"],
                },
            }
        ],
        "artwork": [{"target_id": "U01", "path": str(draft)}],
    }
    text, sources = NativeReplicaProvider._style_render_inputs(
        run,
        {"revision_index": 1},
        {"prompt_path": str(prompt), "referenced_image_paths": []},
        "U01",
    )
    assert "original native handoff" in text and "missing confirmed copy" in text
    assert sources == [draft]
    draft.write_bytes(b"changed")
    with pytest.raises(ReplicaNativeError, match="哈希"):
        NativeReplicaProvider._style_render_inputs(
            run,
            {"revision_index": 1},
            {"prompt_path": str(prompt), "referenced_image_paths": []},
            "U01",
        )


def test_image_call_fence_is_saved_before_network_and_unknown_never_repeats(tmp_path):
    calls = []

    async def edit(request):
        calls.append("network")
        raise TimeoutError("synthetic unknown outcome")

    run = run_for(tmp_path, gateway=SimpleNamespace(edit=edit))
    provider = NativeReplicaProvider(None, run.gateway, None)

    async def save(*args, **kwargs):
        calls.append("saved")

    provider._save = save
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_fixture",
        job_item_id="item_fixture",
        attempt_id="attempt_fixture",
    )
    with pytest.raises(TimeoutError):
        asyncio.run(
            provider._render(
                run,
                request,
                {"token": "single-use"},
                "fixture",
                [],
                [1024, 1024],
                "M01",
            )
        )
    assert calls == ["saved", "network"]
    with pytest.raises(ReplicaNativeError, match="不会自动重复"):
        asyncio.run(
            provider._render(
                run,
                request,
                {"token": "single-use"},
                "fixture",
                [],
                [1024, 1024],
                "M01",
            )
        )
    assert calls == ["saved", "network"]


def test_style_native_start_precedes_reference_analysis(tmp_path):
    run = run_for(tmp_path, "style")
    image = tmp_path / "input.png"
    Image.new("RGB", (64, 64), "white").save(image)
    result = run.cli(
        "new",
        **{"base-dir": tmp_path, "product-input": [image], "reference-input": [image]},
    )
    run.run = Path(result["run"])
    assert run.state()["state"] == "INPUT_REGISTERED"
    with pytest.raises(ReplicaNativeError):
        run.cli("generate-single-request", workspace=run.run, **{"unit-id": "U01"})


@pytest.mark.parametrize(
    "method,skill",
    [
        ("exact", "aurora-ecommerce-reference-remix"),
        ("style", "all-category-style-benchmark-still"),
    ],
)
def test_api_binds_mode_source_native_plan_confirmation_and_history(
    client, monkeypatch, method, skill
):
    client.app.state.settings.reference_remix_provider_enabled = True
    client.app.state.replica_gateway = SimpleNamespace()
    product, reference = upload(client, "red"), upload(client, "blue")

    async def prepare(self, payload, records):
        assert self.method == method
        assert set(records) == {product, reference}
        return [
            {
                "reference": payload["references"][0],
                "analysis": {"commercial_intent": "synthetic native routing fixture"},
                "prompt": "synthetic structural fixture",
                "unit_id": "M01",
            }
        ]

    async def snapshot(self):
        return {
            "schema": "replica-native-v1",
            "skill_id": self.skill_id,
            "source_hash": self.source_hash,
            "files": {},
        }

    monkeypatch.setattr(NativeReplicaRun, "prepare", prepare)
    monkeypatch.setattr(NativeReplicaRun, "snapshot", snapshot)
    route = "/api/v1/workspaces/ws_test/replica"
    body = {
        "request_id": str(uuid4()),
        "replication_method": method,
        "product_asset_ids": [product],
        "references": [{"asset_id": reference}],
    }
    document = client.post(route + "/sessions", json=body).json()
    document = client.get(route + "/documents/" + document["id"]).json()
    assert document["status"] == "awaiting_confirmation", document
    assert document["payload"]["skill_id"] == skill
    assert "native_snapshot" not in document["payload"]
    assert (
        client.get(route + "/sessions").json()[0]["payload"]["replication_method"]
        == method
    )
    confirm = route + "/sessions/" + document["id"] + "/confirm"
    payload = {"plan_hash": document["payload"]["plan_hash"]}
    first, replay = (
        client.post(confirm, json=payload),
        client.post(confirm, json=payload),
    )
    assert first.status_code == replay.status_code == 202, first.text
    assert first.json()["payload"]["job_ids"] == replay.json()["payload"]["job_ids"]
    job_id = first.json()["payload"]["job_ids"][0]
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{job_id}").json()
    assert job["configuration"]["settings"]["reference_skill"] == skill
    assert job["configuration"]["settings"]["replication_method"] == method
    assert job["configuration"]["replica_session_id"] == document["id"]
    assert (
        client.post(confirm, json={**payload, "instructions": ["changed"]}).status_code
        == 409
    )


def test_native_jobs_cannot_bypass_server_confirmation(client):
    product, reference = upload(client, "red"), upload(client, "blue")
    response = client.post(
        "/api/v1/workspaces/ws_test/jobs",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "type": "reference_remix",
            "intent": "submit",
            "input": {
                "product_asset_ids": [product],
                "reference_asset_ids": [reference],
            },
            "configuration": {
                "reference_skill": "aurora-ecommerce-reference-remix",
                "replication_method": "exact",
                "replica_plan_hash": "0" * 64,
            },
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REPLICA_CONFIRM_REQUIRED"


def test_native_campaign_cannot_run_two_items_in_duplicate_workers(client):
    from app.execution import ExecutionCoordinator
    from app.models import JobRecord
    from app.provider import FakeProvider

    from .test_execution_reliability import _create_job, _expand_job_to_three_items

    job_id = _create_job(client)
    _expand_job_to_three_items(client, job_id)
    with client.app.state.session_factory() as session:
        job = session.get(JobRecord, job_id)
        job.type, job.status = "reference_remix", "processing"
        job.configuration = {
            "replica_session_id": "rep_fixture",
            "settings": {"reference_skill": "aurora-ecommerce-reference-remix"},
        }
        session.commit()
    one = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        worker_id="native-one",
        max_concurrency=6,
    )
    two = ExecutionCoordinator(
        client.app.state.session_factory,
        FakeProvider(),
        worker_id="native-two",
        max_concurrency=6,
    )
    assert one.claim_next_item(job_id) is not None
    assert two.claim_next_item(job_id) is None
