"""Native compiler prompts remain byte-exact and isolated from first fast A."""

import asyncio
import hashlib
from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from app.aurora_main_image_fast_provider import (
    NATIVE_SCHEMA_VERSION,
    NATIVE_SKILL_ID,
    NATIVE_VERSION,
    SCHEMA_VERSION,
    SKILL_ID,
    VERSION,
    FastMainImageProvider,
    generation_hash,
    image_prompt,
)
from app.errors import ApiError
from app.models import AssetRecord
from app.provider import ProviderOutcome

from .test_aurora_main_image_fast_provider import Gateway, persist, receipt, setup
from .test_replica import upload


def test_native_canvas_gate_matches_exact_square_harness_without_changing_a():
    output = BytesIO()
    Image.new("RGB", (1025, 1024), "white").save(output, format="PNG")
    data = output.getvalue()
    # A retains its existing ratio tolerance; B must fail before publication,
    # rather than leave six successful files blocked at the native finalizer.
    assert FastMainImageProvider._artifact(data, [1024, 1024]).width == 1025
    with pytest.raises(ApiError):
        FastMainImageProvider._artifact(data, [1024, 1024], strict_square=True)


def native_setup(client, monkeypatch, gateway=None):
    provider, gateway, _, original = setup(client, monkeypatch, gateway)
    extra = upload(client, "blue")
    plan = deepcopy(original)
    plan.update(
        {
            "version": NATIVE_VERSION,
            "skill_id": NATIVE_SKILL_ID,
            "product_identity_lock": {"primary_product": "blue original package"},
            "style_lock": {"palette": "blue and white", "lighting": "soft side light"},
        }
    )
    with client.app.state.session_factory() as session:
        asset = session.get(AssetRecord, extra)
        plan["product_asset_ids"].append(extra)
        plan["source_bindings"].append(
            {
                "asset_id": extra,
                "sha256": asset.sha256,
                "width": asset.width,
                "height": asset.height,
            }
        )
    # The original compiler has selected its edit target first, independently
    # from upload order. These exact ordered IDs are frozen in each page.
    for page in plan["pages"]:
        page.pop("visual_plan")
        page.pop("visible_copy")
        page.pop("forbidden_elements")
        page["source_asset_ids"] = list(reversed(plan["product_asset_ids"]))
        page["prompt"] = (
            f"\n原生第 {page['page_index']} 页完整提示词。\n  保留中文和尾部空白。  \n"
        )
    request = native_persist(client, plan)
    return provider, gateway, request, plan


def native_persist(client, plan, **kwargs):
    return persist(
        client,
        plan,
        speed="native_fast",
        schema_version=NATIVE_SCHEMA_VERSION,
        **kwargs,
    )


def test_first_fast_template_matches_unchanged_a_byte_digest():
    plan = {
        "version": VERSION,
        "product_identity_lock": "A lock",
        "user_facts": ["A fact"],
        "style_lock": "A style",
        "canvas": [1024, 1024],
    }
    page = {
        "page_index": 0,
        "page_role": "SEARCH_MAIN_IMAGE_LISTING_COVER",
        "visual_plan": "A composition",
        "visible_copy": ["A copy"],
        "forbidden_elements": "A ban",
    }
    assert hashlib.sha256(image_prompt(plan, page).encode()).hexdigest() == (
        "eff7a0af9c39d45467a6e6b1fb9ec2b338831553d85e0442e7dff424d70566be"
    )


def test_native_prompt_exact_bytes_ordered_sources_and_high_quality(
    client, monkeypatch
):
    provider, gateway, request, plan = native_setup(client, monkeypatch)
    validations = []

    def validate(self, skill_id):
        validations.append(skill_id)
        return "a" * 64

    monkeypatch.setattr(
        "app.aurora_main_image_fast_provider.BundledSkillManifestValidator.validate",
        validate,
    )
    result = asyncio.run(provider.execute(request))
    assert result.outcome == ProviderOutcome.SUCCEEDED
    assert validations == [NATIVE_SKILL_ID]
    assert result.provider_name == gateway.provider_name
    assert result.model_name == gateway.image_model
    assert result.raw_usage["main_image_speed"] == "native_fast"
    assert result.raw_usage["metrics"]["skill_id"] == NATIVE_SKILL_ID
    submitted = gateway.requests[0]
    assert submitted.prompt.encode() == plan["pages"][0]["prompt"].encode()
    assert submitted.prompt == image_prompt(plan, plan["pages"][0])
    assert [s.asset_id for s in submitted.sources] == plan["pages"][0][
        "source_asset_ids"
    ]
    assert submitted.quality == submitted.input_fidelity == "high"
    assert submitted.single_dispatch and submitted.preserve_provider_artifact
    assert (
        receipt(client, request)["0"]["prompt_hash"]
        == hashlib.sha256(submitted.prompt.encode()).hexdigest()
    )


