"""Scoped identities reach the real review adapter without extra image calls."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image

from app.aurora_detail_runtime import AuroraDetailHarnessRunner
from app.domestic_detail.atomic_review import synthetic_identity
from app.domestic_detail.review import approved_review_authority
from app.provider import ProviderRequest
from app.xinghe_agent import (
    FakeXingheGateway,
    PlannerCallResult,
    SourceImage,
    XingheAgentError,
    XingheImageProvider,
    _artifact_from_bytes,
    _planner_response_format,
    normalize_usage,
)
from tests.domestic_detail_atomic_fixtures import (
    attached_atomic_sources,
    create_atomic_context,
    review_context,
)
from tests.test_api import WORKSPACE_ID
from tests.test_domestic_detail_identity_scope import SCOPE_PRESENCE, authored_role
from tests.test_domestic_detail_plans import planned_job
from tests.test_domestic_detail_review_authority import compiled_review_session


@pytest.mark.parametrize(
    "roles,mode",
    [
        (roles, mode)
        for roles in [
            ("ABSENT", "ABSENT"),
            ("IDENTITY_TOKEN_ONLY", "ABSENT"),
            ("DOMINANT", "ABSENT"),
        ]
        for mode in [
            "complete",
            "missing",
            "uncertain",
            "false_preserved",
            "applicability_conflict",
            "atomic_unknown",
        ]
        if mode not in {"false_preserved", "applicability_conflict"}
        or roles[0] == "IDENTITY_TOKEN_ONLY"
    ],
)
def test_scoped_review_followup_is_bounded_truthful_and_reused(
    client, tmp_path, monkeypatch, roles, mode
):
    job = planned_job(client)
    monkeypatch.setattr(
        "app.xinghe_agent.detail_workspace",
        lambda job_id: tmp_path / "reviews" / job_id,
    )
    output = BytesIO()
    Image.new("RGB", (24, 64), "#778899").save(output, format="PNG")
    artifact = _artifact_from_bytes(
        output.getvalue(),
        filename="pair.png",
        expected_size="1440x3840",
        preserve_provider_artifact=True,
    )
    context_root = tmp_path / "scoped-pair"
    create_atomic_context(context_root, artifact)
    path = context_root / "session.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    for module, role in zip(state["modules"], roles, strict=True):
        authored_role(module["plan"], role)
    path.write_text(json.dumps(state), encoding="utf-8")
    native = review_context(context_root)
    authority = approved_review_authority(compiled_review_session())
    native.update(
        review_authority=authority,
        product_name=authority["product_name"],
        product_information=authority["product_information"],
    )
    reference = SourceImage(
        asset_id="original",
        group_key="primary",
        filename="original.png",
        content_type="image/png",
        sha256=artifact.sha256,
        payload=artifact.payload,
        source_role="product_reference",
    )
    sources = attached_atomic_sources(native, (reference,))
    scope = (
        "VISIBLE_COMPLETE_PRODUCT"
        if "DOMINANT" in roles
        else "VISIBLE_IDENTITY_TOKEN"
        if "IDENTITY_TOKEN_ONLY" in roles
        else "NO_VISIBLE_PRODUCT"
    )

    class Gateway(FakeXingheGateway):
        provider_name = "offline-scoped-identity-test"
        calls = []

        async def plan(self, request):
            self.calls.append(request)
            if request.planning_profile == "aurora_detail_pair_audit":
                raw = provider._native_detail_judgment(native, artifact)
                if mode in {"missing", "uncertain"}:
                    raw.pop("identity_silhouette")
                elif mode in {"false_preserved", "applicability_conflict"}:
                    raw.update(
                        status="MATERIAL_FAIL",
                        material_failures=[
                            {
                                "visible_evidence": "Observed token color drift"
                            }
                        ],
                    )
                    raw["identity_silhouette"].pop("visible_evidence")
                    raw["identity_silhouette"]["colorway_consistent"] = False
                elif mode == "atomic_unknown":
                    raw.pop("identity_silhouette")
                    raw["module_judgments"][0]["identity_silhouette"][
                        "shape_consistent"
                    ] = None
            else:
                assert request.planning_profile == "aurora_detail_identity_audit"
                supplied = json.loads(request.product_information[0])
                assert supplied["expected_observed_comparison_scope"] == scope
                assert len(supplied["module_identity_observations"]) == 2
                raw = {
                    **synthetic_identity(
                        SCOPE_PRESENCE[scope],
                        "Direct scoped identity comparison",
                    ),
                    "audit_complete": True,
                }
                if mode == "uncertain":
                    raw.update(
                        audit_complete=False,
                        shape_consistent=None,
                        uncertainty="Original feature is not visible",
                    )
                elif mode == "applicability_conflict":
                    raw["colorway_consistent"] = "NOT_APPLICABLE"
            return PlannerCallResult(
                payload=raw,
                provider_request_id=f"scoped-{len(self.calls)}",
                raw_usage={"total_tokens": 11},
                normalized_usage=normalize_usage({"total_tokens": 11}),
                api_latency_seconds=0.01,
                actual_cost_micros=1,
                audit_raw_content=json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            )

    gateway = Gateway()
    provider = XingheImageProvider(
        client.app.state.session_factory,
        gateway,
        storage=None,
        skill_root=AuroraDetailHarnessRunner().skill_root,
        max_concurrency=1,
        skill_id="aurora-domestic-ecommerce-detail-page",
    )
    request = ProviderRequest(
        workspace_id=WORKSPACE_ID,
        job_id=job["id"],
        job_item_id="scope-item",
        attempt_id="scope-attempt",
        operation="detail_pack",
        idempotency_key="scope-call",
        configuration={"group_key": "primary"},
    )
    error_expected = mode in {"uncertain", "applicability_conflict", "atomic_unknown"}

    async def invoke(attempt):
        return await provider._native_detail_judgment_with_review(
            replace(request, attempt_id=attempt), native, sources, artifact
        )

    if error_expected:
        with pytest.raises(XingheAgentError):
            asyncio.run(invoke("first"))
    else:
        result = asyncio.run(invoke("first"))
        identity = result["identity_silhouette"]
        assert identity["comparison_scope"] == scope
        if scope == "NO_VISIBLE_PRODUCT":
            assert all(
                identity[k] == "NOT_APPLICABLE"
                for k in (
                    "shape_consistent",
                    "colorway_consistent",
                    "texture_consistent",
                )
            )
        if mode == "false_preserved":
            assert identity["colorway_consistent"] is False
        assert len(result["review_provenance"]) == (1 if mode == "complete" else 2)
    expected_calls = 1 if mode in {"complete", "atomic_unknown"} else 2
    assert len(gateway.calls) == expected_calls
    if error_expected:
        with pytest.raises(XingheAgentError):
            asyncio.run(invoke("reclaimed"))
    else:
        assert asyncio.run(invoke("reclaimed")) == result
    assert len(gateway.calls) == expected_calls and gateway.image_calls == []


def test_provider_identity_schema_accepts_scoped_na_and_uncertainty():
    from jsonschema import Draft202012Validator

    from app.xinghe_agent import PlannerRequest

    request = PlannerRequest(
        workspace_id="offline",
        job_id="scope",
        group_key="primary",
        sources=(),
        page_count=2,
        language="zh-CN",
        product_name="synthetic",
        product_information=(),
        platform_code="domestic-detail",
        size="3x8",
        idempotency_key="scope",
        planning_profile="aurora_detail_identity_audit",
    )
    schema = _planner_response_format(request)["json_schema"]["schema"]
    assert "$ref" not in json.dumps(schema)
    absent = {
        **synthetic_identity(
            "ABSENT", "No product is visibly present in these actual fixed windows"
        ),
        "audit_complete": True,
    }
    assert not list(Draft202012Validator(schema).iter_errors(absent))
    unknown = {
        **absent,
        "audit_complete": False,
        "shape_consistent": None,
        "visible_evidence": "",
        "uncertainty": "Pixels do not resolve the needed original feature",
    }
    assert not list(Draft202012Validator(schema).iter_errors(unknown))
    unknown["audit_complete"] = True
    # The provider only accepts an object at the schema root. Cross-field
    # completeness is enforced by the server before any acceptance or repair.
    from app.domestic_detail.identity_review import merge_identity_followup
    from app.domestic_detail.review import DetailReviewError

    with pytest.raises(DetailReviewError, match="complete scoped comparison"):
        merge_identity_followup({}, unknown, "NO_VISIBLE_PRODUCT")
