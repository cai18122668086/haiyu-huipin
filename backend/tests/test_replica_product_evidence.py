"""Original product context must reach initial and repair image calls."""

import asyncio
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from app.provider import ProviderOutcome
from app.replica_native import ReplicaInputTooLarge
from app.replica_native_provider import NativeReplicaProvider

from .test_replica_transport import run_at


def png(path, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 80), color).save(path, "PNG")
    return path


def setup(tmp_path, method, repair):
    original = png(tmp_path / "inputs/product-00.img", "blue")
    reference = png(tmp_path / "crops/screen-000.png", "red")
    anchor = png(tmp_path / "crops/product-anchor-00.png", "green")
    paths = [reference, anchor]
    if repair:
        failed = png(tmp_path / "artwork/failed.png", "pink")
        if method == "style":
            paths.append(failed)
        else:
            paths.insert(0, failed)
    events = []

    async def edit(request):
        events.append(request)
        payload = BytesIO()
        Image.new("RGB", (64, 80), "white").save(payload, "PNG")
        return SimpleNamespace(
            outcome=ProviderOutcome.SUCCEEDED,
            artifact=SimpleNamespace(
                payload=payload.getvalue(), locally_resampled=False
            ),
            normalized_usage={"reported": False},
            actual_cost_micros=None,
            estimated_cost_micros=None,
            http_status=200,
            error_code=None,
            raw_usage={},
        )

    run = run_at(tmp_path, method, SimpleNamespace(edit=edit))
    provider = NativeReplicaProvider(None, run.gateway, None)

    async def save(*args, **kwargs):
        pass

    provider._save = save
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_fixture",
        job_item_id="item_fixture",
        attempt_id="attempt_fixture",
    )

    async def render(extra=()):
        return await provider._render(
            run,
            request,
            {
                "token": "fixture",
                "revision_index": int(repair) if method == "style" else 0,
                "mode": "REPAIR" if method == "exact" and repair else "INITIAL",
            },
            (
                "Native fixture prompt. "
                "Reference image 1 is the binding composition blueprint;"
            ),
            paths + list(extra),
            [64, 80],
            "M001",
        )

    return render, events, original, paths, tmp_path


@pytest.mark.parametrize("method", ["exact", "style"])
@pytest.mark.parametrize("repair", [False, True])
def test_original_context_reaches_both_modes_without_changing_native_source_order(
    tmp_path, method, repair
):
    render, events, original, paths, _ = setup(tmp_path, method, repair)
    before = original.read_bytes()
    result = asyncio.run(render())
    assert result.exists() and len(events) == 1
    request = events[0]
    expected = [p.name for p in paths]
    if method == "style" and repair:
        expected.insert(len(expected) - 1, original.name)
        assert request.sources[-1].filename == "failed.png"
        assert request.sources[-1].payload == paths[-1].read_bytes()
    else:
        expected.append(original.name)
    assert [source.filename for source in request.sources] == expected
    product = next(
        source for source in request.sources if source.filename == original.name
    )
    assert product.payload == before == original.read_bytes()
    assert product.source_role == "product_reference"
    expected_prompt = (
        "Native fixture prompt. Reference image 1 is the binding composition blueprint;"
    )
    if method == "exact" and repair:
        expected_prompt = expected_prompt.replace(
            "Reference image 1", "Reference image 2"
        )
    assert request.prompt.startswith(expected_prompt + "\n\nIMAGE_BINDINGS=")
    bindings = json.loads(request.prompt.split("IMAGE_BINDINGS=", 1)[1].splitlines()[0])
    expected_roles = ["composition_reference", "product_detail", "product_original"]
    if repair:
        if method == "style":
            expected_roles.append("current_artwork")
        else:
            expected_roles.insert(0, "current_artwork")
            assert (
                "Reference image 1 is the binding composition blueprint;"
                not in request.prompt
            )
    assert [binding["role"] for binding in bindings] == expected_roles
    assert [binding["image"] for binding in bindings] == list(
        range(1, len(expected_roles) + 1)
    )
    ledger = json.loads((tmp_path / "invocations.json").read_text())
    saved = ledger["fixture"]["source_bindings"]
    assert [row["role"] for row in saved] == expected_roles
    assert [row["sha256"] for row in saved] == [item.sha256 for item in request.sources]
    assert asyncio.run(render()) == result
    assert len(events) == 1  # Cached artwork never dispatches again.