@pytest.mark.parametrize("part", ["speed", "schema", "version", "skill"])
def test_variants_cannot_mix_plan_or_selection_bindings(client, monkeypatch, part):
    provider, gateway, _, plan = native_setup(client, monkeypatch)
    if part == "version":
        plan["version"] = VERSION
    elif part == "skill":
        plan["skill_id"] = SKILL_ID
    request = persist(
        client,
        plan,
        speed="fast" if part == "speed" else "native_fast",
        schema_version=SCHEMA_VERSION if part == "schema" else NATIVE_SCHEMA_VERSION,
    )
    assert asyncio.run(provider.execute(request)).outcome == ProviderOutcome.FAILED
    assert not gateway.requests


@pytest.mark.parametrize(
    "bad_ids", [[], ["foreign-asset"], ["duplicate", "duplicate"], [None], "not-a-list"]
)
def test_native_sources_must_be_nonempty_unique_authorized_ids(
    client, monkeypatch, bad_ids
):
    provider, gateway, _, plan = native_setup(client, monkeypatch)
    plan["pages"][0]["source_asset_ids"] = bad_ids
    request = native_persist(client, plan)
    result = asyncio.run(provider.execute(request))
    assert result.error_code == "FAST_MAIN_IMAGE_SOURCE_CHANGED"
    assert not gateway.requests


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt", " \n"),
        ("prompt", None),
        ("product_identity_lock", "wrong-type"),
        ("style_lock", {}),
    ],
)
def test_native_prompt_and_structured_locks_are_required(
    client, monkeypatch, field, value
):
    provider, gateway, _, plan = native_setup(client, monkeypatch)
    if field == "prompt":
        plan["pages"][0][field] = value
    else:
        plan[field] = value
    result = asyncio.run(provider.execute(native_persist(client, plan)))
    assert result.outcome == ProviderOutcome.FAILED
    assert not gateway.requests


def test_native_output_reused_after_delivery_failure_and_prompt_tamper_blocked(
    client, monkeypatch
):
    provider, gateway, request, plan = native_setup(client, monkeypatch)
    first = asyncio.run(provider.execute(request))
    retry = native_persist(
        client,
        {**plan, "metrics": {"planning_reused": True}},
        retry_of=request.job_id,
        receipts=receipt(client, request),
    )
    second = asyncio.run(provider.execute(retry))
    assert second.artifact.payload == first.artifact.payload
    assert second.actual_cost_micros == second.raw_usage["network_requests"] == 0
    assert len(gateway.requests) == 1
    changed = deepcopy(plan)
    changed["pages"][0]["prompt"] += " changed"
    assert generation_hash(changed) != generation_hash(plan)
    tampered = native_persist(
        client, changed, retry_of=request.job_id, receipts=receipt(client, request)
    )
    assert (
        asyncio.run(provider.execute(tampered)).error_code
        == "FAST_MAIN_IMAGE_PLAN_CHANGED"
    )
    assert len(gateway.requests) == 1


def test_native_unknown_submission_refuses_new_retry_dispatch(client, monkeypatch):
    provider, gateway, request, plan = native_setup(
        client, monkeypatch, Gateway(outcome="timeout")
    )
    assert (
        asyncio.run(provider.execute(request)).outcome
        == ProviderOutcome.OUTCOME_UNKNOWN
    )
    retry = native_persist(
        client, plan, retry_of=request.job_id, receipts=receipt(client, request)
    )
    assert asyncio.run(provider.execute(retry)).error_code == "PROVIDER_OUTCOME_UNKNOWN"
    assert len(gateway.requests) == 1
