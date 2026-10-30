from uuid import uuid4

import pytest

from app import services
from app.credit_billing import image_unit_credits
from app.models import ReplicaDocumentRecord
from tests.test_customer_credit_billing import WORKSPACE, seed_job


def test_published_inspiration_quote_matches_reservation(client):
    from app.models import InspirationCaseRecord
    from tests.test_api import upload_asset

    asset = upload_asset(client)
    case_id = "ins_" + uuid4().hex
    with client.app.state.session_factory() as session:
        session.add(
            InspirationCaseRecord(
                id=case_id,
                title="测试商品灵感",
                description="合成商品场景",
                role="电商运营",
                type="卖点图",
                category="测试",
                platform="国内",
                ratio="1:1",
                intent="展示真实商品",
                prompt="保持商品原貌，呈现真实材质",
                workflow_type="product_prompt",
                reference_source="none",
                configuration_status="ready",
                status="published",
                created_by_user_id="usr_test",
                updated_by_user_id="usr_test",
            )
        )
        session.commit()
    payload = {
        "type": "creative_image",
        "inspiration_case_id": case_id,
        "input": {"product_asset_ids": [asset["id"]]},
        "configuration": {
            "image_count": 2,
            "inspiration_planning": True,
            "instruction": "保持商品原貌，呈现真实材质",
        },
    }
    quote = client.post(f"/api/v1/workspaces/{WORKSPACE}/credits/quote", json=payload)
    assert quote.status_code == 200, quote.text
    assert quote.json()["estimated_credits"] == 10
    created = client.post(
        f"/api/v1/workspaces/{WORKSPACE}/jobs",
        headers={"Idempotency-Key": str(uuid4())},
        json=payload,
    )
    assert created.status_code == 202, created.text
    settlement = client.get(
        f"/api/v1/workspaces/{WORKSPACE}/credits/jobs/{created.json()['id']}"
    )
    assert settlement.status_code == 200
    assert settlement.json()["reserved_credits"] == 10


def test_future_membership_and_expired_membership_are_distinct(client):
    from datetime import timedelta

    from app.credit_memberships import add_months
    from app.models import CreditMembershipRecord, utc_now

    now = utc_now()
    with client.app.state.session_factory() as session:
        session.add(
            CreditMembershipRecord(
                id="mem_status_test",
                workspace_id=WORKSPACE,
                plan_id="pro",
                plan_name="专业版",
                monthly_credits=1500,
                months=1,
                starts_at=now + timedelta(days=1),
                ends_at=add_months(now, 1),
            )
        )
        session.commit()
    response = client.get(f"/api/v1/workspaces/{WORKSPACE}/credits/membership")
    assert response.status_code == 200
    assert response.json()["membership_status"] == "scheduled"
    assert response.json()["plan_name"] is None
    assert response.json()["next_plan_name"] == "专业版"
    with client.app.state.session_factory() as session:
        row = session.get(CreditMembershipRecord, "mem_status_test")
        row.starts_at = now - timedelta(days=65)
        row.ends_at = now - timedelta(days=30)
        session.commit()
    response = client.get(f"/api/v1/workspaces/{WORKSPACE}/credits/membership")
    assert response.json()["membership_status"] == "expired"
    assert response.json()["plan_name"] is None
    assert response.json()["previous_plan_name"] == "专业版"


def test_long_reference_stops_before_spending_beyond_budget(client):
    import asyncio

    from sqlalchemy import func, select

    from app.models import JobRecord
    from app.replica_auto_start import submit_automatic

    document_id = "rep_" + uuid4().hex
    with client.app.state.session_factory() as session:
        session.add(
            ReplicaDocumentRecord(
                id=document_id,
                workspace_id=WORKSPACE,
                created_by="usr_test",
                kind="session",
                fingerprint=uuid4().hex * 2,
                status="awaiting_confirmation",
                payload={
                    "auto_start_after_plan": True,
                    "analysis_attempt_id": "attempt",
                    "_automatic_submission": {"version": "replica-submission-v1"},
                    "replica_speed": "fast",
                    "credit_budget": 8,
                    "_fast_plan": {"units": [{"id": "one"}, {"id": "two"}]},
                },
            )
        )
        session.commit()
    asyncio.run(submit_automatic(client.app, document_id, "attempt"))
    with client.app.state.session_factory() as session:
        row = session.get(ReplicaDocumentRecord, document_id)
        assert row.payload["auto_start_after_plan"] is False
        assert row.payload["credit_budget_exceeded"] is True
        assert row.payload["credit_quote"]["estimated_credits"] == 16
        assert session.scalar(select(func.count()).select_from(JobRecord)) == 0


