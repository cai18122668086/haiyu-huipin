from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app import source_pixels
from app.domestic_detail.review import DetailReviewError
from app.domestic_detail.source_comparison_evidence import (
    load_source_comparison_inventory,
    save_source_comparisons,
)
from app.source_print_evidence import identical_print_regions


def test_native_square_recovery_binds_size_instructions_target_and_original(tmp_path):
    import json

    from app.replica_style_delivery import _approved_native_square

    context = {
        "references": [{"aspect_ratio": "follow-reference"}],
        "user_facts": "保留商品原图",
    }
    run = SimpleNamespace(context=context, root=tmp_path)
    entry = {"target": "U01", "state": "returned", "haiyu_native_canvas": "2000x2000",
             "actual_size": [2000, 2000], "sha256": "a" * 64}
    ledger = tmp_path / "invocations.json"

    def valid():
        return _approved_native_square(run, [1200, 1200], [2000, 2000], "a" * 64, "U01")

    assert not valid()
    ledger.write_text(json.dumps({"invocation": entry}))
    assert valid()
    for key, other in (("target", "U02"), ("state", "invoked"), ("sha256", "b" * 64)):
        ledger.write_text(json.dumps({"invocation": {**entry, key: other}}))
        assert not valid()
    ledger.write_text(json.dumps({"invocation": entry}))
    context["user_facts"] = "必须输出 1200×1200 像素"
    assert not valid()


def test_external_copy_view_cannot_hide_changed_product_pixels():
    plate = source_pixels.artifact(Image.new("RGB", (100, 200), "white"), "plate.png")
    layer = source_pixels.artifact(Image.new("RGBA", (20, 30), "red"), "product.png")
    cells = [[0.1, 0.15, 0.5, 0.45]]
    result, proof = source_pixels.compose(plate, (layer,), cells)
    assert source_pixels.marketing_text_view(result, (layer,), cells).width == 200
    from io import BytesIO

    with Image.open(BytesIO(result.payload)) as im:
        x, y = proof["placements"][0]["box"][:2]
        im.putpixel((x + 2, y + 2), (0, 0, 0, 255))
        changed = source_pixels.artifact(im, "changed.png")
    with pytest.raises(ValueError, match="overlap"):
        source_pixels.marketing_text_view(changed, (layer,), cells)


def test_print_equality_checks_native_rgb_including_single_pixel_changes():
    raw = Image.new("RGB", (120, 120), "white")
    ImageDraw.Draw(raw).text((25, 25), "ABC", fill="black")
    first = source_pixels.artifact(raw, "source.png")
    source = SimpleNamespace(asset_id="a", payload=first.payload, sha256=first.sha256)
    prep = {
        "selected_regions": [
            {
                "asset_id": "a",
                "box": [0, 0, 1, 1],
                "print_regions": [[0.1, 0.1, 0.7, 0.1, 0.7, 0.7, 0.1, 0.7]],
            }
        ]
    }
    raw.putpixel((100, 100), (5, 5, 5))
    assert identical_print_regions(
        source, source_pixels.artifact(raw, "edited.png"), prep
    )
    raw.putpixel((30, 30), (5, 5, 5))
    assert not identical_print_regions(
        source, source_pixels.artifact(raw, "changed.png"), prep
    )
    assert not identical_print_regions(source, first, {"selected_regions": []})


def test_external_copy_inventory_is_bound_to_original_combination(tmp_path):
    native, sha = {"pair_id": "P01", "token": "issued"}, "a" * 64
    data = source_pixels.artifact(Image.new("RGB", (6, 5), "white"), "input.png")

    def source(uid, role):
        return SimpleNamespace(
            asset_id=uid, source_role=role, sha256=data.sha256, payload=data.payload
        )

    pair = source("source-candidate-pair-0", "source_candidate_comparison")
    copy = source("external-copy-inspection", "external_copy_inspection")
    save_source_comparisons(tmp_path, native, sha, (pair, copy))
    inventory = load_source_comparison_inventory(tmp_path, native, sha)
    assert [r["role"] for r in inventory] == [pair.source_role, copy.source_role]
    with pytest.raises(DetailReviewError):
        save_source_comparisons(tmp_path, native, sha, (copy, pair))


