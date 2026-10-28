import asyncio
import hashlib
import json
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.provider import ProviderOutcome
from app.replica_native import (
    NativeReplicaRun,
    ReplicaNativeError,
    ReplicaPlanAuditRejected,
    validate_module_constraints,
)
from app.replica_native_provider import (
    NativeReplicaProvider,
    ReplicaImageOutcomeUnknown,
)

from .test_replica_transport import run_at


def constraint():
    return {
        "modules": [
            {
                "module_id": "M003",
                "reference_sha256": "a" * 64,
                "required_identity_checks": [
                    "Visible food must preserve its real irregular granular form"
                ],
                "not_applicable_checks": [
                    "No package here: hidden package labels are inapplicable"
                ],
                "layout_anchors": [{"label": "headline", "bbox": [80, 0, 920, 240]}],
                "composition_notes": (
                    "Preserve headline scale and supporting evidence positions."
                ),
            }
        ]
    }


@pytest.mark.parametrize(
    "bad",
    [
        "hash",
        "missing",
        "duplicate",
        "coordinates",
        "empty_scope",
        "missing_notes",
        "missing_label",
    ],
)
def test_untrusted_module_scope_must_cover_current_sources_and_valid_pixel_regions(bad):
    value = constraint()
    assert validate_module_constraints(value, {"M003": "a" * 64})["M003"][
        "not_applicable_checks"
    ]
    invalid = deepcopy(value)
    if bad == "hash":
        invalid["modules"][0]["reference_sha256"] = "b" * 64
    elif bad == "missing":
        invalid["modules"] = []
    elif bad == "duplicate":
        invalid["modules"] *= 2
    elif bad == "coordinates":
        invalid["modules"][0]["layout_anchors"][0]["bbox"] = [0, 0, 1001, 500]
    elif bad == "empty_scope":
        invalid["modules"][0]["required_identity_checks"] = []
        invalid["modules"][0]["not_applicable_checks"] = []
    elif bad == "missing_notes":
        invalid["modules"][0].pop("composition_notes")
    else:
        invalid["modules"][0]["layout_anchors"][0].pop("label")
    with pytest.raises(ReplicaNativeError):
        validate_module_constraints(invalid, {"M003": "a" * 64})


