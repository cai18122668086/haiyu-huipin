"""Optional campaign review is an explicitly authorized independent queue job."""

from __future__ import annotations

import json

import pytest

from app.aurora_detail_runtime import detail_workspace
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_delivery import (
    REVIEWS,
    explicit_synthetic_delivery,  # noqa: F401
    ready,
)
from tests.test_domestic_detail_execution import _gateway

AUTHORIZATION = (
    "我已审阅当前成图，授权另行进行一次可选整页视觉审核，并同意所列费用预留。"
)


def request_review(client, job, key=None, **changes):
    reserve = client.app.state.settings.tengli_planner_reserve_micros
    quote = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/campaign-review/quote",
        params={"final_review_sha256": REVIEWS[job["id"]]},
    )
    assert quote.status_code == 200, quote.text
    body = {
        "final_review_sha256": REVIEWS[job["id"]],
        "approval_text": AUTHORIZATION,
        "authorized_reserve_micros": reserve,
        "quote_sha256": quote.json()["sha256"],
    }
    body.update(changes)
    return client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job['id']}/campaign-review",
        headers=key or idem(),
        json=body,
    )


def test_create_campaign_review_binds_one_queued_operation_and_preserves_source(client):
    source = ready(client)
    root = detail_workspace(source["id"])
    original_session = (root / "session.json").read_bytes()
    original_job = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source['id']}"
    ).json()
    gateway = _gateway(client)
    before = len(gateway.planner_calls), len(gateway.image_calls)
    key = idem()
    response = request_review(client, source, key)
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["type"] == "domestic_detail_review" and queued["status"] == "queued"
    assert queued["expected_item_count"] == 1 and len(queued["items"]) == 1
    assert queued["assets"] == [] and queued["output_asset_count"] == 0
    assert queued["configuration"]["source_job_id"] == source["id"]
    assert queued["configuration"]["final_review_sha256"] == REVIEWS[source["id"]]
    assert queued["configuration"]["approval_text"] == AUTHORIZATION
    assert (
        int(queued["cost"]["estimated_max"]["micros"])
        == client.app.state.settings.tengli_planner_reserve_micros
    )
    assert request_review(client, source, key).json()["id"] == queued["id"]
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before
    assert (root / "session.json").read_bytes() == original_session
    assert (
        client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source['id']}").json()
        == original_job
    )
    assert json.loads(original_session)["state"] == "AWAITING_USER_FINAL_REVIEW"


