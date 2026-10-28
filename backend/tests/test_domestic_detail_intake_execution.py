from __future__ import annotations

import pytest

from tests.test_api import WORKSPACE_ID, dispatch_pending, upload_asset
from tests.test_domestic_detail_execution import _create_detail_job, _gateway


@pytest.mark.parametrize("with_style", [False, True])
def test_model_intake_precedes_actual_native_preflight(client, with_style):
    source = upload_asset(client, filename="intake-product.png")
    styles = (
        [upload_asset(client, filename="intake-style.png")["id"]] if with_style else []
    )
    job = _create_detail_job(
        client,
        source["id"],
        market_region="domestic",
        auto_start_after_plan=False,
        reference_asset_ids=styles,
    )
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == "awaiting_confirmation", (
        planned.get("error_code"),
        planned.get("error_message"),
        planned.get("plan", {}).get("validation_errors"),
    )
    gateway = _gateway(client)
    assert [call.planning_profile for call in gateway.planner_calls] == [
        "aurora_domestic_intake",
        "aurora_domestic_detail",
        *["aurora_domestic_modules_completion"] * 4,
    ]
    assert gateway.image_calls == []
    contract = gateway.planner_calls[1].detail_intake
    assert contract["identity"]["invariant_features"] == [
        row["text"] for row in contract["identity_observations"]
    ]
    assert [fact["text"] for fact in contract["user_facts"]] == [
        "durable plan",
        "bounded generation",
    ]
    assert {row["id"] for row in contract["reference_materials"]} == set(styles)


def test_missing_brand_waits_then_resumes_same_task_without_images(client):
    from copy import deepcopy

    from app.models import JobRecord
    from tests.test_api import idem

    source = upload_asset(client, filename="no-readable-brand.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    with client.app.state.session_factory() as db:
        record = db.get(JobRecord, job["id"])
        configuration = deepcopy(record.configuration)
        configuration["settings"]["brandless_confirmation"] = None
        record.configuration = configuration
        db.commit()
    dispatch_pending(client)
    pending = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert pending["stage"] == "intake_confirmation_required", pending.get(
        "error_message"
    )
    assert "resolve_intake" in pending["allowed_actions"]
    assert "confirm_plan" not in pending["allowed_actions"]
    assert len(_gateway(client).planner_calls) == 1
    assert _gateway(client).image_calls == []
    body = {
        "expected_revision": pending["plan"]["revision"],
        "confirmation_text": "我确认当前测试商品无品牌",
        "resolutions": [],
        "brandless_confirmation": "我确认当前测试商品无品牌，不添加独立标志",
    }
    key = idem()
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/intake/resolve",
        headers=key,
        json=body,
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["stage"] == "awaiting_confirmation", (
        planned.get("error_code"),
        planned.get("error_message"),
    )
    assert planned["id"] == pending["id"]
    assert [call.planning_profile for call in _gateway(client).planner_calls] == [
        "aurora_domestic_intake",
        "aurora_domestic_intake",
        "aurora_domestic_detail",
        *["aurora_domestic_modules_completion"] * 4,
    ]
    assert len(planned["plan"]["payload"]["usage"]["planner"]) == 7
    assert _gateway(client).image_calls == []
    replay = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/intake/resolve",
        headers=key,
        json=body,
    )
    assert replay.status_code == 202
    assert len(_gateway(client).planner_calls) == 7