@pytest.mark.parametrize("rejections", [0, 1, 2, 3])
def test_prepare_binds_scope_to_approved_crop_and_repairs_invalid_metadata(
    tmp_path,
    rejections,
):
    run = run_at(tmp_path, "exact", SimpleNamespace())
    run.context = {
        "user_facts": "Explicitly provided product facts",
        "references": [{"asset_id": "original-reference"}],
    }
    product, reference = tmp_path / "product.png", tmp_path / "reference.png"
    Image.new("RGB", (100, 120), "red").save(product)
    Image.new("RGB", (100, 200), "blue").save(reference)
    events, asks = [], []
    plans = []
    audit_calls = 0
    failed_audit = {
        "unit_audits": [
            {
                "unit_id": "R001",
                "decision": "FAIL",
                "material_mismatches": [
                    "Correct only the observed synthetic text tier"
                ],
            }
        ]
    }

    def cli(command, **kwargs):
        events.append(command)
        return {}

    async def contract(stage, instruction, context, *args, **kwargs):
        nonlocal audit_calls
        events.append(stage)
        if stage.startswith("exact-plan-audit"):
            assert context["user_facts"] == "Explicitly provided product facts"
            assert context["product_evidence"][0]["product_index"] == 0
            audit_calls += 1
            if audit_calls <= rejections:
                raise ReplicaPlanAuditRejected(deepcopy(failed_audit))
            return {"pass": True}
        assert context["previous_plan"] == (plans[-1] if plans else None)
        if plans:
            assert context["prior_visual_audit"] == failed_audit
            assert "use that compiled plan as the repair base" in instruction
        evidence = context["physical_units"][0]
        plan = {
            "modules": [
                {
                    "id": "M003",
                    "reference_unit_id": "R001",
                    "page_duty": "Product contents evidence",
                    "generation_brief": (
                        f"Preserve layout and real contents, revision {len(plans)}"
                    ),
                    "output_canvas": evidence["recommended_canvas"],
                    "copy_units": [],
                }
            ]
        }
        plans.append(deepcopy(plan))
        return plan

    async def ask(stage, instruction, context, **kwargs):
        asks.append((stage, context, kwargs))
        if stage.startswith("exact-product-evidence"):
            return {
                "products": [
                    {
                        "product_index": 0,
                        "source_sha256": context["products"][0]["sha256"],
                        "readable_marks": [],
                        "uncertainty_notes": (
                            "No readable physical marks in this test image."
                        ),
                    }
                ]
            }
        source_hash = hashlib.sha256(
            (tmp_path / "crops/screen-000.png").read_bytes()
        ).hexdigest()
        if stage.startswith("exact-reference-layout"):
            return {
                "units": [
                    {
                        "reference_unit_id": "R001",
                        "reference_sha256": source_hash,
                        "reference_product_present": False,
                        "has_poster_text": False,
                        "layout_anchors": [
                            {"label": "background", "bbox": [0, 0, 1000, 1000]}
                        ],
                        "text_regions": [],
                        "typography_notes": "No authored text in this source.",
                    }
                ]
            }
        value = constraint()
        value["modules"][0].update(product_visibility="context_only", text_bindings=[])
        value["modules"][0]["reference_sha256"] = (
            "0" * 64 if len(asks) == 3 else source_hash
        )
        return value

    run.cli, run.contract, run.ask = cli, contract, ask
    if rejections == 3:
        with pytest.raises(ReplicaPlanAuditRejected) as failure:
            asyncio.run(
                NativeReplicaRun._prepare_exact(
                    run,
                    [product],
                    [reference],
                    [
                        {
                            "reference_index": 0,
                            "column_index": 0,
                            "screen_index": 0,
                            "bbox": [0, 0, 100, 200],
                            "screen_boundary_reason": (
                                "One complete physical reference screen"
                            ),
                        }
                    ],
                    ["Observed visible food identity"],
                )
            )
        assert failure.value.judgment == failed_audit
        assert len(plans) == audit_calls == 3
        assert "generate-next" not in events
        return
    result = asyncio.run(
        NativeReplicaRun._prepare_exact(
            run,
            [product],
            [reference],
            [
                {
                    "reference_index": 0,
                    "column_index": 0,
                    "screen_index": 0,
                    "bbox": [0, 0, 100, 200],
                    "screen_boundary_reason": "One complete physical reference screen",
                }
            ],
            ["Observed visible food identity"],
        )
    )
    assert events == [
        "start",
        *[
            stage
            for revision in range(rejections + 1)
            for stage in (f"exact-plan-r{revision}", f"exact-plan-audit-r{revision}")
        ],
    ]
    assert len(asks) == 4 and asks[3][1]["validation_error"]
    assert asks[0][2]["paths"] == [product]
    assert asks[1][2]["paths"] == [tmp_path / "crops/screen-000.png"]
    assert "plan" not in asks[0][1] and "plan" not in asks[1][1]
    assert asks[3][2]["paths"] == [product, tmp_path / "crops/screen-000.png"]
    assert result[0]["canvas"] == {"width": 1024, "height": 2048}
    saved = json.loads((tmp_path / "context.json").read_text(encoding="utf-8"))
    assert (
        saved["module_constraints"]["M003"] == run.context["module_constraints"]["M003"]
    )
    assert "generate-next" not in events


