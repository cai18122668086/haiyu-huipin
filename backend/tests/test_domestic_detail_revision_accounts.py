"""Actual selected configurations are inspected without touching an external account."""

import asyncio
import json
from dataclasses import replace

import pytest

from app.domestic_detail.planner_dispatch import route_descriptor
from app.domestic_detail.revision_accounts import configured_revision_accounts
from app.domestic_detail.revision_protocol import RevisionScopeError
from app.domestic_detail.revision_scope import SCOPE_JOURNAL, run_scope_check
from app.xinghe_agent import FakeXingheGateway, TengliOpenAICompatibleGateway
from tests.test_domestic_detail_revision_scope import context_fixture, model_result


class NoTransport:
    async def post_json(self, **kwargs):
        raise AssertionError("Configuration inspection must never call a provider")

    async def post_multipart(self, **kwargs):
        raise AssertionError("Configuration inspection must never call a provider")


def gateway():
    return TengliOpenAICompatibleGateway(
        api_key="fixture-base-secret",
        base_url="https://base.example.invalid/",
        planner_api_key="fixture-review-secret",
        planner_base_url="https://review.example.invalid/",
        image_api_key="fixture-image-secret",
        image_base_url="https://image.example.invalid/",
        planner_model="scope-fixture",
        image_model="image-fixture",
        transport=NoTransport(),
    )


def test_actual_primary_configuration_is_bound_without_remote_availability_claim():
    actual = gateway()
    value = configured_revision_accounts(actual)
    assert value["configuration_checked"] and value["kind"] == "http"
    assert not value["remote_authorization_verified"]
    assert not value["remote_balance_verified"]
    assert actual.external_network_request_count == 0
    assert "secret" not in json.dumps(value) and "example.invalid" not in json.dumps(
        value
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("planner_api_key", ""),
        ("image_api_key", ""),
        ("image_api_key", "  "),
        ("planner_api_key", "secret\nheader"),
        ("planner_model", ""),
        ("image_model", ""),
        ("planner_base_url", ""),
        ("image_base_url", "file:///local"),
        ("planner_base_url", "https:///missing"),
        ("image_base_url", "https://user:secret@example.invalid/"),
        ("image_base_url", "https://image.example.invalid/?token=secret"),
        ("planner_base_url", "https://review.example.invalid/#fragment"),
        ("image_base_url", "https://image.example.invalid:99999/"),
        ("image_base_url", "https://image.example.invalid/a path"),
    ],
)
def test_missing_or_malformed_actual_route_rejected_even_with_nonempty_fingerprints(
    field, value
):
    actual = gateway()
    original = actual._provider_routes["primary"]
    actual._provider_routes["primary"] = replace(original, **{field: value})
    with pytest.raises(RevisionScopeError) as exc:
        configured_revision_accounts(actual)
    assert exc.value.code == "NATIVE_DETAIL_REVISION_ACCOUNTS_UNCONFIGURED"
    assert "secret" not in str(exc.value) and "example.invalid" not in str(exc.value)
    assert actual.external_network_request_count == 0


@pytest.mark.parametrize(
    "tamper",
    [
        "empty_review",
        "empty_image",
        "many_images",
        "stale_review",
        "selector_error",
        "missing_selector",
    ],
)
def test_unavailable_or_inconsistent_actual_selectors_cannot_authorize_a_new_fee(
    tamper,
):
    actual = gateway()
    if tamper == "empty_review":
        actual._ordered_routes = lambda: ()
    elif tamper == "empty_image":
        actual._image_routes = lambda: ()
    elif tamper == "many_images":
        actual._image_routes = lambda: (actual._provider_routes["primary"],) * 2
    elif tamper == "stale_review":
        actual.native_planner_route = lambda: route_descriptor(
            provider="fixture",
            route="stale",
            model="different",
            endpoint="https://stale.example.invalid/",
            credential="fixture-secret",
        )
    elif tamper == "missing_selector":
        actual._image_routes = None
    else:

        def broken():
            raise ValueError("secret provider diagnostic")

        actual._ordered_routes = broken
    with pytest.raises(RevisionScopeError) as exc:
        configured_revision_accounts(actual)
    assert "secret" not in str(exc.value)
    assert actual.external_network_request_count == 0


def test_review_rotation_checks_selected_review_route_and_primary_image_separately():
    actual = TengliOpenAICompatibleGateway(
        api_key="fixture-base",
        base_url="https://base.example.invalid/",
        planner_model="scope-fixture",
        image_model="image-fixture",
        failover_enabled=True,
        failover_api_key="fixture-alternate",
        rotation_order="failover,primary",
        transport=NoTransport(),
    )
    value = configured_revision_accounts(actual)
    assert value["review_dispatch_sha256"] == actual.native_planner_route()["sha256"]
    actual._provider_routes["primary"] = replace(
        actual._provider_routes["primary"], image_api_key=""
    )
    with pytest.raises(RevisionScopeError):
        configured_revision_accounts(actual)
    assert actual.external_network_request_count == 0


def test_explicit_offline_gateway_needs_no_external_account():
    value = configured_revision_accounts(FakeXingheGateway())
    assert value["kind"] == "synthetic"
    assert not value["remote_authorization_verified"]


def test_new_guard_precedes_request_marker_and_does_not_repeat_on_paid_recovery(
    tmp_path,
):
    context, _ = context_fixture()
    root = tmp_path / "job_scope"
    actual = FakeXingheGateway()
    calls, checked, accounts = [], [], {}
    unavailable = True

    async def plan(request):
        assert checked and list((root / "planner-calls").glob("*.request.json"))
        calls.append(request)
        return model_result(request)

    actual.plan = plan

    def check():
        checked.append(True)
        assert not list((root / "planner-calls").glob("*.request.json"))
        if unavailable:
            raise RevisionScopeError("ACCOUNT_FIXTURE", "Unavailable fixture account")
        configured_revision_accounts(actual)

    def account(result, key):
        accounts.setdefault(key, result)

    def run():
        return asyncio.run(
            run_scope_check(
                root=root,
                workspace_id="ws_scope",
                job_id="job_scope",
                context=context,
                gateway=actual,
                before_dispatch=lambda: None,
                before_new_dispatch=check,
                account_result=account,
            )
        )

    with pytest.raises(RevisionScopeError):
        run()
    assert not calls and not accounts and not (root / SCOPE_JOURNAL).exists()
    assert not list((root / "planner-calls").glob("*.request.json"))
    unavailable = False
    first = run()
    assert len(calls) == 1 and len(accounts) == 1 and len(checked) == 2
    unavailable = True
    next((root / "planner-calls").glob("*.reply.txt")).unlink()
    assert run() == first
    assert len(calls) == 1 and len(accounts) == 1 and len(checked) == 2
    assert next((root / "planner-calls").glob("*.reply.txt")).exists()
