"""Only unchanged, user-requested failed retries inherit submission delivery."""

from copy import deepcopy
from types import SimpleNamespace as Obj

import pytest

from app.domestic_detail.delivery_authorization import (
    SUBMISSION_SOURCE,
    SUBMISSION_TEXT,
    job_receipt_authorization,
    submission_authorization_quote,
)
from app.domestic_detail.plans import DetailPlanError
from app.models import JobPlanRecord
from tests.test_domestic_detail_automatic_delivery import contract, quote_arguments


@pytest.mark.parametrize("pair_status", ["MATERIAL_FAIL", "PASS"])
def test_failed_aggregate_scope_preserves_comparisons_and_original_reply(pair_status):
    from app.domestic_detail.website_review import project_failed_scope

    raw = {
        "status": pair_status,
        "material_failures": [{"visible_evidence": "Printed package text differs"}],
        "identity_silhouette": {
            "comparison_scope": "VISIBLE_COMPLETE_PRODUCT",
            "shape_consistent": False,
        },
        "module_judgments": [
            {
                "status": "MATERIAL_FAIL",
                "material_failures": [{"visible_evidence": "Package text differs"}],
                "observations": {"product_presence_observed": "IDENTITY_TOKEN_ONLY"},
                "identity_silhouette": {
                    "comparison_scope": "VISIBLE_IDENTITY_TOKEN",
                    "shape_consistent": False,
                },
            }
            for _ in range(2)
        ],
    }
    before = deepcopy(raw)
    result = project_failed_scope(raw)
    assert raw == before
    assert result["identity_silhouette"]["shape_consistent"] is False
    expected = (
        "VISIBLE_IDENTITY_TOKEN"
        if pair_status == "MATERIAL_FAIL"
        else "VISIBLE_COMPLETE_PRODUCT"
    )
    assert result["identity_silhouette"]["comparison_scope"] == expected


@pytest.mark.parametrize(
    "tamper",
    [
        None,
        "earlier_intake_failure",
        "clone",
        "request",
        "workspace",
        "input",
        "plan",
        "cycle",
        "manual",
    ],
)
def test_retry_authority_checks_original_plan_and_real_retry_lineage(
    tamper, monkeypatch
):
    payload = contract()
    quote = submission_authorization_quote(**quote_arguments(payload))
    payload["aurora_v4_3"]["approval_receipt"] = {
        "approval_text": SUBMISSION_TEXT,
        "confirmation_source": SUBMISSION_SOURCE,
        "actor_user_id": "usr_contract",
        "plan_revision": 3,
        "automatic_delivery_authorization": quote,
    }
    origin = Obj(
        id="plan_contract",
        job_id="job_contract",
        workspace_id="ws_contract",
        revision=3,
        confirmed_at=True,
        validation_status="valid",
        payload=deepcopy(payload),
    )
    parent = Obj(
        id="job_contract",
        workspace_id="ws_contract",
        type="detail_pack",
        configuration={"input": {"a": 1}},
    )
    child = Obj(
        id="job_child",
        workspace_id="ws_contract",
        type="detail_pack",
        configuration=deepcopy(parent.configuration),
        retry_scope="failed",
        retry_of_job_id=parent.id,
        root_job_id=parent.id,
    )
    plan = Obj(
        id="plan_child",
        workspace_id="ws_contract",
        revision=1,
        confirmed_at=True,
        validation_status="valid",
        payload=payload,
    )
    request = Obj()
    if tamper == "earlier_intake_failure":
        child.root_job_id = parent.root_job_id = "job_intake_failure"
    if tamper == "clone":
        child.retry_scope = "full"
    if tamper == "request":
        request = None
    if tamper == "workspace":
        parent.workspace_id = "ws_other"
    if tamper == "input":
        child.configuration = {"input": {"a": 2}}
    if tamper == "plan":
        payload["source_input_sha256"] = "e" * 64
    if tamper == "cycle":
        child.retry_of_job_id = child.id
    if tamper == "manual":
        payload["aurora_v4_3"]["approval_receipt"]["confirmation_source"] = "manual"
    db = Obj(
        get=lambda model, key: (
            origin
            if model is JobPlanRecord
            else ({parent.id: parent, child.id: child}.get(key))
        ),
        scalar=lambda query: request,
    )
    if tamper and tamper != "earlier_intake_failure":
        with pytest.raises(DetailPlanError):
            job_receipt_authorization(db, child, plan)
    else:
        # Deploying a repair does not rewrite historical submission consent.
        monkeypatch.setattr(
            "app.domestic_detail.generation.runtime_source_hash", lambda: "f" * 64
        )
        assert job_receipt_authorization(db, child, plan) == quote
