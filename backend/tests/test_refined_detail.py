from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import pytest
from PIL import Image

from app import refined_detail
from app.provider import ProviderArtifact
from app.skill_runtime import SkillManifestRegistry
from app.xinghe_agent import XingheAgentError, load_planner_system_prompt
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_domestic_detail_execution import _gateway


def create(client, *, skill="refined", count=8, intent="submit", auto_start=False):
    source = upload_asset(client)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs",
        headers=idem(),
        json={
            "type": "detail_pack",
            "intent": intent,
            "display_name": "精美测试",
            "input": {
                "product_groups": [
                    {"group_key": "primary", "asset_ids": [source["id"]]}
                ]
            },
            "configuration": {
                "detail_skill": skill,
                "product_name": "离线测试商品",
                "platform_code": "tmall",
                "market_region": "domestic",
                "page_count": count,
                "selling_points": ["合成测试输入"],
                "auto_start_after_plan": auto_start,
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def planned(client, **kwargs):
    job = create(client, **kwargs)
    dispatch_pending(client)
    job = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert job["status"] == "awaiting_confirmation", job
    return job


def confirmation(job):
    return {
        "expected_revision": job["plan"]["revision"],
        "approval_text": "我已审阅完整方案，确认按此生成。",
        "approval_document_sha256": job["plan"]["payload"]["refined_approval"][
            "document_sha256"
        ],
    }


def test_source_package_is_unmodified_and_complete():
    source = (
        Path(__file__).resolve().parents[1] / "skill_sources" / refined_detail.SKILL_ID
    )
    registry = SkillManifestRegistry.bundled()
    registry.verify_source(refined_detail.SKILL_ID, source)
    prompt = load_planner_system_prompt(source, refined_detail.SKILL_ID)
    for path in [source / "SKILL.md", *sorted((source / "references").glob("*.md"))]:
        assert path.read_text(encoding="utf-8") in prompt
    archive_path = Path("D:/Downloads/星河电商详情4.0.zip")
    if archive_path.exists():
        with ZipFile(archive_path) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            assert len(entries) == 22
            for entry in entries:
                parts = PurePosixPath(entry.filename.replace("\\", "/")).parts
                assert (source / Path(*parts[1:])).read_bytes() == archive.read(entry)


@pytest.mark.parametrize("count", [1, 7, 8, 10, 12])
def test_refined_selects_its_own_complete_plan_and_waits_for_confirmation(
    client, count
):
    job = planned(client, count=count)
    gateway = _gateway(client)
    assert (
        job["configuration"]["domestic_detail_pipeline"]
        == refined_detail.PIPELINE_VERSION
    )
    assert job["configuration"]["settings"]["detail_skill"] == "refined"
    assert len(gateway.planner_calls) == 1
    assert gateway.planner_calls[0].planning_profile == refined_detail.PROFILE
    assert not gateway.image_calls
    payload = job["plan"]["payload"]
    assert len(payload["pages"]) == count
    assert "aurora_v4_3" not in payload
    assert all("pair_id" not in page for page in payload["pages"])
    refined_detail.validate_payload(payload, count)
    approval = payload["refined_approval"]
    assert approval["document_sha256"] == refined_detail.approval_hash(payload)
    assert "星河" not in approval["document_markdown"]
    assert "4.0" not in approval["document_markdown"]
    assert "image_prompt" not in approval["document_markdown"]
    bad = confirmation(job) | {"approval_document_sha256": "0" * 64}
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json=bad,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REFINED_DETAIL_APPROVAL_REQUIRED"
    assert not gateway.image_calls


def test_refined_generates_each_authored_prompt_once_and_delivers_original_bytes(
    client, monkeypatch
):
    gateway = _gateway(client)
    gateway.provider_name = "synthetic"
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    original_edit = gateway.edit
    original_artifacts = {}

    async def edit(request):
        result = await original_edit(request)
        # Explicit synthetic provider fixture. These are never merchant images.
        output = BytesIO()
        Image.new("RGB", (1440, 1920), (request.page_number, 60, 90)).save(
            output, "PNG"
        )
        data = output.getvalue()
        original_artifacts[request.page_number] = data
        return replace(
            result,
            artifact=ProviderArtifact(
                payload=data,
                filename=f"synthetic-{request.page_number}.png",
                content_type="image/png",
                width=1440,
                height=1920,
                sha256=hashlib.sha256(data).hexdigest(),
            ),
        )

    async def skip_delay(_delay):
        return None

    monkeypatch.setattr(gateway, "edit", edit)
    monkeypatch.setattr("app.execution.asyncio.sleep", skip_delay)
    job = planned(client)
    pages = job["plan"]["payload"]["pages"]
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json=confirmation(job),
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert current["status"] == "completed", current
    assert len(gateway.image_calls) == 8
    assert len(gateway.planner_calls) == 1  # No automatic image audit/rewrite.
    assert [call.prompt for call in gateway.image_calls] == [
        page["image_prompt"] for page in pages
    ]
    assert all(
        call.size == "1440x1920" and call.preserve_provider_artifact
        for call in gateway.image_calls
    )
    archive = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/deliverables.zip"
    )
    assert archive.status_code == 200, archive.text
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert len(bundle.namelist()) == 8
        for number in range(1, 9):
            assert bundle.read(f"page-{number:03d}.png") == original_artifacts[number]
    assert gateway.external_network_request_count == 0


def test_refined_rejects_missing_strategy_instead_of_filling_in_placeholders(client):
    job = planned(client)
    payload = deepcopy(job["plan"]["payload"])
    del payload["groups"][0]["analysis"]["buyer_demand_map"]
    with pytest.raises(XingheAgentError, match="required"):
        refined_detail.validate_payload(payload, 8)
    payload = deepcopy(job["plan"]["payload"])
    original = refined_detail.approval_hash(payload)
    payload["pages"][0]["image_prompt"] += " changed execution"
    assert refined_detail.approval_hash(payload) != original


def test_strategy_field_location_preserves_content_and_rejects_conflicts(
    client,
):
    planned(client)
    gateway = _gateway(client)
    request = gateway.planner_calls[-1]
    from app.xinghe_agent import _fake_plan

    value = _fake_plan(request)
    original = deepcopy(value)
    expected = value["analysis"].pop("product_type_strategy")
    value["product_type_strategy"] = expected
    normalized = refined_detail.normalize_strategy(value)
    assert normalized == original
    assert value["product_type_strategy"] == expected
    refined_detail.validate_strategy(normalized, 8)
    conflict = deepcopy(value)
    conflict["analysis"]["product_type_strategy"] = {"different": "authored decision"}
    with pytest.raises(XingheAgentError, match="Conflicting"):
        refined_detail.normalize_strategy(conflict)
    missing = deepcopy(value)
    del missing["product_type_strategy"]
    with pytest.raises(XingheAgentError, match="required"):
        refined_detail.validate_strategy(refined_detail.normalize_strategy(missing), 8)


def test_manual_selected_revision_reuses_other_pages_and_requires_new_approval(
    client, monkeypatch
):
    async def skip_delay(_delay):
        return None

    monkeypatch.setattr("app.execution.asyncio.sleep", skip_delay)
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    job = planned(client)
    gateway = _gateway(client)
    gateway.provider_name = "synthetic"
    original_edit = gateway.edit

    async def edit(request):
        result = await original_edit(request)
        # Refined-only PNG fixture; shared/main-image mock behavior stays intact.
        output = BytesIO()
        with Image.open(BytesIO(result.artifact.payload)) as synthetic:
            synthetic.save(output, format="PNG")
        data = output.getvalue()
        return replace(result, artifact=ProviderArtifact(
            payload=data,
            filename=f"synthetic-{request.page_number}.png",
            content_type="image/png",
            width=1440,
            height=1920,
            sha256=hashlib.sha256(data).hexdigest(),
        ))

    monkeypatch.setattr(gateway, "edit", edit)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/plan/confirm",
        headers=idem(),
        json=confirmation(job),
    )
    assert response.status_code == 202
    dispatch_pending(client)
    original = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert original["status"] == "completed"
    original_archive = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/deliverables.zip"
    )
    assert original_archive.status_code == 200, original_archive.text
    original_zip = original_archive.content
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/retry",
        headers=idem(),
        json={
            "scope": "selected",
            "item_ids": [item["id"] for item in original["items"][6:]],
        },
    )
    assert response.status_code == 201, response.text
    revision = response.json()
    assert revision["status"] == "awaiting_confirmation"
    assert revision["plan"]["confirmed_at"] is None
    assert sum(item["status"] == "reused" for item in revision["items"]) == 6
    dispatch_pending(client)
    assert len(gateway.image_calls) == 8  # No work until the new approval.
    payload = deepcopy(revision["plan"]["payload"])
    payload["pages"][6]["image_prompt"] += " User-requested page correction."
    payload["refined_approval"]["document_markdown"] = refined_detail.approval_document(
        payload
    )
    payload["refined_approval"]["document_sha256"] = refined_detail.approval_hash(
        payload
    )
    response = client.put(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{revision['id']}/plan",
        json={"expected_revision": 1, "payload": payload},
    )
    assert response.status_code == 200, response.text
    revision = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{revision['id']}"
    ).json()
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{revision['id']}/plan/confirm",
        headers=idem(),
        json=confirmation(revision),
    )
    assert response.status_code == 202, response.text
    dispatch_pending(client)
    final = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{revision['id']}"
    ).json()
    assert final["status"] == "completed", {
        "status": final["status"],
        "items": final["items"],
    }
    assert len(gateway.image_calls) == 10 and len(gateway.planner_calls) == 1
    revised_archive = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{revision['id']}/deliverables.zip"
    )
    assert revised_archive.status_code == 200, revised_archive.text
    revised_zip = revised_archive.content
    with ZipFile(BytesIO(original_zip)) as old, ZipFile(BytesIO(revised_zip)) as new:
        for number in range(1, 7):
            filename = f"page-{number:03d}.png"
            assert old.read(filename) == new.read(filename)
    assert (
        client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()[
            "status"
        ]
        == "completed"
    )