def test_material_conflicts_are_batched_and_human_choice_reaches_native_harness(
    client, monkeypatch
):
    from copy import deepcopy
    from dataclasses import replace

    from app.models import JobRecord
    from tests.test_api import idem

    source = upload_asset(client, filename="sku-conflict.png")
    job = _create_detail_job(
        client, source["id"], market_region="domestic", auto_start_after_plan=False
    )
    with client.app.state.session_factory() as db:
        record = db.get(JobRecord, job["id"])
        configuration = deepcopy(record.configuration)
        configuration["settings"]["selling_points"] = ["容量 300 ml", "容量 500 ml"]
        record.configuration = configuration
        db.commit()
    gateway = _gateway(client)
    original = gateway.plan

    async def conflict(request):
        result = await original(request)
        if request.planning_profile != "aurora_domestic_intake":
            return result
        value = deepcopy(result.payload)
        value["conflict_scan"].update(status="CONFLICTS_FOUND", conflict_ids=["C01"])
        value["source_conflicts"] = [
            {
                "id": "C01",
                "topic": "容量",
                "alternative_fact_ids": ["F01", "F02"],
                "why_material": "同一销售规格不能同时为两种不同容量",
                "status": "UNRESOLVED",
            }
        ]
        if not request.intake_resolution:
            value.pop("identity", None)
            value.pop("identity_observations", None)
        return replace(result, payload=value)

    monkeypatch.setattr(gateway, "plan", conflict)
    dispatch_pending(client)
    pending = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert pending["stage"] == "intake_confirmation_required", pending.get(
        "error_message"
    )
    assert len(gateway.planner_calls) == 1 and gateway.image_calls == []
    url = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/intake/resolve"
    incomplete = client.post(
        url,
        headers=idem(),
        json={
            "expected_revision": 1,
            "confirmation_text": "确认 500 ml",
            "resolutions": [],
        },
    )
    assert incomplete.status_code == 422
    response = client.post(
        url,
        headers=idem(),
        json={
            "expected_revision": 1,
            "confirmation_text": "确认当前售卖规格为 500 ml，排除 300 ml",
            "resolutions": [
                {
                    "conflict_id": "C01",
                    "retained_fact_ids": ["F02"],
                    "rejected_fact_ids": ["F01"],
                }
            ],
        },
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["stage"] == "awaiting_confirmation", (
        planned.get("error_code"),
        planned.get("error_message"),
    )
    assert len(gateway.planner_calls) == 7
    contract = gateway.planner_calls[-1].detail_intake
    assert contract["resolution"]["resolutions"][0]["retained_fact_ids"] == ["F02"]
    assert gateway.image_calls == []
    import hashlib
    import json

    from app.aurora_detail_runtime import detail_workspace
    from app.xinghe_agent import XingheImageProvider
    from tests.test_domestic_detail_execution import _aurora_approval_payload

    contexts = []
    approved = planned["plan"]["payload"]["native_approval"]

    async def inspect_authority(self, request, native_request, sources, artifact):
        del request, sources
        authority = native_request["review_authority"]
        assert authority["product_information"] == ["容量 500 ml"]
        assert native_request["product_information"] == ["容量 500 ml"]
        assert (
            authority["native_approval_document_sha256"] == approved["document_sha256"]
        )
        for key in ("input", "identity", "plan"):
            field = f"compiled_{key}_sha256"
            assert authority[field] == approved[field]
        ledger = {row["fact"]["id"]: row for row in authority["original_fact_ledger"]}
        assert ledger["F01"]["fact"]["text"] == "容量 300 ml"
        assert ledger["F01"]["approved_disposition"] == "EXCLUDED"
        assert ledger["F02"]["approved_disposition"] == "LEAD"
        assert authority["locked_identity"] == contract["identity"]
        contexts.append(deepcopy(native_request))
        return self._native_detail_judgment(native_request, artifact)

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", inspect_authority
    )
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(), json=_aurora_approval_payload(planned),
    )
    assert confirmed.status_code == 202, confirmed.text
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert current["status"] == "awaiting_final_review", (
        current.get("error_code"),
        [
            (item.get("error_code"), item.get("error_message"))
            for item in current["items"]
        ],
    )
    assert len(gateway.image_calls) == len(contexts) == 4
    assert len(gateway.planner_calls) == 7
    assert len({context["review_authority"]["sha256"] for context in contexts}) == 1
    for context in contexts:
        page = int(context["module_ids"][0][1:])
        raw = (detail_workspace(job["id"]) / f"generation-{page:03d}.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == context["signed_request_sha256"]
        assert "review_authority" not in json.loads(raw)



@pytest.mark.parametrize("cost", [None, 0, 1700])
def test_unknown_intake_never_resends_across_multiple_failed_retries(
    client, monkeypatch, cost
):
    from dataclasses import replace

    from tests.test_api import idem

    gateway = _gateway(client)
    original = gateway.plan

    async def unknown(request):
        result = await original(request)
        assert request.planning_profile == "aurora_domestic_intake"
        return replace(
            result,
            payload=None,
            provider_request_id="req-known-but-outcome-unknown",
            actual_cost_micros=cost,
            http_status=503,
            error_code="INTAKE_OUTCOME_UNKNOWN",
            error_message="Unknown upstream outcome",
        )

    monkeypatch.setattr(gateway, "plan", unknown)
    product = upload_asset(client, filename="unknown-intake.png")
    job = _create_detail_job(
        client, product["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    job_id = job["id"]
    for _ in range(3):
        failed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}").json()
        assert failed["status"] == "failed", failed
        assert failed["error_code"] == "INTAKE_OUTCOME_UNKNOWN"
        assert len(gateway.planner_calls) == 1 and gateway.image_calls == []
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}/retry",
            headers=idem(),
            json={"scope": "failed"},
        )
        assert response.status_code == 201, response.text
        job_id = response.json()["id"]
        dispatch_pending(client)
    assert len(gateway.planner_calls) == 1
    assert (
        client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}").json()[
            "error_code"
        ]
        == "INTAKE_OUTCOME_UNKNOWN"
    )


