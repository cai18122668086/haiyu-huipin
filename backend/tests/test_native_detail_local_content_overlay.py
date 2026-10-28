"""Native owned forks bind local copy/geometry to actual render and review requests."""

import json
from copy import deepcopy

import pytest

from app.aurora_detail_runtime import AuroraDetailHarnessRunner
from app.domestic_detail.plans import canonical_hash
from tests.test_native_detail_local_adjustment import inventory
from tests.test_native_detail_typography_override import (
    bound as bound,
)
from tests.test_native_detail_typography_override import (
    fork,
    publish,
    write,
)
from tests.test_native_detail_typography_override import (
    native as native,
)

pytest_plugins = (
    "tests.test_native_detail_delivery_manifest",
    "tests.test_native_detail_failure_transition",
)


@pytest.mark.parametrize("kind", ["COPY_CONTENT", "VISUAL_DETAIL"])
def test_actual_native_fork_render_and_review_share_the_local_content_delta(
    bound, native, kind
):
    source_root, source, root, args, intent, context, observation = bound
    original = inventory(source_root)
    module = source["modules"][0]["plan"]
    if kind == "COPY_CONTENT":
        path = "/visible_copy"
        old = deepcopy(module["visible_copy"])
        new = deepcopy(old)
        new[0]["text"] = "握持即可感受凉意"
        instruction = (
            "仅把本屏第一句改为握持即可感受凉意，保留原有事实、锚点与其他屏幕。"
        )
        effect = "REPHRASE_CONFIRMED"
        fact = source["input"]["verified_facts"][0]
        refs = [{"id": fact["id"], "text": fact["text"]}]
        context["approved_authority"]["original_fact_ledger"] = [
            {"fact": refs[0], "approved_disposition": "LEAD"}
        ]
    else:
        path = "/module_spatial_structure_contract/zones"
        old = deepcopy(module["module_spatial_structure_contract"]["zones"])
        new = deepcopy(old)
        new[0]["geometry"] = "同一主导区内略向右侧偏置"
        instruction = (
            "仅将本屏现有主区略向右侧偏置，保留区域内容、权重、商品事实与其他屏幕。"
        )
        effect, refs = "PRESERVE", []
    intent["adjustment"].update(
        change_kind=kind, verbatim_instruction=instruction, change_only=[instruction]
    )
    intent["sha256"] = canonical_hash(
        {k: v for k, v in intent.items() if k != "sha256"}
    )
    write(args["revision-intent"], intent)
    context.update(
        intent_sha256=intent["sha256"], adjustment=deepcopy(intent["adjustment"])
    )
    authority = context["approved_authority"]
    authority["sha256"] = canonical_hash(
        {k: v for k, v in authority.items() if k != "sha256"}
    )
    context["sha256"] = canonical_hash(
        {k: v for k, v in context.items() if k != "sha256"}
    )
    observation.update(
        intent_sha256=intent["sha256"],
        adjustment_sha256=canonical_hash(intent["adjustment"]),
        context_sha256=context["sha256"],
    )
    observation["changes"][0].update(
        original_change=instruction, claim_effect=effect, fact_refs=refs
    )
    observation["contract_overrides"] = [
        {
            "module_id": "M01",
            "change_index": 0,
            "path": path,
            "original_value": old,
            "replacement_value": new,
        }
    ]
    publish(root, context, observation)
    fork(bound)
    runner = AuroraDetailHarnessRunner()
    current = json.loads(args["session"].read_bytes())
    assert current["modules"][0]["plan"] == module
    assert "local_contract_override" not in current["modules"][1]
    projection = runner.run(
        "inspect-pair-review-contracts",
        root,
        {"session": args["session"], "pair-id": "P01"},
    )["module_contracts"][0]
    effective = projection["contract"]
    if kind == "COPY_CONTENT":
        assert effective["visible_copy"] == new
        assert effective["copy_intent"]["text_to_visual_bindings"] == [
            {"text": row["text"], "bound_to_visual": row["anchor"]} for row in new
        ]
        expected_prompt = new[0]["text"]
    else:
        assert effective["module_spatial_structure_contract"]["zones"] == new
        expected_prompt = new[0]["geometry"]
    request = runner.run(
        "generate-next",
        root,
        {"session": args["session"], "out": root / "content-request.json"},
    )
    assert expected_prompt in request["prompt"]
    assert (
        request["approved_module_contracts"][0]["contract_sha256"]
        == projection["contract_sha256"]
    )
    assert (
        request["local_contract_bindings"][0]["effective_plan_sha256"]
        == projection["contract_sha256"]
    )
    assert inventory(source_root) == original
