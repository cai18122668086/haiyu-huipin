"""Explicit website automatic authorization and legacy API compatibility."""

import asyncio

from app.task_runner import MockTaskRunner
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem, upload_asset
from tests.test_p0_amazon_fail_closed import _CountingGateway


def test_crossborder_explicit_website_submission_delivers_without_manual_confirmation(
    client,
):
    gateway = _CountingGateway()
    client.app.state.settings.crossborder_detail_provider_enabled = True
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    client.app.state.task_runner = MockTaskRunner(
        client.app.state.session_factory,
        0,
        storage=client.app.state.storage,
        xinghe_gateway=gateway,
        crossborder_detail_provider_enabled=True,
    )
    source = upload_asset(client)
    payload = {
        "type": "detail_pack",
        "intent": "submit",
        "display_name": "Cross-border automatic website",
        "input": {
            "product_groups": [{"group_key": "primary", "asset_ids": [source["id"]]}]
        },
        "configuration": {
            "product_name": "Synthetic product",
            "selling_points": ["Verified visible structure"],
            "market_region": "international",
            "platform_code": "amazon",
            "language": "en-US",
            "page_count": 2,
            "auto_start_after_plan": True,
        },
    }
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs", headers=idem(), json=payload
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert "automatic_crossborder_submission" in response.json()["configuration"]
    dispatch_pending(client)
    result = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}").json()
    assert result["status"] == "completed", result
    assert (
        result["plan"]["payload"]["crossborder_final_manifest"][
            "manual_review_performed"
        ]
        is False
    )
    assert len(gateway.image_calls) == 2
    asyncio.run(client.app.state.task_runner.run(job_id))
    assert len(gateway.image_calls) == 2
    del payload["configuration"]["auto_start_after_plan"]
    legacy = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs", headers=idem(), json=payload
    )
    assert legacy.status_code == 202, legacy.text
    assert "automatic_crossborder_submission" not in legacy.json()["configuration"]
    dispatch_pending(client)
    legacy_result = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{legacy.json()['id']}"
    ).json()
    assert legacy_result["status"] == "awaiting_confirmation", legacy_result
    assert len(gateway.image_calls) == 2