@pytest.mark.parametrize("drift", ["settings", "pixels", "role", "question"])
def test_source_confirmation_rejects_drift_before_another_paid_call(client, drift):
    from copy import deepcopy

    from sqlalchemy import select

    from app.models import AssetRecord, JobAssetRecord, JobPlanRecord, JobRecord
    from tests.test_api import idem

    product = upload_asset(client, filename="intake-drift.png")
    job = _create_detail_job(
        client, product["id"], market_region="domestic", auto_start_after_plan=False
    )
    with client.app.state.session_factory() as db:
        record = db.get(JobRecord, job["id"])
        config = deepcopy(record.configuration)
        config["settings"]["brandless_confirmation"] = None
        record.configuration = config
        db.commit()
    dispatch_pending(client)
    pending = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert pending["stage"] == "intake_confirmation_required"
    with client.app.state.session_factory() as db:
        record = db.get(JobRecord, job["id"])
        if drift == "settings":
            config = deepcopy(record.configuration)
            config["settings"]["selling_points"] = ["changed original fact"]
            record.configuration = config
        elif drift == "pixels":
            db.get(AssetRecord, product["id"]).sha256 = "0" * 64
        elif drift == "role":
            db.scalar(
                select(JobAssetRecord).where(JobAssetRecord.job_id == job["id"])
            ).role = "style_reference"
        else:
            plan = db.scalar(
                select(JobPlanRecord).where(JobPlanRecord.job_id == job["id"])
            )
            payload = deepcopy(plan.payload)
            payload["product_intake"]["brand_observation"]["text"] = (
                "Changed model observation"
            )
            plan.payload = payload
        db.commit()
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/intake/resolve",
        headers=idem(),
        json={
            "expected_revision": pending["plan"]["revision"],
            "confirmation_text": "我确认当前商品无品牌",
            "brandless_confirmation": "我确认当前商品无品牌",
            "resolutions": [],
        },
    )
    assert response.status_code == 409, response.text
    assert "INTAKE_BINDING_CHANGED" in response.text
    assert (
        len(_gateway(client).planner_calls) == 1 and _gateway(client).image_calls == []
    )


@pytest.mark.parametrize("mode", ["all_rejected", "subset_scheduled"])
def test_actual_native_compiler_preserves_reference_adoption_choices(
    client, monkeypatch, mode
):
    from copy import deepcopy
    from dataclasses import replace

    gateway = _gateway(client)
    original = gateway.plan

    async def choose_reference(request):
        result = await original(request)
        if request.planning_profile != "aurora_domestic_detail":
            return result
        value = deepcopy(result.payload)
        system = value["reference_expression_system"]
        if mode == "all_rejected":
            system["adopted_patterns"] = []
            for row in system["coverage"]:
                row.update(
                    disposition="REJECTED_IRRELEVANT",
                    pattern_ids=[],
                    reason="该参考与商品叙事不相关，明确不采用其视觉语法",
                )
        else:
            for pattern in system["adopted_patterns"]:
                pattern["scheduled_module_ids"] = ["M02"]
        return replace(result, payload=value)

    monkeypatch.setattr(gateway, "plan", choose_reference)
    product = upload_asset(client, filename="reference-product.png")
    style = upload_asset(client, filename="reference-style.png")
    job = _create_detail_job(
        client,
        product["id"],
        market_region="domestic",
        auto_start_after_plan=False,
        reference_asset_ids=[style["id"]],
    )
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["stage"] == "awaiting_confirmation", (
        planned.get("error_code"),
        planned.get("error_message"),
    )
    assert len(gateway.planner_calls) == 6 and gateway.image_calls == []
    native = planned["plan"]["payload"]["groups"][0]["plan_protocol_fields"][
        "aurora_plan"
    ]
    assert native["modules"][0]["reference_pattern_ids"] == []
    if mode == "all_rejected":
        assert (
            native["reference_expression_system"]["coverage"][0]["disposition"]
            == "REJECTED_IRRELEVANT"
        )
    else:
        assert native["reference_expression_system"]["adopted_patterns"][0][
            "scheduled_module_ids"
        ] == ["M02"]