@pytest.mark.parametrize(
    "kind,mode_settings,count,total",
    [
        ("domestic_main_image", {"main_image_speed": "native_fast"}, 6, 48),
        ("detail_pack", {"detail_speed": "fast"}, 8, 64),
        ("amazon_visual_suite", {"amazon_speed": "fast"}, 21, 168),
        (
            "reference_remix",
            {"reference_skill": "aurora-ecommerce-reference-remix-fast"},
            8,
            64,
        ),
        (
            "reference_remix",
            {"reference_skill": "all-category-style-benchmark-still-fast"},
            8,
            64,
        ),
    ],
)
def test_fast_reservation_and_partial_settlement(
    client, kind, mode_settings, count, total
):
    with client.app.state.session_factory() as session:
        job, items = seed_job(
            session, kind=kind, count=count, configuration={"settings": mode_settings}
        )
        hold = services.reserve_image_credits(session, WORKSPACE, job)
        assert hold.reserved_credits == total
        for index, item in enumerate(items):
            item.status = "succeeded" if index == 0 else "failed"
        session.flush()
        services.settle_credits(session, job, 0)
        assert job.actual_credits == 8
        assert (hold.reserved_credits - hold.settled_credits) == total - 8


def test_inspiration_tariff_requires_server_bound_provenance():
    assert (
        image_unit_credits(
            "creative_image", {"settings": {"inspiration_case_id": "forged"}}
        )
        == 15
    )
    assert (
        image_unit_credits(
            "creative_image",
            {
                "inspiration_case_id": "case",
                "inspiration_workflow": "product_prompt",
                "settings": {},
            },
        )
        == 5
    )


@pytest.mark.parametrize(
    "kind,settings,expected",
    [
        ("creative_image", {"detail_speed": "fast"}, 15),
        ("detail_pack", {"main_image_speed": "native_fast"}, 15),
        ("amazon_visual_suite", {"detail_speed": "fast"}, 15),
        ("domestic_main_image", {"amazon_speed": "fast"}, 15),
        ("local_edit", {"detail_speed": "fast"}, 5),
    ],
)
def test_unrelated_speed_fields_cannot_change_a_tools_tariff(kind, settings, expected):
    assert image_unit_credits(kind, {"settings": settings}) == expected


def test_existing_v1_reservation_keeps_original_price(client):
    with client.app.state.session_factory() as session:
        job, items = seed_job(session, count=3)
        hold = services.reserve_image_credits(session, WORKSPACE, job)
        hold.pricing_snapshot = {
            **hold.pricing_snapshot,
            "version": "image-credits-5-15-v1",
        }
        job.configuration = {"settings": {"detail_speed": "fast"}}
        for index, item in enumerate(items):
            item.status = "succeeded" if index == 0 else "failed"
        session.flush()
        services.settle_credits(session, job, 0)
        assert job.actual_credits == 15
        assert (hold.reserved_credits - hold.settled_credits) == 30


def test_counter_includes_old_active_tasks_and_deduplicates_replica(client):
    with client.app.state.session_factory() as session:
        jobs = [seed_job(session, count=1, deliverables=False)[0] for _ in range(25)]
        session.add(
            ReplicaDocumentRecord(
                id="rep_" + uuid4().hex,
                workspace_id=WORKSPACE,
                created_by="usr_test",
                kind="session",
                fingerprint=uuid4().hex * 2,
                status="generating",
                payload={
                    "replication_method": "exact",
                    "job_ids": [jobs[0].id],
                    "progress": 10,
                },
            )
        )
        session.commit()
    result = client.get(f"/api/v1/workspaces/{WORKSPACE}/creation-summary")
    assert result.status_code == 200, result.text
    assert result.json()["active"] == result.json()["total"] == 25
    assert client.get("/api/v1/workspaces/ws_other/creation-summary").status_code == 404
