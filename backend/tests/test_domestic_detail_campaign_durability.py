"""Atomic result journals retain original observations through torn local writes."""

import json
from dataclasses import asdict

import pytest

from app.domestic_detail.campaign_durability import (
    JOURNAL,
    bytes_once,
    load_result,
    recover_response,
    save_result,
)
from app.domestic_detail.planner_dispatch import result_with_dispatch, route_descriptor
from app.domestic_detail.review import ReviewStore
from app.xinghe_agent import PlannerCallResult


def saved(tmp_path):
    route = route_descriptor(
        provider="synthetic",
        route="synthetic",
        model="observer",
        endpoint="offline",
        credential="offline",
        kind="synthetic",
    )
    store = ReviewStore(
        tmp_path / "reviews",
        {
            "phase": "aurora_detail_campaign_audit",
            "render_token": "synthetic-job",
            "native_request_sha256": "a" * 64,
            "candidate_sha256": "b" * 64,
            "request_context_sha256": "c" * 64,
            "dispatch_route": route,
        },
    )
    store.begin()
    value = result_with_dispatch(
        asdict(
            PlannerCallResult(
                payload={"observed": "原始逐像素观察"},
                provider_request_id="synthetic-original",
                audit_raw_content='{"observed":"原始逐像素观察"}',
                raw_usage={"total_tokens": 17},
                normalized_usage={
                    "reported": True,
                    "input_tokens": 9,
                    "output_tokens": 8,
                },
                api_latency_seconds=0.01,
                actual_cost_micros=17,
            )
        ),
        route,
    )
    save_result(store, value)
    store.finish(value)
    return store, json.loads(json.dumps(value, ensure_ascii=False))


@pytest.mark.parametrize(
    "changed",
    [
        "reply_partial",
        "response_partial",
        "reply_missing",
        "response_missing",
        "journal",
        "request",
        "conflicting_response",
    ],
)
def test_journal_restores_only_its_original_result_and_retains_damaged_bytes(
    tmp_path, changed
):
    store, original = saved(tmp_path)
    reply = store.stem.with_suffix(".reply.txt")
    target = {
        "journal": tmp_path / JOURNAL,
        "request": store.request_path,
        "response_partial": store.response_path,
        "response_missing": store.response_path,
        "reply_partial": reply,
        "reply_missing": reply,
        "conflicting_response": store.response_path,
    }[changed]
    if changed.endswith("missing"):
        target.unlink()
    elif changed == "conflicting_response":
        value = json.loads(target.read_bytes())
        value["result"]["payload"] = {"invented": True}
        target.write_bytes(json.dumps(value).encode())
    else:
        target.write_bytes(b"partial-original-fragment")
    before = target.read_bytes() if target.exists() else None
    journal = (tmp_path / JOURNAL).read_bytes()
    if changed in {"journal", "request", "conflicting_response"}:
        with pytest.raises((ValueError, OSError)):
            recover_response(store)
        assert target.read_bytes() == before
    else:
        result = recover_response(store)
        assert result == original and store.completed() == original
        assert reply.read_bytes() == original["audit_raw_content"].encode("utf-8")
        assert (tmp_path / JOURNAL).read_bytes() == journal
        if before is not None:
            assert any(
                p.read_bytes() == before
                for p in (store.root / "recovered-originals").glob("*.bytes")
            )
        assert load_result(store)["result"] == original


def test_unpublished_result_stage_is_retained_when_publication_fails(
    tmp_path, monkeypatch
):
    import app.domestic_detail.campaign_durability as durability

    target = tmp_path / "original-result.json"
    payload = b"original completed model result"

    def failed_link(*args):
        raise OSError("synthetic filesystem publication interruption")

    monkeypatch.setattr(durability.os, "link", failed_link)
    with pytest.raises(OSError):
        bytes_once(target, payload)
    assert not target.exists()
    stages = list(tmp_path.glob(".campaign-stage-*"))
    assert len(stages) == 1 and stages[0].read_bytes() == payload