def test_photo_led_intake_carries_observed_facts_and_hypotheses_into_native_plan(
    client,
):
    from copy import deepcopy

    from app.models import JobRecord

    product = upload_asset(client, filename="photo-led-source.png")
    job = _create_detail_job(
        client, product["id"], market_region="domestic", auto_start_after_plan=False
    )
    with client.app.state.session_factory() as db:
        record = db.get(JobRecord, job["id"])
        config = deepcopy(record.configuration)
        config["settings"]["selling_points"] = []
        config["settings"]["verified_facts"] = []
        record.configuration = config
        db.commit()
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["stage"] == "awaiting_confirmation", (
        planned.get("error_code"),
        planned.get("error_message"),
    )
    intake = planned["plan"]["payload"]["product_intake"]
    assert intake["user_facts"] == []
    assert intake["observed_facts"][0]["source_class"] == "OBSERVED_PRODUCT_EVIDENCE"
    assert intake["operating_analysis"]["assumption_disclosure"]
    assert (
        len(_gateway(client).planner_calls) == 6 and _gateway(client).image_calls == []
    )


def test_tampered_intake_receipt_stops_failed_only_retry_without_rebilling(client):
    import json

    from app.aurora_detail_runtime import detail_workspace
    from tests.test_api import idem

    gateway = _gateway(client)
    gateway.planner_failure = "schema"
    product = upload_asset(client, filename="tampered-intake.png")
    job = _create_detail_job(
        client, product["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    failed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert failed["status"] == "failed"
    response_path = next(
        (detail_workspace(job["id"]) / "intake").glob("*.response.json")
    )
    receipt = json.loads(response_path.read_text(encoding="utf-8"))
    receipt["result"]["payload"]["version"] = "changed"
    response_path.write_text(json.dumps(receipt), encoding="utf-8")
    before = len(gateway.planner_calls)
    gateway.planner_failure = None
    retry = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/retry",
        headers=idem(),
        json={"scope": "failed"},
    )
    assert retry.status_code == 201, retry.text
    dispatch_pending(client)
    result = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{retry.json()['id']}"
    ).json()
    assert (
        result["status"] == "failed"
        and result["error_code"] == "INTAKE_RESPONSE_CHANGED"
    ), result
    assert len(gateway.planner_calls) == before and gateway.image_calls == []


