"""Synthetic candidates exercise local review wiring and recovery."""

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from app.aurora_detail_runtime import AuroraDetailHarnessRunner, detail_workspace
from app.domestic_detail.generation import (
    DetailGenerationError,
    begin_invocation,
    finish_invocation,
    verify_accepted_canvas,
)
from app.domestic_detail.pixels import load_native_pair_pixels, pixel_review_context
from app.domestic_detail.revision_review import original_revision_sources
from app.models import JobAttemptRecord, JobPlanRecord, JobRecord, utc_now
from app.xinghe_agent import (
    PlannerCallResult,
    SourceImage,
    XingheAgentError,
    _artifact_from_bytes,
    normalize_usage,
)
from tests.test_domestic_detail_delivery import ORIGINAL_REVIEW
from tests.test_native_detail_revision_pixel_review import observation


def run_local_review_boundary(
    client,
    monkeypatch,
    provider,
    gateway,
    request,
    binding,
    native,
    ordered,
    configuration,
    product_sources,
    mode,
):
    root = detail_workspace(request.job_id)
    runner = AuroraDetailHarnessRunner()
    quote = request.configuration["job"]["local_revision"]["authority"]["quote"]
    original = original_revision_sources(native)
    assert len(original) == 3
    assert original[0].sha256 == quote["source_pair_canvas"]["sha256"]
    assert [s.sha256 for s in original[1:]] == [
        row["sha256"] for row in quote["source_screens"]
    ]
    assert all(hashlib.sha256(s.payload).hexdigest() == s.sha256 for s in original)
    original_canvas_sha = original[0].sha256
    prior_plan = gateway.plan
    calls, identities = [], {}
    pair_count = 0

    async def review(call):
        nonlocal pair_count
        if call.planning_profile not in {
            "aurora_detail_pair_audit",
            "aurora_detail_identity_audit",
        }:
            return await prior_plan(call)
        calls.append(call)
        context = json.loads(call.product_information[0])
        original_frames = [
            s
            for s in call.sources
            if s.source_role.startswith("local_revision_original_")
        ]
        assert [s.sha256 for s in original_frames] == [s.sha256 for s in original]
        assert [s.source_role for s in original_frames] == [
            s.source_role for s in original
        ]
        assert call.sources[-1].source_role == "generated_pair_candidate"
        assert all(
            hashlib.sha256(s.payload).hexdigest() == s.sha256 for s in call.sources
        )
        if call.planning_profile == "aurora_detail_pair_audit":
            pair_count += 1
            assert (
                context["local_revision_context"]["expected_observation"][
                    "source_canvas_sha256"
                ]
                == original_canvas_sha
            )
            artifact = _artifact_from_bytes(
                call.sources[-1].payload,
                filename=call.sources[-1].filename,
                expected_size="1440x3840",
                preserve_provider_artifact=True,
            )
            raw = provider._native_detail_judgment(context, artifact)
            raw["local_revision_observation"] = observation(
                native["local_revision_contract"], native["pixel_lineage"]
            )
            identities[artifact.sha256] = deepcopy(raw["identity_silhouette"])
            if mode == "repair_identity" and pair_count == 1:
                raw["local_revision_observation"]["change_only"][0]["pass"] = False
                raw.update(status="MATERIAL_FAIL", material_failures=[])
            if mode in {"missing_local", "repair_identity"}:
                raw.pop("identity_silhouette")
            if mode == "missing_local":
                raw.pop("local_revision_observation")
        else:
            raw = {**identities[call.sources[-1].sha256], "audit_complete": True}
        return PlannerCallResult(
            payload=raw,
            provider_request_id="offline-local-review-" + str(len(calls)),
            raw_usage={"total_tokens": 13},
            normalized_usage=normalize_usage({"total_tokens": 13}),
            api_latency_seconds=0.01,
            actual_cost_micros=quote["review_reserve_micros"] + 1
            if mode == "overrun"
            else 1,
            audit_raw_content=json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        )

    monkeypatch.setattr(gateway, "plan", review)
    attempts = 2 if mode == "repair_identity" else 1
    invocation_paths = []
    for attempt in range(attempts):
        if attempt:
            previous_sha = native["pixel_lineage"]["canvas"]["sha256"]
            _, native, ordered = asyncio.run(
                provider._prepare_native_detail_generation(
                    request, configuration, product_sources, binding
                )
            )
            assert native["edit_canvas_evidence"]["sha256"] == previous_sha
            assert (
                native["local_revision_contract"]["baseline"]["canvas"]["sha256"]
                == original_canvas_sha
            )
        shape = native["local_revision_contract"]["baseline"]["canvas"]
        stream = BytesIO()
        Image.new(
            "RGB",
            (shape["width_px"], shape["height_px"]),
            (111 + attempt * 30, 121, 131),
        ).save(stream, format="PNG")
        artifact = _artifact_from_bytes(
            stream.getvalue(),
            filename="synthetic-local-candidate.png",
            expected_size="1440x3840",
            preserve_provider_artifact=True,
        )
        path = root / ("synthetic-local-candidate-" + str(attempt) + ".png")
        path.write_bytes(artifact.payload)
        invoice = begin_invocation(
            root,
            binding,
            native,
            provider=gateway.provider_name,
            model=gateway.image_model,
            pricing_version=gateway.pricing_version,
            idempotency_key=request.idempotency_key + ":" + str(attempt),
            prompt=str(native.get("prompt") or "SYNTHETIC fixture signed request"),
            size="1440x3840",
            quality="high",
            sources=[
                {"asset_id": s.asset_id, "role": s.source_role, "sha256": s.sha256}
                for s in ordered
            ],
        )
        finish_invocation(
            invoice,
            outcome="succeeded",
            provider_request_id="synthetic-local-image-" + str(attempt),
            artifact_sha256=artifact.sha256,
            artifact_payload=artifact.payload,
        )
        invocation_paths.append(invoice)
        runner.run(
            "record-generation",
            root,
            {"session": root / "session.json", "token": native["token"], "image": path},
        )
        lineage = runner.run(
            "inspect-pair-artifacts",
            root,
            {
                "session": root / "session.json",
                "pair-id": native["pair_id"],
                "selection": "LATEST_RECORDED",
            },
        )
        pixels = load_native_pair_pixels(lineage, [root], group_key="local")
        native["pixel_lineage"] = pixel_review_context(lineage)
        attached = (
            *ordered,
            *(
                SourceImage(
                    asset_id="native-slice:" + module + ":" + s.sha256,
                    group_key="local",
                    filename=s.filename,
                    content_type=s.content_type,
                    sha256=s.sha256,
                    payload=s.payload,
                    source_role="generated_module_candidate:" + module,
                )
                for module, s in zip(native["module_ids"], pixels.screens, strict=True)
            ),
        )

        def evaluate(native=native, attached=attached, pixels=pixels):
            return asyncio.run(
                ORIGINAL_REVIEW(provider, request, native, attached, pixels.canvas)
            )

        if mode == "complete" and attempt == 0:
            # Recheck the current lease and cancellation before a new model fee.
            with client.app.state.session_factory() as db:
                db.get(JobRecord, request.job_id).cancel_requested_at = utc_now()
                db.commit()
            with pytest.raises(XingheAgentError):
                evaluate()
            with client.app.state.session_factory() as db:
                db.get(JobRecord, request.job_id).cancel_requested_at = None
                db.get(JobAttemptRecord, request.attempt_id).lease_expires_at = (
                    utc_now() - timedelta(seconds=1)
                )
                db.commit()
            with pytest.raises(XingheAgentError):
                evaluate()
            with client.app.state.session_factory() as db:
                db.get(JobAttemptRecord, request.attempt_id).lease_expires_at = (
                    utc_now() + timedelta(hours=1)
                )
                db.commit()
            assert not calls and not list((root / "reviews").glob("*.request.json"))
        before_native = (root / "session.json").read_bytes()
        if mode in {"missing_local", "overrun"}:
            for _ in range(2):
                with pytest.raises(XingheAgentError):
                    evaluate()
            assert (
                len(calls) == 1
                and calls[0].planning_profile == "aurora_detail_pair_audit"
            )
            assert (root / "session.json").read_bytes() == before_native
            with client.app.state.session_factory() as db:
                plan = db.scalar(
                    select(JobPlanRecord).where(JobPlanRecord.job_id == request.job_id)
                )
                reviews = [
                    r
                    for r in plan.payload["usage"]["planner"]
                    if r.get("review_binding_sha256")
                ]
                assert len(reviews) == 1
                assert reviews[0]["actual_cost_micros"] == (
                    quote["review_reserve_micros"] + 1 if mode == "overrun" else 1
                )
            return
        judgment = evaluate()
        count = len(calls)
        from app.domestic_detail import revision_accounts

        with monkeypatch.context() as patcher:

            def unavailable(*args, **kwargs):
                raise AssertionError(
                    "Original paid review must not query or use a current account"
                )

            patcher.setattr(gateway, "native_planner_route", unavailable)
            patcher.setattr(
                revision_accounts, "configured_revision_accounts", unavailable
            )
            assert evaluate() == judgment and len(calls) == count
        assert (
            judgment["local_revision_observation"]["source_canvas_sha256"]
            == original_canvas_sha
        )
        judgment_path = root / ("local-pixel-judgment-" + str(attempt) + ".json")
        judgment_path.write_text(
            json.dumps(judgment, ensure_ascii=False), encoding="utf-8"
        )
        result = runner.run(
            "audit-pair",
            root,
            {"session": root / "session.json", "judgment": judgment_path},
        )
        assert result["state"] == (
            "REPAIR_REQUIRED"
            if attempt + 1 < attempts
            else "AWAITING_USER_FINAL_REVIEW"
        )
    assert len(calls) == (4 if mode == "repair_identity" else 1)
    assert len(invocation_paths) == attempts
    accepted = verify_accepted_canvas(
        [root],
        binding,
        pair_id=native["pair_id"],
        artifact_sha256=lineage["canvas"]["sha256"],
    )
    assert accepted == lineage
    evidence = runner.run(
        "inspect-pair-review-evidence",
        root,
        {"session": root / "session.json", "pair-id": native["pair_id"]},
    )
    assert (
        evidence["audit_input"]["local_revision_observation"]["source_canvas_sha256"]
        == original_canvas_sha
    )
    assert len(evidence["audit_input"]["review_provenance"]) == (
        2 if mode == "repair_identity" else 1
    )
    count = len(calls)
    for reference in evidence["audit_input"]["review_provenance"]:
        req = root / reference["request_file"]
        reply = req.with_name(req.name.replace(".request.json", ".reply.txt"))
        original_reply = reply.read_bytes()
        reply.write_bytes(original_reply + b"changed fixture bytes")
        with pytest.raises(DetailGenerationError):
            verify_accepted_canvas(
                [root],
                binding,
                pair_id=native["pair_id"],
                artifact_sha256=lineage["canvas"]["sha256"],
            )
        reply.write_bytes(original_reply)
    assert len(calls) == count
    verify_accepted_canvas(
        [root],
        binding,
        pair_id=native["pair_id"],
        artifact_sha256=lineage["canvas"]["sha256"],
    )
    with client.app.state.session_factory() as db:
        plan = db.scalar(
            select(JobPlanRecord).where(JobPlanRecord.job_id == request.job_id)
        )
        reviews = [
            r
            for r in plan.payload["usage"]["planner"]
            if r.get("review_binding_sha256")
        ]
        assert len(reviews) == len(calls)
        assert len(plan.payload["usage"]["planner"]) == 1 + len(calls) <= 5