def test_original_already_in_native_inputs_is_not_duplicated(tmp_path):
    render, events, original, _, _ = setup(tmp_path, "exact", False)
    asyncio.run(render([original]))
    assert sum(s.filename == original.name for s in events[0].sources) == 1


def test_original_context_over_budget_does_not_consume_generation_token(
    tmp_path, monkeypatch
):
    render, events, original, paths, root = setup(tmp_path, "exact", False)
    # Native crop inputs fit; adding the original must be checked before dispatch.
    measured = sum(len(p.read_bytes()) for p in paths)
    assert measured < measured + len(original.read_bytes())
    monkeypatch.setattr("app.replica_native.MAX_SOURCE_BYTES", measured)
    with pytest.raises(ReplicaInputTooLarge):
        asyncio.run(render())
    assert events == []
    assert not (root / "invocations.json").exists()


def test_style_native_handoff_last_draft_survives_original_evidence_supplement(
    tmp_path,
):
    from app.replica_native import digest

    calls = []
    original = png(tmp_path / "inputs/product-00.img", "blue")
    draft = png(tmp_path / "artwork/draft.png", "pink")
    reference = png(tmp_path / "reference.png", "red")

    async def edit(request):
        calls.append(request)
        return SimpleNamespace(
            outcome=ProviderOutcome.SUCCEEDED,
            artifact=SimpleNamespace(
                payload=draft.read_bytes(), locally_resampled=False
            ),
            normalized_usage={"reported": False},
            actual_cost_micros=None,
            estimated_cost_micros=None,
            http_status=200,
            error_code=None,
            raw_usage={},
        )

    run = run_at(tmp_path, "style", SimpleNamespace(edit=edit))
    run.state = lambda: {
        "audits": [
            {
                "target_id": "U01",
                "pass": False,
                "artwork_sha256": digest(draft.read_bytes()),
                "evidence": {"failure_reasons": ["Synthetic geometry mismatch"]},
            }
        ],
        "artwork": [{"target_id": "U01", "path": str(draft)}],
    }
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Native style prompt", encoding="utf-8")
    native = {"token": "fixture", "revision_index": 1}
    prompt, paths = NativeReplicaProvider._style_render_inputs(
        run,
        native,
        {"prompt_path": str(prompt_path), "referenced_image_paths": [str(reference)]},
        "U01",
    )
    provider = NativeReplicaProvider(None, run.gateway, None)

    async def save(*args, **kwargs):
        pass

    provider._save = save
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_fixture",
        job_item_id="item_fixture",
        attempt_id="attempt_fixture",
    )
    asyncio.run(provider._render(run, request, native, prompt, paths, [64, 80], "U01"))
    assert len(calls) == 1
    assert "最后一张参考图是当前商品初稿" in calls[0].prompt
    assert [source.filename for source in calls[0].sources] == [
        reference.name,
        original.name,
        draft.name,
    ]
    assert calls[0].sources[-1].payload == draft.read_bytes()


def test_style_repair_missing_draft_stops_before_invocation(tmp_path):
    from app.replica_native import ReplicaNativeError

    original = png(tmp_path / "inputs/product-00.img", "blue")
    assert original.is_file()
    run = run_at(tmp_path, "style")
    provider = NativeReplicaProvider(None, None, None)
    request = SimpleNamespace(
        workspace_id="ws_test",
        job_id="job_fixture",
        job_item_id="item_fixture",
        attempt_id="attempt_fixture",
    )
    with pytest.raises(ReplicaNativeError, match="初稿"):
        asyncio.run(
            provider._render(
                run,
                request,
                {"token": "unspent", "revision_index": 1},
                "fixture",
                [],
                [64, 80],
                "U01",
            )
        )
    assert not (tmp_path / "invocations.json").exists()


def test_style_repair_keeps_draft_last_even_when_already_in_handoff(tmp_path):
    render, events, original, paths, _ = setup(tmp_path, "style", True)
    draft = paths[-1]
    asyncio.run(render([draft]))
    sources = events[0].sources
    assert sources[-1].filename == draft.name
    assert sum(source.filename == draft.name for source in sources) == 1
    assert any(source.filename == original.name for source in sources)