@pytest.mark.parametrize("readable", [False, True])
def test_supplementary_brand_photo_stays_product_scoped_and_bounded(
    client, monkeypatch, readable
):
    from copy import deepcopy
    from dataclasses import replace

    from app.domestic_detail.mock_intake import mock_intake
    from app.models import JobRecord
    from tests.test_api import idem

    product = upload_asset(client, filename="unreadable-brand-product.png")
    extra = upload_asset(client, filename="supplementary-product-brand.png")
    job = _create_detail_job(
        client, product["id"], market_region="domestic", auto_start_after_plan=False
    )
    with client.app.state.session_factory() as db:
        record = db.get(JobRecord, job["id"])
        config = deepcopy(record.configuration)
        config["settings"]["brandless_confirmation"] = None
        record.configuration = config
        db.commit()
    gateway = _gateway(client)
    original = gateway.plan

    async def inspect_brand(request):
        result = await original(request)
        if (
            request.planning_profile != "aurora_domestic_intake"
            or not request.intake_resolution
            or not readable
        ):
            return result
        assert [source.asset_id for source in request.sources] == [
            product["id"],
            extra["id"],
        ]
        value = mock_intake(
            {
                **request.intake_settings,
                "brandless_confirmation": "Synthetic fixture construction only",
            },
            [
                {
                    "asset_id": source.asset_id,
                    "role": source.source_role,
                    "sha256": source.sha256,
                }
                for source in request.sources
            ],
            request.intake_resolution,
        )
        value["identity"]["brand_logo_source"] = {
            "mode": "BRANDED",
            "image_id": extra["id"],
            "authorization_basis": "USER_SUPPLIED_CURRENT_PRODUCT",
            "readability": "READABLE",
            "usage": "FIRST_SCREEN_INDEPENDENT_LOGO",
            "observed_logo_text": "TESTBRAND",
            "observed_region": "补充商品图正面品牌区域",
        }
        return replace(result, payload=value)

    monkeypatch.setattr(gateway, "plan", inspect_brand)
    dispatch_pending(client)
    pending = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert pending["stage"] == "intake_confirmation_required"
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/intake/resolve",
        headers=idem(),
        json={
            "expected_revision": pending["plan"]["revision"],
            "confirmation_text": "我补充清晰商品品牌图供分析",
            "resolutions": [],
            "additional_product_asset_ids": [extra["id"]],
        },
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    if readable:
        assert result["stage"] == "awaiting_confirmation", (
            result.get("error_code"),
            result.get("error_message"),
        )
        brand = result["plan"]["payload"]["product_intake"]["identity"][
            "brand_logo_source"
        ]
        assert brand["mode"] == "BRANDED" and brand["image_id"] == extra["id"]
    else:
        assert (
            result["status"] == "failed"
            and result["error_code"] == "INTAKE_CONFIRMATION_STILL_UNRESOLVED"
        ), result
    assert (
        len(gateway.planner_calls) == (7 if readable else 2)
        and gateway.image_calls == []
    )


def test_contradictory_batch_answer_is_rejected_before_resuming_paid_intake(
    client, monkeypatch
):
    from copy import deepcopy
    from dataclasses import replace

    from tests.test_api import idem

    gateway = _gateway(client)
    original = gateway.plan

    async def scan_two_conflicts(request):
        result = await original(request)
        if request.planning_profile != "aurora_domestic_intake":
            return result
        value = deepcopy(result.payload)
        value["conflict_scan"].update(
            status="CONFLICTS_FOUND", conflict_ids=["C01", "C02"]
        )
        value["source_conflicts"] = [
            {
                "id": conflict_id,
                "topic": "关联规格核对",
                "alternative_fact_ids": ["F01", "F02"],
                "why_material": "同一销售规格的两个来源需要一致取舍",
                "status": "UNRESOLVED",
            }
            for conflict_id in ("C01", "C02")
        ]
        value.pop("identity", None)
        value.pop("identity_observations", None)
        return replace(result, payload=value)

    monkeypatch.setattr(gateway, "plan", scan_two_conflicts)
    product = upload_asset(client, filename="cross-conflict-product.png")
    job = _create_detail_job(
        client, product["id"], market_region="domestic", auto_start_after_plan=False
    )
    dispatch_pending(client)
    pending = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert pending["stage"] == "intake_confirmation_required"
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/intake/resolve",
        headers=idem(),
        json={
            "expected_revision": pending["plan"]["revision"],
            "confirmation_text": "一次确认以上全部规格",
            "resolutions": [
                {
                    "conflict_id": "C01",
                    "retained_fact_ids": ["F01"],
                    "rejected_fact_ids": ["F02"],
                },
                {
                    "conflict_id": "C02",
                    "retained_fact_ids": ["F02"],
                    "rejected_fact_ids": ["F01"],
                },
            ],
        },
    )
    assert (
        response.status_code == 422
        and "INTAKE_RESOLUTION_CONTRADICTORY" in response.text
    )
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert (
        current["stage"] == pending["stage"]
        and current["plan"]["revision"] == pending["plan"]["revision"]
    )
    assert len(gateway.planner_calls) == 1 and gateway.image_calls == []
