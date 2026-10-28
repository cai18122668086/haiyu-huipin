"""Website v1 travels through real native compilation, pixels and evidence gates."""

import json
from copy import deepcopy

import pytest

from app.aurora_detail_runtime import detail_workspace
from app.domestic_detail import authoring, website_contract
from app.xinghe_agent import XingheImageProvider, _planner_response_format
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_domestic_detail_execution import _aurora_approval_payload, _gateway


@pytest.fixture(autouse=True)
def explicit_synthetic_storage(client, monkeypatch):
    # The ordinary mock shortcut deliberately does not persist merchant outputs.
    # This explicit fixture exercises real storage while marking pixels synthetic.
    monkeypatch.setattr(_gateway(client), "provider_name", "synthetic")

    async def observe(self, request, native, sources, artifact):
        return self._normalize_native_pair_judgment(
            self._native_detail_judgment(native, artifact), native, artifact
        )

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", observe
    )


def planned(client, profile=website_contract.PROFILE, auto_start=False):
    source = upload_asset(client, filename="website-v1-product.png")
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=idem(),
        json={
            "type": "detail_pack",
            "intent": "submit",
            "display_name": "Website v1 synthetic acceptance",
            "input": {
                "product_groups": [
                    {"group_key": "primary", "asset_ids": [source["id"]]}
                ]
            },
            "configuration": {
                "product_name": "Synthetic product",
                "platform_code": "tmall",
                "market_region": "domestic",
                "page_count": 8,
                "selling_points": ["合成商品可见外观", "合成商品局部结构"],
                "brandless_confirmation": "测试商品无品牌，不添加独立标志",
                "website_contract_version": profile,
                "auto_start_after_plan": auto_start,
            },
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    if not auto_start:
        assert result["status"] == "awaiting_confirmation", result
    return result


def generate(client, job):
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(job),
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    return client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()


def test_website_one_map_compiles_and_four_pairs_keep_lean_original_evidence(client):
    job = planned(client)
    calls = _gateway(client).planner_calls
    story = next(call for call in calls if call.authoring_stage == "story")
    schema = _planner_response_format(story)["json_schema"]["schema"]
    assert "claim_coverage" in schema["required"]
    assert all(key not in schema["properties"] for key in website_contract.DERIVED)
    assert len(calls) == 6
    assert (
        "本次采用精简检查"
        in job["plan"]["payload"]["native_approval"]["document_markdown"]
    )
    current = generate(client, job)
    assert current["status"] == "awaiting_final_review", (
        current.get("error_code"),
        current.get("error_message"),
    )
    assert len(_gateway(client).image_calls) == 4
    root = detail_workspace(job["id"])
    state = json.loads((root / "session.json").read_bytes())
    assert state["plan"]["website_contract_version"] == website_contract.PROFILE
    assert all(
        row["plan"]["website_contract_version"] == website_contract.PROFILE
        for row in state["modules"]
    )
    report = state["modules"][0]["attempts"][0]["judgment"]
    assert report["website_contract_version"] == website_contract.PROFILE
    assert len(report["module_judgments"][0]["checks"]) == 6
    assert "headline_rank" not in report["module_judgments"][0]["observations"]
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/detail-final-review"
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "problem", ["missing_check", "unknown_presence", "contradictory_pass"]
)
def test_website_incomplete_or_contradictory_review_never_spends_repair(
    client, monkeypatch, problem
):
    job = planned(client)

    async def review(self, request, native, sources, artifact):
        raw = self._native_detail_judgment(native, artifact)
        report = raw["module_judgments"][0]
        if problem == "missing_check":
            report["checks"].pop("facts")
        elif problem == "unknown_presence":
            report["observations"]["product_presence_observed"] = None
        else:
            report["checks"]["facts"]["pass"] = False
        return self._normalize_native_pair_judgment(raw, native, artifact)

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", review
    )
    current = generate(client, job)
    assert current["status"] == "failed", current
    assert len(_gateway(client).image_calls) == 1


def test_website_material_failure_repairs_once_and_preserves_residuals(
    client, monkeypatch
):
    job = planned(client)
    originals = []

    async def review(self, request, native, sources, artifact):
        raw = self._native_detail_judgment(native, artifact)
        if native["pair_id"] == "P01":
            finding = {
                "check": "proof_integrity",
                "visible_evidence": "SYNTHETIC proof relation absent in lower window",
            }
            raw.update(status="MATERIAL_FAIL", material_failures=[finding])
            report = raw["module_judgments"][1]
            report.update(status="MATERIAL_FAIL", material_failures=[finding])
            report["checks"]["proof_integrity"]["pass"] = False
            originals.append(deepcopy(raw["module_judgments"]))
        return self._normalize_native_pair_judgment(raw, native, artifact)

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", review
    )
    current = generate(client, job)
    assert current["status"] == "awaiting_final_review", (
        current.get("error_code"),
        current.get("error_message"),
    )
    assert len(_gateway(client).image_calls) == 5
    state = json.loads((detail_workspace(job["id"]) / "session.json").read_bytes())
    assert all(
        row["status"] == "COMPLETE_WITH_RESIDUALS" for row in state["modules"][:2]
    )
    for module in state["modules"][:2]:
        for index, attempt in enumerate(module["attempts"]):
            assert attempt["judgment"]["module_judgments"] == originals[index]


def test_website_explicit_conflicting_view_is_not_overwritten(client):
    planned(client)
    gateway = _gateway(client)
    story = next(
        call for call in gateway.planner_calls if call.authoring_stage == "story"
    )
    from app.xinghe_agent import _fake_plan

    raw = authoring.fake_phase(_fake_plan(story), story)
    value = authoring.project_stage(raw, story)
    value["campaign_recall_contract"]["must_remember_claim_ids"] = ["UNVERIFIED"]
    before = deepcopy(value)
    projected = authoring.project_stage(value, story)
    assert projected == before
    assert authoring.stage_errors(projected, story)


def test_native_negative_ocr_uses_bounded_repair_without_accepting_model_pass(
    client, monkeypatch
):
    job = planned(client)
    originals = []

    async def review(self, request, native, sources, artifact):
        raw = self._native_detail_judgment(native, artifact)
        if native["pair_id"] == "P01":
            report = raw["module_judgments"][0]
            report["observations"]["ocr_text"].append("UNAPPROVED_SOURCE_MARK_9000")
            report["artifact_binding"]["ocr_text"] = deepcopy(
                report["observations"]["ocr_text"]
            )
            before = deepcopy(raw)
            result = self._normalize_native_pair_judgment(raw, native, artifact)
            assert raw == before and raw["status"] == "PASS"
            assert result["status"] == "MATERIAL_FAIL"
            assert result["module_judgments"][0]["checks"] == report["checks"]
            originals.append(before)
            return result
        return self._normalize_native_pair_judgment(raw, native, artifact)

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", review
    )
    current = generate(client, job)
    assert current["status"] == "awaiting_final_review", current
    assert len(originals) == 2
    assert len(_gateway(client).image_calls) == 5
    state = json.loads((detail_workspace(job["id"]) / "session.json").read_bytes())
    assert state["modules"][0]["status"] == "COMPLETE_WITH_RESIDUALS"
