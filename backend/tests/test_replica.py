from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.errors import ApiError
from app.justone import image_url, normalize_product, parse_product_link
from app.replica import ReplicaDocumentRecord


def upload(client, color):
    out = BytesIO()
    Image.new("RGB", (80, 90), color).save(out, "PNG")
    response = client.post(
        "/api/v1/workspaces/ws_test/assets",
        headers={"Idempotency-Key": str(uuid4())},
        files={"file": ("product.png", out.getvalue(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_market_links_and_bare_ids():
    assert parse_product_link("https://item.taobao.com/item.htm?id=123456789")[0:2] == (
        "taobao",
        "123456789",
    )
    assert parse_product_link("https://detail.1688.com/offer/123456789.html")[0:2] == (
        "1688",
        "123456789",
    )
    assert parse_product_link("https://item.jd.com/123456789.html")[0:2] == (
        "jd",
        "123456789",
    )
    assert parse_product_link(
        "https://npcitem.jd.hk/ware/view.action?wareId=123456789"
    )[0:2] == ("jd", "123456789")
    assert parse_product_link("123456789", "tmall")[0:2] == ("tmall", "123456789")
    with pytest.raises(ApiError):
        parse_product_link("123456789")
    for value in (
        "http://127.0.0.1/123.html",
        "https://item.taobao.com:bad/item.htm?id=123456789",
        "https://mobile.yangkeduo.com/goods.html?goods_id=123456789",
        "https://taobao.com.evil.test/item.htm?id=123456789",
        "https://u:p@item.taobao.com/item.htm?id=123456789",
    ):
        with pytest.raises(ApiError):
            parse_product_link(value)


def test_provider_image_groups_and_1688_top_level_gallery():
    data = {
        "item": {"offerTitle": "我的杯子"},
        "images": [
            {
                "fullPathImageURI": "https://cbu01.alicdn.com/a.jpg",
                "size220x220ImageURI": "https://cbu01.alicdn.com/a.220.jpg",
            }
        ],
        "desc": '<img src="//cbu01.alicdn.com/detail.jpg">',
        "sku": {"images": ["https://cbu01.alicdn.com/sku.jpg"]},
    }
    result = normalize_product(
        data, "1688", "123456789", "https://detail.1688.com/offer/123456789.html"
    )
    assert result["title"] == "我的杯子"
    assert result["imageUrls"] == ["https://cbu01.alicdn.com/a.jpg"]
    assert result["descImageUrls"] == ["https://cbu01.alicdn.com/detail.jpg"]
    assert result["skuImageUrls"] == ["https://cbu01.alicdn.com/sku.jpg"]
    data["images"].append({"fullPathImageURI": "https://cbu01.alicdn.com/sku.jpg"})
    data["mainPic"] = {
        "offerImgList": ["https://cbu01.alicdn.com/a.jpg"],
        "skuImages": [{"image": "https://cbu01.alicdn.com/sku.jpg"}],
    }
    result = normalize_product(data, "1688", "123456789", "https://detail.1688.com")
    assert result["imageUrls"] == ["https://cbu01.alicdn.com/a.jpg"]
    assert result["skuImageUrls"] == ["https://cbu01.alicdn.com/sku.jpg"]
    assert image_url("https://alicdn.com:bad/a.jpg") is None
    assert image_url("https://alicdn.com/description.html") is None
    assert image_url("http://localhost/image.jpg") is None
    assert image_url("https://alicdn.com.evil.test/image.jpg") is None


def test_paid_product_request_is_cached_and_workspace_owned(client, monkeypatch):
    calls = []

    def fake_fetch(token, platform, item_id, canonical, **options):
        calls.append(item_id)
        return {
            "title": "保温杯",
            "imageUrls": ["https://cbu01.alicdn.com/a.jpg"],
            "descImageUrls": [],
            "skuImageUrls": [],
        }

    monkeypatch.setattr("app.replica.fetch_product", fake_fetch)
    route = "/api/v1/workspaces/ws_test/replica/products"
    one = client.post(
        route, json={"url": "https://detail.1688.com/offer/123456789.html"}
    )
    two = client.post(
        route, json={"url": "https://detail.1688.com/offer/123456789.html"}
    )
    assert one.status_code == two.status_code == 202
    assert one.json()["id"] == two.json()["id"]
    assert calls == ["123456789"]
    document_id = one.json()["id"]
    assert (
        client.get(
            f"/api/v1/workspaces/ws_test/replica/documents/{document_id}"
        ).json()["status"]
        == "ready"
    )
    assert (
        client.get(
            f"/api/v1/workspaces/ws_other/replica/documents/{document_id}"
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{route}/{document_id}/import",
            json={"urls": ["https://cbu01.alicdn.com/other.jpg"]},
        ).status_code
        == 400
    )

    image = BytesIO()
    Image.new("RGB", (80, 90), "purple").save(image, "PNG")
    monkeypatch.setattr("app.replica._download", lambda url, maximum: image.getvalue())
    imported = client.post(
        f"{route}/{document_id}/import",
        json={"urls": ["https://cbu01.alicdn.com/a.jpg"]},
    )
    again = client.post(
        f"{route}/{document_id}/import",
        json={"urls": ["https://cbu01.alicdn.com/a.jpg"]},
    )
    assert imported.status_code == again.status_code == 200, imported.text
    assert imported.json()[0]["asset_id"] == again.json()[0]["asset_id"]


def test_analyse_confirm_generates_once_and_preserves_source_roles(client, monkeypatch):
    client.app.state.settings.reference_remix_provider_enabled = True
    product, reference = upload(client, "red"), upload(client, "blue")
    calls = []

    async def plan(request):
        calls.append(request)
        return SimpleNamespace(
            payload={
                "product_identity_lock": "preserve red product",
                "reference_visual_analysis": "composition only",
                "hard_composition_skeleton": "center",
                "original_remix_prompt": "Red product, reference layout.",
                "commercial_intent": "突出用户商品",
            },
            error_code=None,
            normalized_usage={"input_tokens": 20},
            actual_cost_micros=10,
        )

    client.app.state.replica_gateway = SimpleNamespace(plan=plan)
    route = "/api/v1/workspaces/ws_test/replica"
    body = {
        "request_id": str(uuid4()),
        "product_asset_ids": [product],
        "references": [{"asset_id": reference, "title": "主图", "aspect_ratio": "1:1"}],
        "facts": "我的品牌",
    }
    created = client.post(route + "/sessions", json=body)
    assert created.status_code == 202, created.text
    document = client.get(route + "/documents/" + created.json()["id"]).json()
    assert document["status"] == "awaiting_confirmation", document
    assert len(calls) == 1
    assert [source.source_role for source in calls[0].sources] == [
        "product_reference",
        "style_reference",
    ]
    confirm_route = route + "/sessions/" + document["id"] + "/confirm"
    assert client.post(confirm_route, json={"plan_hash": "0" * 64}).status_code == 409
    confirmation = {
        "plan_hash": document["payload"]["plan_hash"],
        "instructions": ["不要增加赠品"],
    }
    too_long = client.post(
        confirm_route, json={**confirmation, "instructions": ["长" * 4000]}
    )
    assert too_long.status_code == 422
    assert (
        "confirmed_instructions_hash"
        not in client.get(route + "/documents/" + document["id"]).json()["payload"]
    )
    first = client.post(confirm_route, json=confirmation)
    assert first.status_code == 202, first.text
    second = client.post(confirm_route, json=confirmation)
    assert second.status_code == 202, second.text
    assert first.json()["payload"]["job_ids"] == second.json()["payload"]["job_ids"]
    assert len(first.json()["payload"]["job_ids"]) == 1
    job_id = first.json()["payload"]["job_ids"][0]
    job = client.get(f"/api/v1/workspaces/ws_test/jobs/{job_id}").json()
    assert (
        job["configuration"]["settings"]["reference_skill"]
        == "reference-visual-reverse-remix"
    )
    assert job["configuration"]["settings"]["execution_mode"] == "direct_prompt"
    assert job["configuration"]["replica_session_id"] == document["id"]
    from app.outbox import _dispatch
    from app.tasks import process_job

    dispatches = []
    monkeypatch.setattr(
        process_job, "apply_async", lambda **kwargs: dispatches.append(kwargs)
    )
    celery_settings = client.app.state.settings.model_copy(
        update={"task_dispatcher": "celery"}
    )
    _dispatch(
        celery_settings,
        client.app.state.task_runner,
        event_type="job.dispatch_requested",
        dedupe_key="replica-test",
        payload={"job_id": job_id},
    )
    assert dispatches[0]["queue"] == "one_click_replica"
    assert job["configuration"]["input"]["product_asset_ids"] == [product]
    assert job["configuration"]["input"]["reference_asset_ids"] == [reference]
    changed = client.post(
        confirm_route, json={**confirmation, "instructions": ["换个方案"]}
    )
    assert changed.status_code == 409
    with client.app.state.session_factory() as db:
        assert db.get(ReplicaDocumentRecord, document["id"]).payload["job_ids"] == [
            job_id
        ]

    from app.models import JobItemRecord, JobRecord, utc_now

    with client.app.state.session_factory() as db:
        job_record = db.get(JobRecord, job_id)
        job_record.status = "failed"
        job_record.finished_at = utc_now()
        job_record.failed_item_count = job_record.expected_item_count
        for item in db.query(JobItemRecord).filter_by(job_id=job_id).all():
            item.status = "failed"
        document_record = db.get(ReplicaDocumentRecord, document["id"])
        document_record.payload = {
            **document_record.payload,
            "execution_progress": {"job_id": job_id, "percentage": 95},
            "error": "Previous run did not pass review",
            "error_code": "REPLICA_SKILL_BLOCKED",
        }
        db.commit()
    image_retry = route + "/sessions/" + document["id"] + "/jobs/" + job_id + "/retry"
    one = client.post(image_retry, json={"request_id": str(uuid4())})
    two = client.post(image_retry, json={"request_id": str(uuid4())})
    assert one.status_code == two.status_code == 202
    assert one.json()["payload"]["job_ids"] == two.json()["payload"]["job_ids"]
    replacement = one.json()["payload"]["job_ids"][0]
    assert replacement != job_id
    assert len(one.json()["payload"]["retry_events"]) == 1
    assert not {"execution_progress", "error", "error_code"}.intersection(
        one.json()["payload"]
    )
    assert (
        client.get(f"/api/v1/workspaces/ws_test/jobs/{replacement}").json()[
            "configuration"
        ]["replica_session_id"]
        == document["id"]
    )


def test_reference_and_product_cannot_share_identity(client):
    product = upload(client, "red")
    result = client.post(
        "/api/v1/workspaces/ws_test/replica/sessions",
        json={
            "request_id": str(uuid4()),
            "product_asset_ids": [product],
            "references": [{"asset_id": product}],
        },
    )
    assert result.status_code == 422


def test_analysis_retry_preserves_completed_plans(client):
    client.app.state.settings.reference_remix_provider_enabled = True
    product = upload(client, "red")
    references = [upload(client, "blue"), upload(client, "green")]
    calls = []

    async def plan(request):
        calls.append(request.group_key)
        failed = request.group_key == "replica-1" and calls.count("replica-1") == 1
        return SimpleNamespace(
            payload=None
            if failed
            else {
                "product_identity_lock": "red product",
                "reference_visual_analysis": "layout only",
                "hard_composition_skeleton": "center",
                "commercial_intent": "突出商品",
                "original_remix_prompt": "保留用户的红色商品，参考构图。",
            },
            error_code="TRANSIENT" if failed else None,
            normalized_usage={"input_tokens": 20},
            actual_cost_micros=10,
        )

    client.app.state.replica_gateway = SimpleNamespace(plan=plan)
    route = "/api/v1/workspaces/ws_test/replica"
    response = client.post(
        route + "/sessions",
        json={
            "request_id": str(uuid4()),
            "product_asset_ids": [product],
            "references": [{"asset_id": asset} for asset in references],
        },
    )
    document_id = response.json()["id"]

    def get():
        return client.get(route + "/documents/" + document_id).json()

    assert get()["status"] == "failed"
    assert len(get()["payload"]["plans"]) == 1
    assert client.post(route + "/sessions/" + document_id + "/retry").status_code == 202
    assert get()["status"] == "awaiting_confirmation"
    assert len(get()["payload"]["plans"]) == 2
    assert calls == ["replica-0", "replica-1", "replica-1"]
    assert client.post(route + "/sessions/" + document_id + "/retry").status_code == 409


@pytest.mark.parametrize("has_checkpoint", [False, True])
def test_exhausted_plan_reviews_preserve_work_and_refuse_unchanged_retries(
    client, monkeypatch, has_checkpoint
):
    from copy import deepcopy

    from app import replica

    async def unexpected_analysis(*args):
        pytest.fail("A rejected plan needs revision before another analysis attempt")

    monkeypatch.setattr(replica, "_analyse", unexpected_analysis)
    document_id = "rep_" + uuid4().hex
    payload = {
        "analysis_attempt_id": "exhausted-attempt",
        "error_code": "REPLICA_PLAN_AUDIT_REJECTED",
        "error": "Legacy response suggested retrying the same plan",
        "job_ids": [],
        "progress": 82,
        "analysis_progress": {
            "stage": "review_plan",
            "total": 12,
            "planned": 12,
            "reviewed": 12,
            "accepted": 9,
            "pending_corrections": 3,
        },
    }
    if has_checkpoint:
        payload["_analysis_checkpoint"] = {"preserved": "source-bound-evidence"}
    original = deepcopy(payload)
    with client.app.state.session_factory() as session:
        session.add(
            ReplicaDocumentRecord(
                id=document_id,
                workspace_id="ws_test",
                created_by="usr_test",
                kind="session",
                fingerprint=uuid4().hex * 2,
                status="failed",
                payload=payload,
            )
        )
        session.commit()
    route = "/api/v1/workspaces/ws_test/replica"
    document = client.get(route + "/documents/" + document_id).json()
    assert document["payload"]["analysis_resume_available"] is False
    assert "请先调整" in document["payload"]["error"]
    assert document["payload"]["analysis_progress"]["accepted"] == 9
    assert "_analysis_checkpoint" not in document["payload"]
    for _ in range(2):
        retry = client.post(route + "/sessions/" + document_id + "/retry", json={})
        assert retry.status_code == 409, retry.text
        assert retry.json()["error"]["code"] == "REPLICA_PLAN_REVISION_REQUIRED"
    with client.app.state.session_factory() as session:
        saved = session.get(ReplicaDocumentRecord, document_id)
        assert saved.status == "failed" and saved.payload == original
    restored = next(
        d for d in client.get(route + "/sessions").json() if d["id"] == document_id
    )
    assert restored["payload"]["analysis_resume_available"] is False
    assert restored["payload"]["analysis_progress"]["accepted"] == 9


@pytest.mark.parametrize("input_changed", [False, True])
def test_explicit_protocol_revision_is_scoped_idempotent_and_keeps_checkpoint(
    client, monkeypatch, input_changed
):
    from copy import deepcopy

    from app import replica
    from app.replica_exact_revision import V2_BINDING

    client.app.state.settings.replica_exact_compact_workspaces = ("ws_test",)
    calls = []

    async def analyse(app, document_id, attempt_id):
        calls.append((document_id, attempt_id))

    monkeypatch.setattr(replica, "_analyse", analyse)
    document_id = "rep_" + uuid4().hex
    checkpoint = {"model_protocol": V2_BINDING, "files": {"kept": "synthetic"}}
    payload = {
        "product_asset_ids": ["synthetic-product"],
        "references": [{"asset_id": "synthetic-reference"}],
        "facts": "Synthetic authorized product facts",
        "language": "zh-CN",
        "platform_code": "taobao",
        "replication_method": "exact",
        "_exact_model_protocol": "exact-compact-v2",
        "_analysis_checkpoint": checkpoint,
        "analysis_attempt_id": "old",
        "error_code": "REPLICA_PLAN_AUDIT_REJECTED",
        "job_ids": [],
        "progress": 82,
        "analysis_progress": {
            "total": 12,
            "planned": 12,
            "reviewed": 12,
            "accepted": 9,
            "pending_corrections": 3,
            "stage": "review_plan",
        },
    }
    payload["_analysis_input_hash"] = replica._analysis_input_hash(payload)
    if input_changed:
        payload["facts"] += " Changed after checkpoint."
    original = deepcopy(payload)
    with client.app.state.session_factory() as session:
        session.add(
            ReplicaDocumentRecord(
                id=document_id,
                workspace_id="ws_test",
                created_by="usr_test",
                kind="session",
                fingerprint=uuid4().hex * 2,
                status="failed",
                payload=payload,
            )
        )
        session.commit()
    route = "/api/v1/workspaces/ws_test/replica"
    wire = client.get(route + "/documents/" + document_id).json()
    assert wire["payload"]["analysis_revision_available"] is (not input_changed)
    response = client.post(route + "/sessions/" + document_id + "/revise", json={})
    if input_changed:
        assert response.status_code == 409 and not calls
        with client.app.state.session_factory() as session:
            assert session.get(ReplicaDocumentRecord, document_id).payload == original
        return
    assert response.status_code == 202, response.text
    value = response.json()
    assert value["payload"]["analysis_progress"]["accepted"] == 9
    assert "_analysis_revision" not in value["payload"]
    assert "_analysis_checkpoint" not in value["payload"]
    again = client.post(route + "/sessions/" + document_id + "/revise", json={})
    assert again.status_code == 202 and len(calls) == 1
    with client.app.state.session_factory() as session:
        saved = session.get(ReplicaDocumentRecord, document_id)
        assert saved.payload["_analysis_checkpoint"] == checkpoint
        from app.replica_exact_compact import VERSION

        assert saved.payload["_exact_model_protocol"] == VERSION
        assert saved.payload["_analysis_input_hash"] == original["_analysis_input_hash"]
        assert saved.payload["_analysis_revision"][
            "parent_snapshot_sha256"
        ] == replica._hash(checkpoint)


def test_1688_main_gallery_does_not_absorb_sku_specification_images():
    main, sku = "https://cbu01.alicdn.com/main.jpg", "https://cbu01.alicdn.com/sku.jpg"
    data = {
        "item": {"offerTitle": "杯子"},
        "images": [main],
        "specs": [{"images": [sku]}],
    }
    product = normalize_product(
        data, "1688", "123456789", "https://detail.1688.com/offer/123456789.html"
    )
    assert product["imageUrls"] == [main]
    assert product["skuImageUrls"] == [sku]


def test_twenty_images_fit_job_capacity_and_occupied_capacity_preserves_plan(client):
    from app.models import JobRecord, utc_now
    from app.services import set_credit_enforcement_enabled

    client.app.state.settings.reference_remix_provider_enabled = True
    set_credit_enforcement_enabled(False)
    product, reference = upload(client, "red"), upload(client, "blue")
    normal_body = {
        "type": "reference_remix",
        "intent": "submit",
        "input": {"product_asset_ids": [product], "reference_asset_ids": [reference]},
        "configuration": {"reference_skill": "reference-visual-reverse-remix"},
    }
    normal = client.post(
        "/api/v1/workspaces/ws_test/jobs",
        json=normal_body,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert normal.status_code == 202, normal.text
    assert "replica_session_id" not in normal.json()["configuration"]

    async def plan(request):
        return SimpleNamespace(
            payload={
                "product_identity_lock": "red product",
                "reference_visual_analysis": "layout only",
                "hard_composition_skeleton": "center",
                "commercial_intent": "突出商品",
                "original_remix_prompt": "保留用户商品，借鉴参考构图。",
            },
            error_code=None,
            normalized_usage={},
            actual_cost_micros=0,
        )

    client.app.state.replica_gateway = SimpleNamespace(plan=plan)
    route = "/api/v1/workspaces/ws_test/replica"
    created = client.post(
        route + "/sessions",
        json={
            "request_id": str(uuid4()),
            "product_asset_ids": [product],
            "references": [
                {"asset_id": reference, "title": f"参考图 {i + 1}"} for i in range(20)
            ],
        },
    )
    document_id = created.json()["id"]
    document = client.get(route + "/documents/" + document_id).json()
    assert document["status"] == "awaiting_confirmation"
    confirm = route + "/sessions/" + document_id + "/confirm"
    body = {"plan_hash": document["payload"]["plan_hash"]}
    blocked = client.post(confirm, json=body)
    assert blocked.status_code == 429, blocked.text
    saved = client.get(route + "/documents/" + document_id).json()
    assert saved["payload"]["job_ids"] == []
    assert "confirmed_instructions_hash" not in saved["payload"]
    with client.app.state.session_factory() as db:
        old = db.get(JobRecord, normal.json()["id"])
        old.status, old.finished_at, old.cancelled_item_count = (
            "cancelled",
            utc_now(),
            old.expected_item_count,
        )
        db.commit()
    accepted = client.post(confirm, json=body)
    assert accepted.status_code == 202, accepted.text
    assert len(accepted.json()["payload"]["job_ids"]) == 20
    assert accepted.json()["payload"]["submission_complete"] is True
    assert (
        client.post(
            "/api/v1/workspaces/ws_test/jobs",
            json=normal_body,
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 429
    )


def test_partial_submission_is_resumable_without_duplicate_jobs(client, monkeypatch):
    from app import routes_v1
    from app.models import JobRecord

    client.app.state.settings.reference_remix_provider_enabled = True
    product, reference = upload(client, "red"), upload(client, "blue")

    async def plan(request):
        return SimpleNamespace(
            payload={
                "product_identity_lock": "red product",
                "reference_visual_analysis": "layout only",
                "hard_composition_skeleton": "center",
                "commercial_intent": "突出商品",
                "original_remix_prompt": "保留用户商品，借鉴参考构图。",
            },
            error_code=None,
            normalized_usage={},
            actual_cost_micros=0,
        )

    client.app.state.replica_gateway = SimpleNamespace(plan=plan)
    route = "/api/v1/workspaces/ws_test/replica"
    response = client.post(
        route + "/sessions",
        json={
            "request_id": str(uuid4()),
            "product_asset_ids": [product],
            "references": [
                {"asset_id": reference, "title": str(index)} for index in range(3)
            ],
        },
    )
    document_id = response.json()["id"]
    document = client.get(route + "/documents/" + document_id).json()
    original = routes_v1.create_job
    admitted = []

    def interrupted(**kwargs):
        if len(admitted) == 2:
            raise ApiError(503, "TEST_INTERRUPTED", "测试中断")
        job = original(**kwargs)
        admitted.append(job.id)
        return job

    monkeypatch.setattr(routes_v1, "create_job", interrupted)
    confirm = route + "/sessions/" + document_id + "/confirm"
    body = {"plan_hash": document["payload"]["plan_hash"], "instructions": ["保留标识"]}
    assert client.post(confirm, json=body).status_code == 503
    partial = client.get(route + "/documents/" + document_id).json()
    assert partial["payload"]["job_ids"] == admitted
    assert partial["payload"]["confirmed_instructions"] == body["instructions"]
    assert partial["payload"]["submission_complete"] is False
    monkeypatch.setattr(routes_v1, "create_job", original)
    finished = client.post(confirm, json=body)
    assert finished.status_code == 202, finished.text
    assert finished.json()["payload"]["job_ids"][:2] == admitted
    assert len(finished.json()["payload"]["job_ids"]) == 3
    with client.app.state.session_factory() as db:
        assert len(db.query(JobRecord).all()) == 3


def test_visual_planner_receives_user_language_canvas_and_facts():
    from app.xinghe_agent import PlannerRequest, _planner_prompt

    prompt = _planner_prompt(
        PlannerRequest(
            workspace_id="ws_test",
            job_id="rep_test",
            group_key="test",
            sources=(),
            page_count=1,
            language="zh-CN",
            product_name="我的商品",
            product_information=("保留银色蝴蝶结，不虚构功效",),
            platform_code="tmall",
            size="1024x1024",
            idempotency_key="test",
            planning_profile="reference_visual_remix",
        )
    )
    assert '"target_language": "zh-CN"' in prompt
    assert '"canvas_size": "1024x1024"' in prompt
    assert "保留银色蝴蝶结，不虚构功效" in prompt