def synthetic_campaign_observation(context, *, boilerplate=False):
    """Explicit synthetic protocol observations; never real visual acceptance."""
    plan = context["approved_plan"]
    contract = context["native_contract"]
    ids = [row["id"] for row in plan["modules"]]
    evidence = (
        "Synthetic M01 upper-left navy area x=20,y=40; "
        "M02 lower edge region y=1880 has a separate light band."
    )
    checks = {
        name: {
            "observation_status": "OBSERVED",
            "pass": True,
            "visible_evidence": evidence
            if boilerplate
            else (
                f"Synthetic {name}: M{index % len(ids) + 1:02d} upper-left block "
                f"x={20 + index * 7}, y={40 + index * 3}; a separate lower-edge "
                f"band runs through x={80 + index * 2}."
            ),
        }
        for index, name in enumerate(contract["required_checks"])
    }
    classes = [
        "COMPLETE_PRODUCT",
        "HUMAN",
        "PRODUCT_DETAIL",
        "INGREDIENT_OR_MATERIAL",
        "MECHANISM",
        "ACTION",
        "RESULT_STATE",
        "DOCUMENT_EVIDENCE",
        "SPECIFICATION_INFORMATION",
        "ENVIRONMENT_SCENE",
        "BODY_OR_SKIN",
        "PRODUCT_DETAIL",
    ]
    forms = [
        "HERO_POSTER",
        "MULTI_PAIN_ARRAY",
        "WHOLE_PLUS_MACRO",
        "EXPLODED_STRUCTURE",
        "PROCESS_SEQUENCE",
        "SOURCE_ACTION_RESULT",
        "STATE_CONTRAST",
        "DOCUMENTARY_EVIDENCE",
        "SPECIFICATION_BOARD",
        "ASYMMETRIC_EDITORIAL",
        "USE_SCOPE_MATRIX",
        "FAQ_BOARD",
    ]
    observed = []
    for index, module_id in enumerate(ids):
        role = "DOMINANT" if index == 0 else "SUPPORTING"
        signature = f"{classes[index]}+{forms[index]}+medium+{role}"
        observed.append(
            {
                "module_id": module_id,
                "primary_subject_class": classes[index],
                "presentation_form": forms[index],
                "camera_scale": "medium",
                "complete_product_role": role,
                "expression_signature": signature,
                "visible_evidence": evidence,
            }
        )
    pairs = plan["output_strategy"]["continuous_pair_rendering"]["pair_ids"]
    by_id = {m["id"]: m for m in plan["modules"]}
    pair_audits = []
    for pair in pairs:
        method = by_id[pair["module_ids"][0]]["transition_contract"]["method_id"]
        seam = {
            "method_id": method,
            "source_region": "lower edge y=1880",
            "target_region": "upper edge y=40",
            "carrier": "synthetic navy band x=40",
            "geometry": "band extends across x=40..120 at the fixed y=1920 boundary",
            "hard_cut_absent": True,
            "visible_evidence": evidence,
        }
        for key in (
            "crosses_boundary",
            "state_relation_visible",
            "direction_continuous",
            "perspective_direction_continuous",
            "scale_relation_visible",
            "shared_field_continuous",
            "shared_token_visible",
            "carrier_crosses_boundary",
            "intentional_transition",
        ):
            seam[key] = True
        pair_audits.append(
            {
                **pair,
                "internal_seam_method_id": method,
                "internal_seam_visible": True,
                "duplicate_content_absent": True,
                "internal_seam_evidence": seam,
                "visible_evidence": evidence,
            }
        )
    tokens = plan["output_strategy"]["continuous_pair_rendering"][
        "pair_to_pair_transition_tokens"
    ]
    transitions = []
    for index, (left, right) in enumerate(zip(pairs, pairs[1:], strict=False)):
        transitions.append(
            {
                "from_pair_id": left["pair_id"],
                "to_pair_id": right["pair_id"],
                "module_ids": [left["module_ids"][-1], right["module_ids"][0]],
                "method_id": by_id[left["module_ids"][-1]]["transition_contract"][
                    "method_id"
                ],
                "transition_token": tokens[index],
                "shared_token_visible": True,
                "hard_separator_absent": True,
                "source_region": "lower edge y=1880",
                "target_region": "upper edge y=40",
                "carrier": "synthetic navy edge band",
                "visible_evidence": evidence,
            }
        )
    claims = plan.get("selling_point_depth_contract", {}).get("primary_claim_ids", [])
    return {
        "status": "PASS",
        "audit_complete": True,
        "artifact_binding": {
            **context["artifact_binding"],
            "ocr_text": [],
            "visual_inspection_output": evidence,
        },
        "checks": checks,
        "pair_audits": pair_audits,
        "pair_transition_audits": transitions,
        "expression_diversity_audit": {
            "module_observations": observed,
            "distinct_subject_classes": len(
                {r["primary_subject_class"] for r in observed}
            ),
            "distinct_presentation_forms": len(
                {r["presentation_form"] for r in observed}
            ),
            "complete_product_dominant_module_ids": [ids[0]],
            "maximum_consecutive_same_signature": 1,
            "actual_pixels_pass": True,
        },
        "claim_fulfillment": [
            {
                "claim_id": claim,
                "overview_visible": True,
                "dedicated_visible": True,
                "use_or_result_visible": True,
                "recall_visible": True,
                "observed_module_ids": [ids[0], ids[2], ids[-1]],
                "visible_evidence": evidence,
            }
            for claim in claims
        ],
    }