def test_six_page_role_alias_requires_the_actual_six_attachments():
    from copy import deepcopy

    from app.aurora_main_image_provider import _normalize_parallel_set_audit_payload

    sources = tuple(
        SimpleNamespace(
            asset_id=f"accepted-page-{i}", filename=f"page-{i}.png", sha256=f"{i}" * 64
        )
        for i in range(6)
    )
    rows = [
        {
            "asset_id": s.asset_id,
            "filename": s.filename,
            "page_order_index": i,
            "image_sha256": s.sha256,
            "is_search_listing_cover": i == 0,
            "pixel_evidence": [f"Visible subject and composition differ on image {i}."],
            "not_cover_reason": "Full-page hardware detail and no cover headline."
            if i
            else None,
        }
        for i, s in enumerate(sources)
    ]
    raw = {
        "page_roles": rows,
        "search_listing_cover": {**rows[0], "is_alone": True},
        "all_material_deficiencies": [],
        "checks": [
            {
                "name": "role_uniqueness",
                "status": "pass",
                "evidence": "Only the first image has the listing cover layout.",
            }
        ],
    }
    good = _normalize_parallel_set_audit_payload(deepcopy(raw), sources)
    assert good["search_role_is_unique"] is True
    assert len(good["carousel_distinctions"]) == 5
    for key, value in (
        ("asset_id", "another-page"),
        ("image_sha256", "a" * 64),
        ("is_search_listing_cover", True),
    ):
        bad = deepcopy(raw)
        bad["page_roles"][2][key] = value
        assert not _normalize_parallel_set_audit_payload(bad, sources).get(
            "search_role_is_unique"
        )


@pytest.mark.parametrize(
    "outcome", ["correct", "unreadable", "reduced_preview", "technical", "still_wrong"]
)
def test_detail_window_checks_actual_pixels_before_acceptance(outcome):
    import asyncio
    from types import SimpleNamespace

    from app import source_window_review
    from app.image_fidelity import SourcePhotoUnusable
    from app.provider_diagnostics import DETAIL_UNCLEAR

    size = (1200, 150) if outcome == "reduced_preview" else (100, 150)
    pixels = Image.new("RGBA", size, "white")
    ImageDraw.Draw(pixels).ellipse((15, 80, 25, 92), fill="red")
    layer = source_pixels.artifact(pixels, "input.png")
    placements = [
        {"panel": 0, "layer_index": 0},
        {
            "panel": 1,
            "layer_index": 0,
            "source_window": [0, 0.3, 1, 0.8],
            "panel_box": [0.4, 0.2, 0.8, 0.6],
        },
    ]
    corrected = [0.1, 0.5, 0.3, 0.65]
    evidence = []

    class Gateway:
        async def _plan(self, request, profile, sources, context, suffix):
            assert profile == source_window_review.PROFILE
            if outcome == "reduced_preview":
                assert len(sources) == 2 and sources[1].sha256 == layer.sha256
                assert sources[1].payload == layer.payload
            else:
                assert len(sources) == 1
            evidence.append(sources[0].sha256)
            if outcome == "technical":
                return SimpleNamespace(error_code="PLANNER_TIMEOUT", payload=None)
            verdict = (
                "unreadable_source"
                if outcome in {"unreadable", "reduced_preview"}
                else "wrong_window"
                if len(evidence) == 1 or outcome == "still_wrong"
                else "matches"
            )
            return SimpleNamespace(
                error_code=None,
                payload={
                    "crops": [
                        {
                            "placement": 1,
                            "verdict": verdict,
                            "evidence": "Actual source and detail compared on both "
                            "views.",
                            "window": corrected
                            if verdict == "wrong_window"
                            else placements[1]["source_window"],
                        }
                    ]
                },
            )

    req = SimpleNamespace(
        prompt="Show the red mark in the second panel", group_key="fixture"
    )
    call = source_window_review.check(Gateway(), req, (layer,), placements, [], 0)
    if outcome == "correct":
        receipts = asyncio.run(call)
        assert len(receipts) == len(set(evidence)) == 2
        assert placements[1]["source_window"] == corrected
        assert "source_window" not in placements[0]
    elif outcome in {"unreadable", "reduced_preview"}:
        with pytest.raises(SourcePhotoUnusable) as exc:
            asyncio.run(call)
        assert exc.value.code == DETAIL_UNCLEAR and len(evidence) == 1
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(call)
        assert len(evidence) == (2 if outcome == "still_wrong" else 1)