def test_quick_remains_default_and_skill_cannot_switch_on_a_saved_job(client):
    source = upload_asset(client)
    body = {
        "type": "detail_pack",
        "intent": "draft",
        "display_name": "快速兼容测试",
        "input": {
            "product_groups": [{"group_key": "primary", "asset_ids": [source["id"]]}]
        },
        "configuration": {
            "product_name": "合成商品",
            "platform_code": "tmall",
            "market_region": "domestic",
            "page_count": 8,
        },
    }
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs", headers=idem(), json=body
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["configuration"]["settings"]["detail_skill"] == "quick"
    assert (
        job["configuration"]["domestic_detail_pipeline"]
        == "aurora-domestic-detail-v4.3.1"
    )
    changed = deepcopy(job["configuration"])
    changed["settings"]["detail_skill"] = "refined"
    response = client.patch(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}",
        json={"configuration": changed},
    )
    assert response.status_code == 409, response.text


def test_refined_cannot_be_combined_with_quick_contract_or_international_market(client):
    from pydantic import ValidationError

    from app.schemas_v1 import DetailPackConfiguration

    base = {
        "detail_skill": "refined",
        "market_region": "domestic",
        "platform_code": "tmall",
        "product_name": "测试商品",
        "page_count": 8,
    }
    for update in (
        {"website_contract_version": "WEBSITE_DIRECT_V2"},
        {"market_region": "international"},
    ):
        with pytest.raises(ValidationError):
            DetailPackConfiguration.model_validate(base | update)