@pytest.mark.parametrize("boilerplate", [False, True])
def test_queued_campaign_review_runs_once_with_original_reply_and_no_image_call(
    client, monkeypatch, boilerplate
):
    from app.xinghe_agent import PlannerCallResult, normalize_usage

    source = ready(client)
    original = (detail_workspace(source["id"]) / "session.json").read_bytes()
    gateway = _gateway(client)
    original_plan = gateway.plan
    calls = []

    async def observed_review(request):
        if request.planning_profile != "aurora_detail_campaign_audit":
            return await original_plan(request)
        calls.append(request)
        gateway.planner_calls.append(request)
        observation = synthetic_campaign_observation(
            json.loads(request.product_information[0]), boilerplate=boilerplate
        )
        return PlannerCallResult(
            payload=observation,
            provider_request_id="synthetic-campaign-review",
            raw_usage={"input_tokens": 9, "output_tokens": 8},
            normalized_usage=normalize_usage({"input_tokens": 9, "output_tokens": 8}),
            api_latency_seconds=0.01,
            actual_cost_micros=17,
            audit_raw_content=json.dumps(observation, ensure_ascii=False),
        )

    monkeypatch.setattr(gateway, "plan", observed_review)
    before = len(gateway.image_calls)
    queued = request_review(client, source)
    assert queued.status_code == 202, queued.text
    review_id = queued.json()["id"]
    dispatch_pending(client)
    completed = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}").json()
    assert completed["status"] == ("failed" if boilerplate else "completed"), completed
    assert len(calls) == 1 and len(gateway.image_calls) == before
    assert completed["output_asset_count"] == 0 and completed["assets"] == []
    assert completed["cost"]["actual"]["micros"] == "17"
    assert completed["cost"]["usage_completeness"] == "complete"
    root = detail_workspace(review_id)
    report = json.loads((root / "campaign-report.json").read_bytes())
    assert report["native_gate_accepted"] is (not boilerplate)
    assert report["residual_checks"] == []
    if not boilerplate:
        assert (
            json.loads((root / "session.json").read_bytes())["long_page_audit"]
            == report["judgment"]
        )
    assert len(list((root / "reviews").glob("*.reply.txt"))) == 1
    report_response = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}/campaign-review/report"
    )
    assert report_response.status_code == 200, report_response.text
    assert report_response.json()["report"] == report
    assert report_response.json()["original_message_state"] == "PRESERVED_UTF8"
    assert json.loads(report_response.json()["original_message"]) == report["judgment"]
    preview = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}/campaign-review/preview.png"
    )
    assert (
        preview.status_code == 200
        and preview.content == (root / "long-page.png").read_bytes()
    )

    if not boilerplate:
        evidence = [
            root / "campaign-report.json",
            root / "campaign-request-context.json",
            root / "source-session.json",
            root / "audited-session.json",
            root / "session.json",
            root / "long-page.png",
            next((root / "model-inputs").iterdir()),
            next((root / "reviews").glob("*.request.json")),
            next((root / "reviews").glob("*.response.json")),
            next((root / "reviews").glob("*.reply.txt")),
        ]
        for path in evidence:
            saved = path.read_bytes()
            try:
                path.write_bytes(b"changed-original-evidence")
                invalid = client.get(
                    f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}/campaign-review/report"
                )
                assert invalid.status_code == 409, (path.name, invalid.text)
            finally:
                path.write_bytes(saved)
        restored = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}/campaign-review/report"
        )
        assert restored.status_code == 200, restored.text

    dispatch_pending(client)
    assert len(calls) == 1 and len(gateway.image_calls) == before
    assert (detail_workspace(source["id"]) / "session.json").read_bytes() == original
    assert (
        client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{source['id']}").json()[
            "status"
        ]
        == "awaiting_final_review"
    )


