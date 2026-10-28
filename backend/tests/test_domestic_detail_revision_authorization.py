"""Revision quotes bind exact source truth and never dispatch or reserve usage."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.aurora_detail_runtime import detail_workspace
from app.domestic_detail.planner_dispatch import select_route
from app.domestic_detail.plans import DetailPlanError, canonical_hash
from app.domestic_detail.revision_authorization import (
    REVISION_APPROVAL_TEXT,
    authorize_revision_quote,
    image_dispatch,
    normalize_adjustment,
    quote_value,
)
from app.models import (
    AssetRecord,
    AuditEventRecord,
    CreditReservationRecord,
    JobAssetRecord,
    JobAttemptRecord,
    JobItemRecord,
    JobPlanRecord,
    JobRecord,
    OutboxEventRecord,
    UsageEventRecord,
)
from app.schemas_v1 import DomesticDetailLocalRevisionQuote
from app.xinghe_agent import FakeXingheGateway, TengliOpenAICompatibleGateway
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_automatic_delivery import authorize_body
from tests.test_domestic_detail_delivery import (  # noqa: F401
    REVIEWS,
    explicit_synthetic_delivery,
    ready,
)
from tests.test_domestic_detail_execution import _gateway
from tests.test_domestic_detail_plans import planned_job
from tests.test_native_detail_local_adjustment import adjustment, inventory


def quote_fixture(count=8, module_id="M02"):
    gateway = FakeXingheGateway()
    modules = [{"id": f"M{i + 1:02d}"} for i in range(count)]
    pairs = [
        {
            "pair_id": f"P{i // 2 + 1:02d}",
            "module_ids": [modules[i]["id"], modules[i + 1]["id"]],
        }
        for i in range(0, count, 2)
    ]
    screens = [
        {
            "module_id": module["id"],
            "asset_id": f"asset_{i}",
            "ordinal": i,
            "sha256": f"{i + 1:064x}",
            "width": 1440,
            "height": 1920,
        }
        for i, module in enumerate(modules)
    ]
    canvases = [
        {
            **pair,
            "asset_id": f"canvas_{i}",
            "sha256": "a" * 64,
            "width": 1440,
            "height": 3840,
            "pixel_lineage_sha256": "b" * 64,
        }
        for i, pair in enumerate(pairs)
    ]
    return {
        "source": JobRecord(
            id="job_source", workspace_id="ws_source", expected_item_count=count
        ),
        "plan_id": "plan_source",
        "plan_revision": 3,
        "plan_payload": {
            "native_approval": {
                "document_sha256": "c" * 64,
                "skill_content_hash": "d" * 64,
            },
            "aurora_v4_3": {
                "approval_receipt": {
                    "approval_text": "original exact plan approval",
                    "automatic_delivery_authorization": {
                        "delivery_mode": "AUTO_FINALIZE_AUTHORIZED"
                    },
                },
                "contract_binding": {"sha256": "e" * 64},
            },
        },
        "context": {"screens": screens, "generation_binding_sha256": "f" * 64},
        "state": {
            "modules": modules,
            "plan": {
                "output_strategy": {"continuous_pair_rendering": {"pair_ids": pairs}}
            },
        },
        "packet": {
            "pair_canvases": canvases,
            "sha256": "1" * 64,
            "quality_state": "WITH_RESIDUALS",
            "residual_issues": [
                {"module_id": "M01", "issues": ["original material residual"]}
            ],
        },
        "source_session_sha256": "2" * 64,
        "adjustment": adjustment(module_id),
        "actor_id": "usr_source",
        "image_route": image_dispatch(gateway),
        "review_route": select_route(gateway),
        "image_unit_micros": 90_000,
        "review_reserve_micros": 250_000,
        "pricing_version": "explicit-test-price-v1",
    }


@pytest.mark.parametrize(
    "count,module_id,expected_pair",
    [
        (8, "M01", "P01"),
        (8, "M02", "P01"),
        (8, "M08", "P04"),
        (10, "M10", "P05"),
        (12, "M11", "P06"),
        (12, "M12", "P06"),
    ],
)
def test_quote_maps_exact_pair_preserves_raw_delta_and_never_inherits_auto(
    count, module_id, expected_pair
):
    values = quote_fixture(count, module_id)
    values["adjustment"]["verbatim_instruction"] = (
        "  只修改此屏标题位置。\n保留原事实和另一屏。  "
    )
    before = deepcopy({k: v for k, v in values.items() if k != "source"})
    result = quote_value(**values)
    DomesticDetailLocalRevisionQuote.model_validate(result)
    assert result["pair_id"] == expected_pair
    assert (
        result["selected_module_id"] == module_id and len(result["source_screens"]) == 2
    )
    assert module_id in result["module_ids"]
    assert result["approved_screen_count"] == count
    assert result["maximum_cost_micros"] == 1_430_000
    assert (
        result["maximum_image_requests"],
        result["maximum_review_model_requests"],
        result["maximum_provider_requests"],
    ) == (2, 4, 7)
    assert (
        result["delivery_mode"] == "USER_FINAL_REVIEW"
        and "automatic_delivery_authorization" not in result
    )
    assert result["adjustment"] == values["adjustment"]
    assert result["source_residual_issues"] == before["packet"]["residual_issues"]
    assert {k: v for k, v in values.items() if k != "source"} == before
    assert result["sha256"] == canonical_hash(
        {k: v for k, v in result.items() if k != "sha256"}
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "none",
        "actor",
        "workspace",
        "source",
        "continue",
        "stale_quote",
        "runtime",
        "request_policy",
        "body_changed",
    ],
)
def test_revision_authority_requires_current_exact_quote_and_separate_human_intent(
    tamper, monkeypatch
):
    quote = quote_value(**quote_fixture())
    kwargs = dict(
        submitted_sha256=quote["sha256"],
        approval_text=REVISION_APPROVAL_TEXT,
        source_job_id="job_source",
        workspace_id="ws_source",
        actor_id="usr_source",
    )
    if tamper in {"actor", "workspace", "source"}:
        kwargs[
            {
                "actor": "actor_id",
                "workspace": "workspace_id",
                "source": "source_job_id",
            }[tamper]
        ] = "different_owner"
    elif tamper == "continue":
        kwargs["approval_text"] = "继续"
    elif tamper == "stale_quote":
        kwargs["submitted_sha256"] = "a" * 64
    elif tamper == "runtime":
        monkeypatch.setattr(
            "app.domestic_detail.revision_authorization.runtime_source_hash",
            lambda: "b" * 64,
        )
    elif tamper == "request_policy":
        monkeypatch.setattr(
            "app.domestic_detail.revision_authorization.revision_policy_hash",
            lambda: "c" * 64,
        )
    elif tamper == "body_changed":
        quote["maximum_cost_micros"] = 0
    if tamper != "none":
        with pytest.raises(DetailPlanError):
            authorize_revision_quote(quote, **kwargs)
    else:
        authority = authorize_revision_quote(quote, **kwargs)
        assert (
            authority["quote"] == quote
            and authority["approval_text"] == REVISION_APPROVAL_TEXT
        )
        assert authority["sha256"] == canonical_hash(
            {k: v for k, v in authority.items() if k != "sha256"}
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "true",
        "zero",
        "string_false",
        "global",
        "continue",
        "blank_duty",
        "extra",
    ],
)
def test_missing_or_global_delta_never_becomes_local_by_default(tamper):
    delta = adjustment("M02")
    if tamper == "missing":
        del delta["changes_facts"]
    elif tamper in {"true", "zero", "string_false"}:
        delta["changes_facts"] = {"true": True, "zero": 0, "string_false": "false"}[
            tamper
        ]
    elif tamper == "global":
        delta["change_scope"] = "global"
    elif tamper == "continue":
        delta["verbatim_instruction"] = "继续。"
    elif tamper == "blank_duty":
        delta["preserve"] = ["  "]
    elif tamper == "extra":
        delta["actor_id"] = "spoofed_actor"
    with pytest.raises(ValueError):
        normalize_adjustment(delta)


def test_quote_pins_the_actual_image_account_and_rejects_mixed_real_synthetic_review():
    gateway = TengliOpenAICompatibleGateway(
        api_key="not-a-live-key",
        base_url="https://offline.invalid",
        planner_model="review-model",
        image_model="image-model",
        image_api_key="original-image-key",
    )
    first = image_dispatch(gateway)
    assert (
        first["credential_sha256"] == hashlib.sha256(b"original-image-key").hexdigest()
    )
    assert first["model"] == "image-model" and "original-image-key" not in json.dumps(
        first
    )
    gateway._provider_routes["primary"] = replace(
        gateway._provider_routes["primary"], image_api_key="changed-image-key"
    )
    assert image_dispatch(gateway)["sha256"] != first["sha256"]
    values = quote_fixture()
    values["image_route"] = first
    with pytest.raises(DetailPlanError):
        quote_value(**values)
    assert gateway.external_network_request_count == 0


def database_snapshot(client):
    result = {}
    with client.app.state.session_factory() as db:
        for model in (
            JobRecord,
            JobPlanRecord,
            JobItemRecord,
            JobAssetRecord,
            JobAttemptRecord,
            AssetRecord,
            UsageEventRecord,
            CreditReservationRecord,
            AuditEventRecord,
            OutboxEventRecord,
        ):
            rows = [
                {
                    column.key: deepcopy(getattr(row, column.key))
                    for column in model.__table__.columns
                }
                for row in db.scalars(select(model)).all()
            ]
            result[model.__tablename__] = sorted(
                json.dumps(row, default=str, sort_keys=True) for row in rows
            )
    return result


@pytest.mark.parametrize("automatic", [False, True])
def test_actual_quote_is_read_only_and_source_actor_price_and_raw_bytes_are_bound(
    client, automatic
):
    if automatic:
        planned = planned_job(client)
        client.app.state.settings.max_image_pixels = 6_000_000
        client.app.state.storage.validator.max_image_pixels = 6_000_000
        prefix, _, body = authorize_body(client, planned)
        result = client.post(prefix + "/plan/confirm", headers=idem(), json=body)
        assert result.status_code == 202, result.text
        dispatch_pending(client)
        packet_response = client.post(prefix + "/detail-final-review")
        assert packet_response.status_code == 200, packet_response.text
        fingerprint = packet_response.json()["sha256"]
        job_id = planned["id"]
    else:
        source = ready(client)
        job_id, fingerprint = source["id"], REVIEWS[source["id"]]
        prefix = f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{job_id}"
    client.app.state.settings.provider_quota_enabled = True
    source_root = detail_workspace(job_id)
    original_files, original_db = inventory(source_root), database_snapshot(client)
    gateway = _gateway(client)
    counts = len(gateway.planner_calls), len(gateway.image_calls)
    body = {"final_review_sha256": fingerprint, "adjustment": adjustment("M02")}
    response = client.post(prefix + "/local-revision/quote", json=body)
    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote["pair_id"] == "P01" and quote["delivery_mode"] == "USER_FINAL_REVIEW"
    assert (
        quote["source_native_session_sha256"]
        == hashlib.sha256((source_root / "session.json").read_bytes()).hexdigest()
    )
    assert quote["workspace_id"] == WORKSPACE_ID and quote["source_job_id"] == job_id
    assert (
        quote["maximum_cost_micros"]
        == 2 * gateway.high_price_micros
        + 5 * client.app.state.settings.tengli_planner_reserve_micros
    )
    assert client.post(prefix + "/local-revision/quote", json=body).json() == quote
    current = dict(
        source_job_id=job_id, workspace_id=WORKSPACE_ID, actor_id=quote["actor_user_id"]
    )
    assert (
        authorize_revision_quote(
            quote,
            submitted_sha256=quote["sha256"],
            approval_text=REVISION_APPROVAL_TEXT,
            **current,
        )["quote_sha256"]
        == quote["sha256"]
    )
    gateway.high_price_micros += 1
    newer = client.post(prefix + "/local-revision/quote", json=body)
    assert newer.status_code == 200, newer.text
    assert newer.json()["sha256"] != quote["sha256"]
    if not automatic:
        session_file = source_root / "session.json"
        original_session = session_file.read_bytes()
        try:
            session_file.write_bytes(original_session + b"\n")
            raw_changed = client.post(prefix + "/local-revision/quote", json=body)
            assert raw_changed.status_code == 200, raw_changed.text
            assert (
                raw_changed.json()["source_native_session_sha256"]
                != newer.json()["source_native_session_sha256"]
            )
            assert raw_changed.json()["sha256"] != newer.json()["sha256"]
        finally:
            session_file.write_bytes(original_session)
    with pytest.raises(DetailPlanError):
        authorize_revision_quote(
            newer.json(),
            submitted_sha256=quote["sha256"],
            approval_text=REVISION_APPROVAL_TEXT,
            **current,
        )
    assert (
        inventory(source_root) == original_files
        and database_snapshot(client) == original_db
    )
    assert counts == (len(gateway.planner_calls), len(gateway.image_calls))
    malformed = deepcopy(body)
    malformed["adjustment"]["changes_facts"] = 0
    assert (
        client.post(prefix + "/local-revision/quote", json=malformed).status_code == 422
    )
    stale = {**body, "final_review_sha256": "f" * 64}
    assert client.post(prefix + "/local-revision/quote", json=stale).status_code == 409
    missing = {**body, "adjustment": adjustment("M12")}
    assert (
        client.post(prefix + "/local-revision/quote", json=missing).status_code == 409
    )
    assert (
        inventory(source_root) == original_files
        and database_snapshot(client) == original_db
    )
    assert counts == (len(gateway.planner_calls), len(gateway.image_calls))
