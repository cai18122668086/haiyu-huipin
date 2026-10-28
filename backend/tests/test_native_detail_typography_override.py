"""Original plan and raw intent survive one source-bound local layout override."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy

import pytest

from app.aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
)
from app.domestic_detail.atomic_review import synthetic_module_judgments
from app.domestic_detail.plans import canonical_hash
from tests.domestic_detail_atomic_fixtures import minimal_atomic_plan, review_context
from tests.test_native_detail_failure_transition import audit, record_fixture
from tests.test_native_detail_local_adjustment import inventory
from tests.test_native_detail_local_revision_fork import fork_fixture
from tests.test_native_detail_revision_pixel_review import (
    observation as local_pixel_observation,
)

pytest_plugins = (
    "tests.test_native_detail_delivery_manifest",
    "tests.test_native_detail_failure_transition",
)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def native():
    root = AuroraDetailHarnessRunner().skill_root
    spec = importlib.util.spec_from_file_location(
        "native_typography_fixture", root / "tests/test_regressions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from runtime.local_contracts import effective_plan, scope_contract_override

    return module, effective_plan, scope_contract_override


@pytest.fixture
def bound(accepted_delivery, native, pair_audit):
    module, _, _ = native
    portable = module.V4ContractTests()._portable_approval_session()
    original_root, _, state = accepted_delivery
    state["input"] = portable["input"]
    state["identity"] = portable["identity"]
    state["plan"] = portable["plan"]
    state["plan"]["output_strategy"].update(module_height_px=4, canvas_width_px=3)
    state["plan"]["output_strategy"]["continuous_pair_rendering"][
        "pair_canvas_height_px"
    ] = 8
    state["approval_document"] = {"sha256": "d" * 64}
    for item, plan in zip(state["modules"], state["plan"]["modules"], strict=True):
        transition = deepcopy(plan["transition_contract"])
        complete = deepcopy(module.route_aware_plan()["modules"][0])
        complete.update(module.v43_prompt_module(1))
        plan.clear()
        plan.update(complete)
        plan.update(id=item["id"], order=item["order"], transition_contract=transition)
        for key, value in minimal_atomic_plan(
            item["id"], item["order"], "BACKGROUND_STRINGING"
        ).items():
            plan.setdefault(key, deepcopy(value))
        plan["expression_signature"] = "+".join(
            str(plan[key])
            for key in (
                "primary_subject_class",
                "presentation_form",
                "camera_scale",
                "complete_product_role",
            )
        )
        plan["output"].update(width_px=3, height_px=4)
        plan["text_system"].update(
            primary_mode="centered_hero_headline", anchor_zone="center"
        )
        item["plan"] = plan
    # The original targeted pair has real signed source/canvas/slice lineage,
    # not the older delivery fixture's placeholder pixel history.
    state.update(session_dir=str(original_root), current_index=0)
    for item in state["modules"][:2]:
        item.update(cycle_attempt_count=0, attempts=[])
    state = record_fixture(
        original_root, state, original_root / "P01.png", "original-signed-layout-source"
    )
    context = review_context(original_root)
    judgment = deepcopy(pair_audit[2])
    judgment.update(status="PASS", material_failures=[])
    judgment["artifact_binding"]["image_sha256"] = context["pixel_lineage"]["canvas"][
        "sha256"
    ]
    judgment["internal_seam_evidence"]["method_id"] = state["modules"][1]["plan"][
        "transition_contract"
    ]["method_id"]
    judgment["module_judgments"] = synthetic_module_judgments(
        context,
        context["pixel_lineage"]["canvas"]["sha256"],
        "2026-10-18T02:00:00+00:00",
    )
    _, state = audit((original_root, state, judgment))
    accepted_delivery = (original_root, original_root / "session.json", state)
    source_root, source, root, args, intent = fork_fixture(accepted_delivery, index=0)
    selected = intent["adjustment"]["module_id"]
    pair = source["plan"]["output_strategy"]["continuous_pair_rendering"]["pair_ids"][0]
    authority = {
        "compiled_input_sha256": canonical_hash(source["input"]),
        "compiled_identity_sha256": canonical_hash(source["identity"]),
        "compiled_plan_sha256": canonical_hash(source["plan"]),
    }
    authority["sha256"] = canonical_hash(authority)
    context = {
        "version": "domestic-detail-revision-scope-v1",
        "intent_sha256": intent["sha256"],
        "adjustment": intent["adjustment"],
        "approved_plan": source["plan"],
        "approved_authority": authority,
        "selected_module_id": selected,
        "pair_id": pair["pair_id"],
        "module_ids": pair["module_ids"],
        "effective_module_contracts": [
            {
                "module_id": m["id"],
                "contract": m["plan"],
                "contract_sha256": canonical_hash(m["plan"]),
            }
            for m in source["modules"]
            if m["id"] in pair["module_ids"]
        ],
    }
    context["sha256"] = canonical_hash(context)
    observation = {
        "version": context["version"],
        "context_sha256": context["sha256"],
        "intent_sha256": intent["sha256"],
        "adjustment_sha256": canonical_hash(intent["adjustment"]),
        "selected_module_id": selected,
        "pair_id": pair["pair_id"],
        "decision": "LOCAL",
        "dimensions": [
            {
                "dimension": key,
                "state": "UNCHANGED",
                "reason": "Synthetic original source comparison",
            }
            for key in [
                "identity",
                "facts",
                "target_buyer",
                "campaign_style",
                "module_sequence",
            ]
        ],
        "other_screens": {
            "state": "PRESERVED",
            "reason": "Synthetic original sibling retained",
        },
        "changes": [
            {
                "index": 0,
                "original_change": intent["adjustment"]["change_only"][0],
                "scope": "LOCAL",
                "claim_effect": "PRESERVE",
                "contract_effect": "OVERRIDE_LOCAL_FIELDS",
                "fact_refs": [],
                "reason": "Synthetic exact local typography instruction",
            }
        ],
        "contract_overrides": [
            {
                "module_id": selected,
                "change_index": 0,
                "path": "/text_system/primary_mode",
                "original_value": "centered_hero_headline",
                "replacement_value": "offset_editorial_headline",
            },
            {
                "module_id": selected,
                "change_index": 0,
                "path": "/text_system/anchor_zone",
                "original_value": "center",
                "replacement_value": "edge_left",
            },
        ],
    }
    publish(root, context, observation)
    return source_root, source, root, args, intent, context, observation


def publish(root, context, observation):
    semantic = {"profile": "aurora_detail_revision_scope", "semantics": context}
    binding = {
        "phase": "aurora_detail_revision_scope",
        "semantic_request": semantic,
        "semantic_sha256": canonical_hash(semantic),
    }
    result = {
        "error_code": None,
        "payload": observation,
        "audit_raw_content": json.dumps(observation, ensure_ascii=False),
    }
    journal = {
        "version": "domestic-detail-local-scope-result-v1",
        "binding": binding,
        "result": result,
        "result_sha256": canonical_hash(result),
    }
    journal["sha256"] = canonical_hash(journal)
    journal_sha = write(root / "local-scope-model-result.json", journal)
    scope = {
        "version": context["version"],
        "original_observation": observation,
        "accepted_local_scope": True,
        "requires_replan": False,
        "binding_sha256": canonical_hash(binding),
        "context_sha256": context["sha256"],
        "original_journal_file_sha256": journal_sha,
    }
    write(root / "local-revision-scope.json", scope)


def fork(bound):
    return AuroraDetailHarnessRunner().run("fork-local-revision", bound[2], bound[3])


def test_native_prompt_and_review_share_effective_contract_without_mutating_original(
    bound, native
):
    source_root, source, root, args, intent, _, _ = bound
    original = inventory(source_root)
    fork(bound)
    current = json.loads(args["session"].read_bytes())
    target, sibling = current["modules"][:2]
    assert target["plan"] == source["modules"][0]["plan"]
    assert target["user_adjustment"] == intent["adjustment"]
    assert (
        sibling["plan"] == source["modules"][1]["plan"]
        and "local_contract_override" not in sibling
    )
    projected = AuroraDetailHarnessRunner().run(
        "inspect-pair-review-contracts",
        root,
        {"session": args["session"], "pair-id": "P01"},
    )
    contract = projected["module_contracts"][0]
    assert (
        contract["contract"]["text_system"]["primary_mode"]
        == "offset_editorial_headline"
    )
    assert contract["contract"]["text_system"]["anchor_zone"] == "edge_left"
    assert contract["contract_sha256"] != canonical_hash(target["plan"])
    from runtime.prompt_compiler import compile_prompt

    prompt = compile_prompt(current, target)
    assert (
        "Primary mode: offset_editorial_headline" in prompt
        and "zone: edge_left" in prompt
    )
    assert "Primary mode: centered_hero_headline" not in prompt
    request = AuroraDetailHarnessRunner().run(
        "generate-next",
        root,
        {"session": args["session"], "out": root / "effective-request.json"},
    )
    assert "Primary mode: offset_editorial_headline" in request["prompt"]
    assert (
        request["local_contract_bindings"][0]["effective_plan_sha256"]
        == contract["contract_sha256"]
    )
    assert (
        request["local_contract_bindings"][0]["override_sha256"]
        == target["local_contract_override"]["sha256"]
    )
    assert (
        request["approved_module_contracts"][0]["contract_sha256"]
        == contract["contract_sha256"]
    )
    # Record the actual signed request token, then audit the same effective
    # typography plus the original/candidate local pixel contract.
    from PIL import Image

    candidate = root / "synthetic-candidate.png"
    Image.new("RGB", (3, 8), "#999999").save(candidate)
    runner = AuroraDetailHarnessRunner()
    runner.run(
        "record-generation",
        root,
        {"session": args["session"], "token": request["token"], "image": candidate},
    )
    pixels = review_context(root)
    review_contract = runner.run(
        "inspect-pair-review-contracts",
        root,
        {"session": args["session"], "pair-id": "P01"},
    )["local_revision_contract"]
    judgment = deepcopy(source["modules"][0]["attempts"][-1]["judgment"])
    judgment.update(status="PASS", material_failures=[])
    judgment.pop("effective_passed_layers", None)
    judgment["artifact_binding"]["image_sha256"] = pixels["pixel_lineage"]["canvas"][
        "sha256"
    ]
    judgment["module_judgments"] = synthetic_module_judgments(
        pixels, pixels["pixel_lineage"]["canvas"]["sha256"], "2026-10-18T02:10:00+00:00"
    )
    judgment["local_revision_observation"] = local_pixel_observation(
        review_contract, pixels["pixel_lineage"]
    )
    current = json.loads(args["session"].read_bytes())
    raw = deepcopy(judgment)
    _, accepted = audit((root, current, judgment))
    assert (
        accepted["modules"][0]["attempts"][-1]["judgment"]["module_judgments"]
        == raw["module_judgments"]
    )
    assert accepted["modules"][0]["plan"] == source["modules"][0]["plan"]
    proof = runner.run(
        "inspect-pair-review-evidence",
        root,
        {"session": args["session"], "pair-id": "P01"},
    )
    assert (
        proof["audit_input"]["local_revision_observation"]
        == raw["local_revision_observation"]
    )
    assert inventory(source_root) == original
    assert fork(bound)["reused"] is True
    assert (
        current["approval"]["local_contract_override_sha256"]
        == target["local_contract_override"]["sha256"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "other_module",
        "facts",
        "copy",
        "neighbor",
        "wrong_old",
        "duplicate",
        "invalid_enum",
        "blank",
        "no_effect",
        "restore_with_patch",
        "override_without_patch",
        "global",
        "unknown_dimension",
        "rewrite_change",
        "different_context",
        "different_effective_base",
        "new_claim",
        "scope_result_unknown",
    ],
)
def test_invalid_scope_never_creates_owned_child_or_changes_original(bound, mutation):
    source_root, source, root, args, _, context, observation = bound
    before = inventory(source_root)
    patch = observation["contract_overrides"][0]
    if mutation == "other_module":
        patch["module_id"] = "M02"
    elif mutation == "facts":
        patch["path"] = "/lead_claim_id"
    elif mutation == "copy":
        patch["path"] = "/visible_copy/0/text"
    elif mutation == "neighbor":
        patch["path"] = "/text_system/neighbor_difference"
    elif mutation == "wrong_old":
        patch["original_value"] = "minimal_label"
    elif mutation == "duplicate":
        observation["contract_overrides"].append(deepcopy(patch))
    elif mutation == "invalid_enum":
        patch["replacement_value"] = "automatic_any_layout"
    elif mutation == "blank":
        patch.update(
            path="/text_system/anchor_target",
            original_value=source["modules"][0]["plan"]["text_system"]["anchor_target"],
            replacement_value="   ",
        )
    elif mutation == "no_effect":
        observation["changes"][0].pop("contract_effect")
    elif mutation == "restore_with_patch":
        observation["changes"][0]["contract_effect"] = "RESTORE_OR_PRESERVE_APPROVED"
    elif mutation == "override_without_patch":
        observation["contract_overrides"] = []
    elif mutation == "global":
        observation["decision"] = "GLOBAL"
    elif mutation == "unknown_dimension":
        observation["dimensions"][0]["state"] = "UNKNOWN"
    elif mutation == "rewrite_change":
        observation["changes"][0]["original_change"] = (
            "Different invented user instruction"
        )
    elif mutation == "different_context":
        context["adjustment"] = {**context["adjustment"], "module_id": "M02"}
    elif mutation == "different_effective_base":
        context["effective_module_contracts"][0]["contract_sha256"] = "0" * 64
    elif mutation == "new_claim":
        observation["changes"][0]["claim_effect"] = "REPHRASE_CONFIRMED"
    context["sha256"] = canonical_hash(
        {k: v for k, v in context.items() if k != "sha256"}
    )
    observation["context_sha256"] = context["sha256"]
    publish(root, context, observation)
    if mutation == "scope_result_unknown":
        journal = json.loads((root / "local-scope-model-result.json").read_bytes())
        journal["result"]["error_code"] = "NATIVE_DETAIL_REVISION_SCOPE_OUTCOME_UNKNOWN"
        journal["result_sha256"] = canonical_hash(journal["result"])
        journal["sha256"] = canonical_hash(
            {k: v for k, v in journal.items() if k != "sha256"}
        )
        digest = write(root / "local-scope-model-result.json", journal)
        scope = json.loads((root / "local-revision-scope.json").read_bytes())
        scope["original_journal_file_sha256"] = digest
        write(root / "local-revision-scope.json", scope)
    with pytest.raises(AuroraDetailHarnessError):
        fork(bound)
    assert not args["session"].exists() and inventory(source_root) == before


@pytest.mark.parametrize(
    "file",
    [
        "local-revision-scope.json",
        "local-scope-model-result.json",
        "source-session.json",
        "local-revision-intent.json",
    ],
)
def test_effective_contract_rejects_original_authority_file_tamper(bound, file):
    fork(bound)
    root, args = bound[2:4]
    p = root / file
    p.write_bytes(p.read_bytes() + b" ")
    with pytest.raises(AuroraDetailHarnessError):
        AuroraDetailHarnessRunner().run(
            "inspect-pair-review-contracts",
            root,
            {"session": args["session"], "pair-id": "P01"},
        )


@pytest.mark.parametrize("change_approval", [False, True])
def test_self_rehashed_module_metadata_cannot_replace_original_model_value(
    bound, change_approval
):
    fork(bound)
    root, args = bound[2:4]
    current = json.loads(args["session"].read_bytes())
    override = current["modules"][0]["local_contract_override"]
    override["patches"][0]["replacement_value"] = "minimal_label"
    override["sha256"] = canonical_hash(
        {k: v for k, v in override.items() if k != "sha256"}
    )
    if change_approval:
        current["approval"]["local_contract_override_sha256"] = override["sha256"]
        current["revision_fork"]["local_contract_override_sha256"] = override["sha256"]
    write(args["session"], current)
    with pytest.raises(AuroraDetailHarnessError):
        AuroraDetailHarnessRunner().run(
            "inspect-pair-review-contracts",
            root,
            {"session": args["session"], "pair-id": "P01"},
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "wrong_effective",
        "wrong_override",
        "other_module",
        "missing_sibling",
        "reordered",
    ],
)
def test_effective_layout_cannot_be_rebound_to_another_original_signed_request(
    bound, mutation
):
    from pathlib import Path

    from runtime.local_contracts import validate_request_contracts
    from runtime.state import HarnessError

    fork(bound)
    root, args = bound[2:4]
    runner = AuroraDetailHarnessRunner()
    request = runner.run(
        "generate-next",
        root,
        {"session": args["session"], "out": root / "candidate-request.json"},
    )
    current = json.loads(args["session"].read_bytes())
    entry = current["generation_requests"][-1]
    forged = json.loads(Path(entry["request"]).read_bytes())
    if mutation == "missing":
        forged.pop("local_contract_bindings")
    elif mutation == "wrong_effective":
        forged["local_contract_bindings"][0]["effective_plan_sha256"] = "0" * 64
    elif mutation == "wrong_override":
        forged["local_contract_bindings"][0]["override_sha256"] = "0" * 64
    elif mutation == "other_module":
        forged["local_contract_bindings"][0]["module_id"] = "M02"
    elif mutation == "missing_sibling":
        forged["local_contract_bindings"].pop()
    else:
        forged["local_contract_bindings"].reverse()
    path = root / "forged-request-test-only.json"
    digest = write(path, forged)
    entry["request"] = str(path)
    # Simulate a consistent new receipt hash: the leaf-contract comparison must
    # still reject it, independently of the general native pixel lineage gate.
    lineage = {"token": request["token"], "signed_request_sha256": digest}
    with pytest.raises(HarnessError):
        validate_request_contracts(current, lineage, current["modules"][:2])