def test_unknown_campaign_request_cannot_be_rebilled(client, monkeypatch):
    from sqlalchemy import select

    from app.models import JobAttemptRecord

    source = ready(client)
    original = (detail_workspace(source["id"]) / "session.json").read_bytes()
    gateway = _gateway(client)
    original_plan = gateway.plan
    calls = []

    async def timeout_review(request):
        if request.planning_profile != "aurora_detail_campaign_audit":
            return await original_plan(request)
        calls.append(request)
        raise TimeoutError("Synthetic result unknown after dispatch")

    monkeypatch.setattr(gateway, "plan", timeout_review)
    before = len(gateway.image_calls)
    response = request_review(client, source)
    assert response.status_code == 202, response.text
    review_id = response.json()["id"]
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}").json()
    assert current["status"] == "failed", current
    assert current["items"][0]["error_code"] == "PROVIDER_OUTCOME_UNKNOWN"
    assert len(calls) == 1 and len(gateway.image_calls) == before
    report = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}/campaign-review/report"
    )
    assert report.status_code == 200, report.text
    assert report.json()["report"]["native_gate_accepted"] is False
    assert report.json()["original_message_state"] == "UNAVAILABLE"
    recovery_quote = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}/campaign-review/recovery-quote"
    )
    assert recovery_quote.status_code == 409, recovery_quote.text
    assert len(calls) == 1
    repeated = request_review(client, source)
    assert repeated.status_code == 409, repeated.text
    assert repeated.json()["error"]["code"] == "NATIVE_DETAIL_CAMPAIGN_OUTCOME_UNKNOWN"
    for endpoint, body in (("retry", {}), ("clone", {})):
        rejected = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}/{endpoint}",
            headers=idem(),
            json=body,
        )
        assert rejected.status_code == 409, rejected.text
    root = detail_workspace(review_id)
    requests = root / "reviews"
    moved = root / "reviews-unavailable"
    requests.rename(moved)
    try:
        with client.app.state.session_factory() as db:
            attempt = db.scalar(
                select(JobAttemptRecord).where(JobAttemptRecord.job_id == review_id)
            )
            assert attempt.status == "outcome_unknown"
        rejected = request_review(client, source)
        assert rejected.status_code == 409, rejected.text
        assert (
            rejected.json()["error"]["code"] == "NATIVE_DETAIL_CAMPAIGN_OUTCOME_UNKNOWN"
        )
    finally:
        moved.rename(requests)
    dispatch_pending(client)
    assert len(calls) == 1 and len(gateway.image_calls) == before
    assert (detail_workspace(source["id"]) / "session.json").read_bytes() == original


def test_review_quote_authorization_active_and_cancel_guards(client):
    from sqlalchemy import func, select

    from app.models import JobRecord

    source = ready(client)
    gateway = _gateway(client)
    before = len(gateway.planner_calls), len(gateway.image_calls)
    with client.app.state.session_factory() as db:
        count = db.scalar(select(func.count(JobRecord.id)))
    for changes, expected in (
        ({"authorized_reserve_micros": 1}, 409),
        ({"authorized_reserve_micros": True}, 422),
        ({"quote_sha256": "a" * 64}, 409),
        ({"final_review_sha256": "b" * 64}, 409),
        ({"approval_text": "  "}, 409),
    ):
        response = request_review(client, source, **changes)
        assert response.status_code == expected, response.text
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count(JobRecord.id))) == count
    key = idem()
    response = request_review(client, source, key)
    assert response.status_code == 202, response.text
    review_id = response.json()["id"]
    active = request_review(client, source)
    assert active.status_code == 409, active.text
    assert active.json()["error"]["code"] == "NATIVE_DETAIL_CAMPAIGN_ALREADY_ACTIVE"
    altered = request_review(client, source, key, approval_text=AUTHORIZATION + "改动")
    assert altered.status_code == 409, altered.text
    cancelled = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}/cancel",
        headers=idem(),
        json={"reason": "Synthetic pre-dispatch cancellation"},
    )
    assert cancelled.status_code in (200, 202), cancelled.text
    dispatch_pending(client)
    current = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{review_id}").json()
    assert current["status"] == "cancelled", current
    assert (len(gateway.planner_calls), len(gateway.image_calls)) == before