@pytest.mark.parametrize("unknown_second", [False, True])
def test_known_bad_canvas_can_retry_but_unknown_response_never_dispatches_again(
    tmp_path, unknown_second
):
    calls = []
    original_outputs = []

    async def edit(request):
        calls.append(request)
        outcome = (
            ProviderOutcome.OUTCOME_UNKNOWN
            if unknown_second and len(calls) == 2
            else ProviderOutcome.SUCCEEDED
        )
        data = BytesIO()
        Image.new("RGB", (64, 64) if len(calls) == 1 else (64, 80), "pink").save(
            data, "PNG"
        )
        original_outputs.append(data.getvalue())
        return SimpleNamespace(
            outcome=outcome,
            artifact=SimpleNamespace(payload=data.getvalue(), locally_resampled=False),
            normalized_usage={"reported": False},
            actual_cost_micros=None,
            estimated_cost_micros=None,
            http_status=200,
            error_code=None,
            raw_usage={},
            error_message="Unknown request outcome",
        )

    run = run_at(tmp_path, "exact", SimpleNamespace(edit=edit))
    provider = NativeReplicaProvider(None, run.gateway, None)

    async def save(*args, **kwargs):
        return None

    provider._save = save
    source = tmp_path / "blueprint.png"
    Image.new("RGB", (64, 80), "blue").save(source)
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_fixture",
        job_item_id="item_fixture",
        attempt_id="attempt_fixture",
    )

    def render():
        return provider._render(
            run,
            request,
            {"token": "canvas-test"},
            "Bound native prompt",
            [source],
            [64, 80],
            "M003",
        )

    if unknown_second:
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(render())
        with pytest.raises(ReplicaImageOutcomeUnknown):
            asyncio.run(render())
    else:
        result = asyncio.run(render())
        assert result.read_bytes() == original_outputs[1]
        assert asyncio.run(render()) == result
        record = json.loads((tmp_path / "invocations.json").read_text())["canvas-test"]
        assert [row["accepted_canvas"] for row in record["canvas_attempts"]] == [
            False,
            True,
        ]
    assert len(calls) == 2
    assert calls[0].idempotency_key != calls[1].idempotency_key
    assert "FORMAT CORRECTION" in calls[1].prompt
    assert len(run.usage) == 2


def test_unusual_canvas_has_a_separately_bound_format_guide_and_original_bytes(
    tmp_path,
):
    calls = []
    data = BytesIO()
    Image.new("RGB", (1024, 1866), "pink").save(data, "PNG")

    async def edit(request):
        calls.append(request)
        return SimpleNamespace(
            outcome=ProviderOutcome.SUCCEEDED,
            artifact=SimpleNamespace(payload=data.getvalue(), locally_resampled=False),
            normalized_usage={},
            actual_cost_micros=None,
            estimated_cost_micros=None,
            http_status=200,
            error_code=None,
            raw_usage={},
        )

    run = run_at(tmp_path, "exact", SimpleNamespace(edit=edit))
    run.context = {"module_constraints": {"M003": constraint()["modules"][0]}}
    run.context["module_constraints"]["M003"].update(
        product_visibility="context_only",
        text_bindings=[{"region_id": "T1", "render_text": "Approved scene copy"}],
    )
    provider = NativeReplicaProvider(None, run.gateway, None)

    async def save(*args, **kwargs):
        return None

    provider._save = save
    source = tmp_path / "reference.png"
    Image.new("RGB", (64, 80), "blue").save(source)
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_fixture",
        job_item_id="item_fixture",
        attempt_id="attempt_fixture",
    )
    path = asyncio.run(
        provider._render(
            run,
            request,
            {"token": "guide-test"},
            "Native prompt",
            [source],
            [1024, 1866],
            "M003",
        )
    )
    assert path.read_bytes() == data.getvalue()
    assert calls[0].sources[-1].source_role == "format_canvas"
    with Image.open(BytesIO(calls[0].sources[-1].payload)) as guide:
        assert guide.size == (1024, 1866)
    assert "not_applicable_checks" in calls[0].prompt
    assert "TEXT SLOT EXECUTION" in calls[0].prompt
    assert "CONTEXT-ONLY SCENE" in calls[0].prompt
    assert "Approved scene copy" in calls[0].prompt
    assert len(calls) == 1
