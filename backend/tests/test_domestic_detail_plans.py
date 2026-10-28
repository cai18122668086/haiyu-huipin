from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.domain import JobStatus
from app.domestic_detail.plans import (
    DetailPlanError,
    resolve_native_plan,
    validate_plan_views,
)
from app.models import JobRecord
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_domestic_detail_execution import (
    _aurora_approval_payload,
    _create_detail_job,
    _gateway,
)


@pytest.mark.parametrize("location", ["root", "analysis", "group_analysis"])
def test_duplicate_plan_authorities_must_agree(location: str) -> None:
    plan = {"modules": [{"id": "M01", "duty": "original"}]}
    payload = {"groups": [{"plan_protocol_fields": {"aurora_plan": plan}}]}
    changed = {"modules": [{"id": "M01", "duty": "changed"}]}
    if location == "root":
        payload["aurora_plan"] = changed
    elif location == "analysis":
        payload["analysis"] = {"aurora_plan": changed}
    else:
        payload["groups"][0]["analysis"] = {"aurora_plan": changed}
    with pytest.raises(DetailPlanError, match="disagree"):
        resolve_native_plan(payload)


def test_plan_resolution_preserves_data_and_detaches_mutation() -> None:
    plan = {"modules": [{"id": "M01", "duty": "original"}]}
    payload = {"aurora_plan": plan, "analysis": {"aurora_plan": deepcopy(plan)}}
    result = resolve_native_plan(payload)
    assert result == plan
    result["modules"][0]["duty"] = "changed"
    assert plan["modules"][0]["duty"] == "original"


def planned_job(client: TestClient) -> dict:
    source = upload_asset(client, filename="approval-authority-product.png")
    job = _create_detail_job(
        client,
        source["id"],
        market_region="domestic",
        auto_start_after_plan=False,
    )
    dispatch_pending(client)
    planned = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}").json()
    assert planned["status"] == JobStatus.AWAITING_CONFIRMATION.value
    return planned


def test_approval_exposes_locked_input_plan_and_skill_scope(client: TestClient) -> None:
    planned = planned_job(client)
    metadata = planned["plan"]["payload"]["aurora_v4_3"]
    binding = metadata["contract_binding"]
    assert binding["version"] == "domestic-detail-approval-v4"
    for field in [
        "native_plan_sha256",
        "screen_views_sha256",
        "settings_sha256",
        "skill_content_hash",
        "sha256",
    ]:
        assert len(binding[field]) == 64
    assert binding["sha256"] in metadata["approval_document_markdown"]
    assert _gateway(client).image_calls == []


def test_changed_product_input_cannot_use_an_old_approval(client: TestClient) -> None:
    planned = planned_job(client)
    with client.app.state.session_factory() as session:
        record = session.get(JobRecord, planned["id"])
        configuration = deepcopy(record.configuration)
        configuration["settings"]["product_name"] = (
            "different product after approval packet"
        )
        record.configuration = configuration
        session.commit()
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AURORA_APPROVAL_BINDING_INVALID"
    assert _gateway(client).image_calls == []


@pytest.mark.parametrize("change", ["document", "root_module", "group_module"])
def test_stale_or_conflicting_packets_cannot_be_confirmed(
    client: TestClient,
    change: str,
) -> None:
    planned = planned_job(client)
    from app.models import JobPlanRecord

    with client.app.state.session_factory() as session:
        plan = session.get(JobPlanRecord, planned["plan"]["id"])
        payload = deepcopy(plan.payload)
        if change == "document":
            payload["aurora_v4_3"]["approval_document_markdown"] += "changed"
        elif change == "root_module":
            payload["aurora_plan"] = deepcopy(
                payload["groups"][0]["plan_protocol_fields"]["aurora_plan"]
            )
            payload["aurora_plan"]["modules"][0]["duty"] = "different duty"
        else:
            payload["pages"][0]["title"] = "different display"
        plan.payload = payload
        session.commit()
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "AURORA_APPROVAL_BINDING_INVALID"
    assert _gateway(client).image_calls == []


@pytest.mark.parametrize("group_pages", [None, "not an array", 7])
def test_malformed_group_views_fail_closed(group_pages: object) -> None:
    payload = {
        "groups": [
            {
                "pages": group_pages,
                "plan_protocol_fields": {
                    "aurora_plan": {"modules": []},
                },
            }
        ],
        "pages": [],
    }
    with pytest.raises(DetailPlanError, match="ordered screen"):
        validate_plan_views(payload)
