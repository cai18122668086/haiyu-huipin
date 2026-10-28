"""Local scope never promotes facts, weak findings or a second model request."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict

import pytest

from app.domestic_detail.plans import canonical_hash
from app.domestic_detail.revision_protocol import (
    DIMENSIONS,
    SCOPE_PROFILE,
    RevisionScopeError,
    request_context,
    scope_context,
    scope_prompt,
    validate_scope_response,
)
from app.domestic_detail.revision_scope import SCOPE_JOURNAL, run_scope_check
from app.xinghe_agent import (
    FakeXingheGateway,
    PlannerCallResult,
    PlannerRequest,
    normalize_usage,
)
from tests.test_domestic_detail_review_authority import compiled_review_session
from tests.test_native_detail_local_adjustment import adjustment


def context_fixture():
    state = compiled_review_session()
    state["plan"]["modules"] = [{"id": f"M{i + 1:02d}"} for i in range(8)]
    state["plan"]["output_strategy"]["continuous_pair_rendering"] = {
        "pair_ids": [
            {
                "pair_id": f"P{i // 2 + 1:02d}",
                "module_ids": [f"M{i + 1:02d}", f"M{i + 2:02d}"],
            }
            for i in range(0, 8, 2)
        ]
    }
    delta = adjustment("M02")
    delta["verbatim_instruction"] = "  只修改此屏标题位置。\n保留原事实和另一屏。  "
    quote = {
        "adjustment": delta,
        "approved_screen_count": 8,
        "module_ids": ["M01", "M02"],
        "selected_module_id": "M02",
        "pair_id": "P01",
        "source_plan_payload_sha256": "b" * 64,
        "source_context_sha256": "c" * 64,
        "source_pair_canvas": {"sha256": "d" * 64},
        "skill_content_hash": (
            "4c65f700372e08f30020ca4a8961bd85c4ebb44fc3ff09c3b7cc039966d5b68d"
        ),
    }
    intent = {"sha256": "a" * 64, "adjustment": delta, "authority": {"quote": quote}}
    context = scope_context(intent, state)
    return context, make_request(context)


def make_request(context):
    return PlannerRequest(
        workspace_id="ws_scope",
        job_id="job_scope",
        group_key="local-revision-scope",
        sources=(),
        page_count=2,
        language="zh-CN",
        product_name=context["approved_authority"]["product_name"],
        product_information=(json.dumps(context, ensure_ascii=False),),
        platform_code="domestic-detail",
        size="semantic-scope-only",
        idempotency_key="scope-original",
        domestic_protocol=True,
        planning_profile=SCOPE_PROFILE,
    )


def observation(request):
    context = request_context(request)
    return {
        "version": context["version"],
        "context_sha256": context["sha256"],
        "intent_sha256": context["intent_sha256"],
        "adjustment_sha256": context["adjustment_sha256"],
        "selected_module_id": context["selected_module_id"],
        "pair_id": context["pair_id"],
        "contract_overrides": [],
        "decision": "LOCAL",
        "dimensions": [
            {
                "dimension": key,
                "state": "UNCHANGED",
                "reason": "Explicit synthetic comparison against the locked " + key,
            }
            for key in DIMENSIONS
        ],
        "changes": [
            {
                "index": i,
                "original_change": text,
                "scope": "LOCAL",
                "claim_effect": "PRESERVE",
                "contract_effect": "RESTORE_OR_PRESERVE_APPROVED",
                "reason": (
                    "Only the named title layout changes; "
                    "original wording and geometry preserved"
                ),
                "fact_refs": [],
            }
            for i, text in enumerate(context["adjustment"]["change_only"])
        ],
        "other_screens": {
            "state": "PRESERVED",
            "reason": "Original sibling and every other screen remain the same",
        },
        "rationale": (
            "Only the selected title position changes under the "
            "original approved facts and visual direction"
        ),
    }


def test_scope_context_preserves_original_instruction_and_source_fact_dispositions():
    context, request = context_fixture()
    before = deepcopy(context)
    prompt = scope_prompt(request)
    assert "RAW UNAPPROVED CLAIM" not in prompt and "false flags" in prompt
    assert (
        request_context(request)["adjustment"]["verbatim_instruction"]
        == context["adjustment"]["verbatim_instruction"]
    )
    assert validate_scope_response(observation(request), request)[
        "accepted_local_scope"
    ]
    assert context == before
    ledger = context["approved_authority"]["original_fact_ledger"]
    assert [row["approved_disposition"] for row in ledger] == [
        "LEAD",
        "EXCLUDED",
        "DEFERRED",
        "SUPPORT",
    ]


@pytest.mark.parametrize(
    "change",
    [
        "global",
        "unknown",
        "dimension_changed",
        "dimension_unknown",
        "other_changed",
        "other_unknown",
        "change_global",
        "change_unknown",
        "new_claim",
    ],
)
def test_unknown_or_contradictory_model_findings_cannot_be_local_pass(change):
    _, request = context_fixture()
    value = observation(request)
    if change in {"global", "unknown"}:
        value["decision"] = change.upper()
    elif change.startswith("dimension_"):
        value["dimensions"][1]["state"] = change.removeprefix("dimension_").upper()
    elif change.startswith("other_"):
        value["other_screens"]["state"] = change.removeprefix("other_").upper()
    elif change.startswith("change_"):
        value["changes"][0]["scope"] = change.removeprefix("change_").upper()
    else:
        value["changes"][0]["claim_effect"] = "NEW_OR_UNKNOWN"
    result = validate_scope_response(value, request)
    assert result["requires_replan"] and not result["accepted_local_scope"]
    assert result["original_observation"] == value


@pytest.mark.parametrize(
    "tamper",
    [
        "context",
        "intent",
        "delta",
        "module",
        "pair",
        "extra",
        "missing_dimension",
        "duplicate_dimension",
        "blank_reason",
        "missing_change",
        "rewritten_change",
        "bool_index",
        "deferred_fact",
        "excluded_fact",
        "invented_fact",
        "changed_fact_text",
        "duplicate_fact",
        "rephrase_without_fact",
    ],
)
def test_scope_rejects_missing_rewritten_or_unapproved_model_evidence(tamper):
    _, request = context_fixture()
    value = observation(request)
    if tamper in {"context", "intent", "delta"}:
        value[
            {
                "context": "context_sha256",
                "intent": "intent_sha256",
                "delta": "adjustment_sha256",
            }[tamper]
        ] = "f" * 64
    elif tamper == "module":
        value["selected_module_id"] = "M01"
    elif tamper == "pair":
        value["pair_id"] = "P02"
    elif tamper == "extra":
        value["optimistic_default"] = True
    elif tamper == "missing_dimension":
        value["dimensions"].pop()
    elif tamper == "duplicate_dimension":
        value["dimensions"][1] = deepcopy(value["dimensions"][0])
    elif tamper == "blank_reason":
        value["other_screens"]["reason"] = "  "
    elif tamper == "missing_change":
        value["changes"].pop()
    elif tamper == "rewritten_change":
        value["changes"][0]["original_change"] = "Model rewrote the human instruction"
    elif tamper == "bool_index":
        value["changes"][0]["index"] = False
    elif tamper == "rephrase_without_fact":
        value["changes"][0]["claim_effect"] = "REPHRASE_CONFIRMED"
    else:
        fact = {
            "deferred_fact": {"id": "F03", "text": "777-hour performance claim"},
            "excluded_fact": {"id": "F02", "text": "Capacity 300 ml"},
            "invented_fact": {"id": "F99", "text": "Invented"},
            "changed_fact_text": {"id": "F01", "text": "Capacity 999 ml"},
            "duplicate_fact": {"id": "F01", "text": "Capacity 500 ml"},
        }[tamper]
        value["changes"][0]["fact_refs"] = (
            [fact, fact] if tamper == "duplicate_fact" else [fact]
        )
    with pytest.raises(RevisionScopeError):
        validate_scope_response(value, request)


def model_result(request, value=None, raw=None):
    value = observation(request) if value is None else value
    return PlannerCallResult(
        payload=value,
        provider_request_id="mock_scope_original",
        raw_usage=None,
        normalized_usage=normalize_usage(None),
        api_latency_seconds=0,
        actual_cost_micros=0,
        audit_raw_content=json.dumps(value, ensure_ascii=False) if raw is None else raw,
        external_request=False,
    )


def test_one_original_call_is_journaled_accounted_once_and_repaired_without_a_model(
    tmp_path,
):
    context, _ = context_fixture()
    root = tmp_path / "job_scope"
    gateway = FakeXingheGateway()
    calls, accounts, preflights = [], {}, []

    async def plan(request):
        calls.append(request)
        return model_result(request)

    gateway.plan = plan

    def account(result, key):
        value = asdict(result)
        if key in accounts:
            assert canonical_hash(accounts[key]) == canonical_hash(value)
        accounts[key] = value

    def run(value=context):
        return asyncio.run(
            run_scope_check(
                root=root,
                workspace_id="ws_scope",
                job_id="job_scope",
                context=value,
                gateway=gateway,
                before_dispatch=lambda: preflights.append(True),
                account_result=account,
            )
        )

    first = run()
    assert first["accepted_local_scope"] and len(calls) == 1 and len(accounts) == 1
    assert (root / SCOPE_JOURNAL).exists()
    assert run() == first and len(calls) == 1
    original = next((root / "planner-calls").glob("*.reply.txt")).read_bytes()
    next((root / "planner-calls").glob("*.reply.txt")).write_bytes(
        b"damaged derived raw reply"
    )
    assert run() == first and len(calls) == 1
    assert next((root / "planner-calls").glob("*.reply.txt")).read_bytes() == original
    assert list((root / "planner-calls/recovered-originals").glob("*.bytes"))
    altered = deepcopy(context)
    altered["adjustment"]["verbatim_instruction"] += " changed"
    altered["adjustment_sha256"] = canonical_hash(altered["adjustment"])
    altered["sha256"] = canonical_hash(
        {k: v for k, v in altered.items() if k != "sha256"}
    )
    with pytest.raises(RevisionScopeError):
        run(altered)
    assert len(calls) == 1 and len(accounts) == 1
    (root / SCOPE_JOURNAL).unlink()
    with pytest.raises(RevisionScopeError, match="Reconcile"):
        run()
    assert len(calls) == 1 and len(accounts) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "global",
        "unknown",
        "missing_findings",
        "raw_mismatch",
        "raw_missing",
        "dispatch_invalid",
    ],
)
def test_rejected_scope_retains_original_paid_result_and_never_calls_again(
    tmp_path, failure
):
    from app.domestic_detail.planner_dispatch import route_descriptor

    context, _ = context_fixture()
    root = tmp_path / "job_scope"
    gateway = FakeXingheGateway()
    if failure == "dispatch_invalid":
        gateway.native_planner_route = lambda: route_descriptor(
            provider="fixture",
            route="original",
            model="scope",
            endpoint="https://example.invalid",
            credential="fixture-only",
        )
    calls, accounts = [], {}

    async def plan(request):
        calls.append(request)
        value = observation(request)
        if failure in {"global", "unknown"}:
            value["decision"] = failure.upper()
        elif failure == "missing_findings":
            value["dimensions"].pop()
        result = model_result(
            request, value, raw="{}" if failure == "raw_mismatch" else None
        )
        if failure == "raw_missing":
            from dataclasses import replace

            result = replace(result, audit_raw_content=None)
        return result

    gateway.plan = plan

    def account(result, key):
        accounts[key] = asdict(result)

    for _ in range(2):
        with pytest.raises(ValueError):
            asyncio.run(
                run_scope_check(
                    root=root,
                    workspace_id="ws_scope",
                    job_id="job_scope",
                    context=context,
                    gateway=gateway,
                    before_dispatch=lambda: None,
                    account_result=account,
                )
            )
    assert len(calls) == 1 and len(accounts) == 1
    assert (root / SCOPE_JOURNAL).exists()
    assert not list(root.glob("generation-*"))
    if failure in {"global", "unknown"}:
        saved = json.loads((root / "local-revision-scope.json").read_bytes())
        assert saved["requires_replan"] and not saved["accepted_local_scope"]


def test_potentially_sent_transport_failure_occupies_original_scope_slot(tmp_path):
    from app.domestic_detail.planner_dispatch import route_descriptor

    context, _ = context_fixture()
    root = tmp_path / "job_scope"
    gateway = FakeXingheGateway()
    calls = []
    accounts = {}
    gateway.native_planner_route = lambda: route_descriptor(
        provider="fixture",
        route="original",
        model="scope",
        endpoint="https://example.invalid",
        credential="fixture-only",
    )

    async def plan(request):
        calls.append(request)
        raise TimeoutError("explicit offline fixture")

    gateway.plan = plan
    for _ in range(2):
        with pytest.raises(RevisionScopeError) as error:
            asyncio.run(
                run_scope_check(
                    root=root,
                    workspace_id="ws_scope",
                    job_id="job_scope",
                    context=context,
                    gateway=gateway,
                    before_dispatch=lambda: None,
                    account_result=lambda result, key: accounts.update(
                        {key: asdict(result)}
                    ),
                )
            )
        assert error.value.code == "NATIVE_DETAIL_REVISION_SCOPE_OUTCOME_UNKNOWN"
    assert len(calls) == 1 and len(accounts) == 1
    assert next(iter(accounts.values()))["actual_cost_micros"] is None


@pytest.mark.parametrize("failure", ["none", "server", "invalid_message"])
def test_actual_gateway_uses_scope_prompt_schema_and_only_one_transport(failure):
    from dataclasses import replace

    from tests.test_domestic_detail_planner_dispatch import gateway

    _, request = context_fixture()

    class Transport:
        def __init__(self):
            self.calls = []

        async def post_json(self, **kwargs):
            self.calls.append(kwargs)
            if failure == "server":
                return 502, {"error": {"message": "explicit offline fixture"}}
            content = (
                "{"
                if failure == "invalid_message"
                else json.dumps(observation(request), ensure_ascii=False)
            )
            return 200, {
                "id": "offline-scope-one",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            }

    transport = Transport()
    provider = gateway(transport)
    result = asyncio.run(
        provider.plan(replace(request, native_dispatch=provider.native_planner_route()))
    )
    assert len(transport.calls) == 1
    payload = transport.calls[0]["payload"]
    assert payload["max_completion_tokens"] == 4096
    assert payload["response_format"]["json_schema"]["name"] == SCOPE_PROFILE
    assert "false flags" in json.dumps(payload["messages"])
    assert (
        result.provider_envelope["native_planner_dispatch"]["transport_attempts"] == 1
    )
    if failure == "none":
        assert result.error_code is None
        assert validate_scope_response(result.payload, request)["accepted_local_scope"]
    else:
        assert result.error_code
