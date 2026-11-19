import hashlib
import json
from types import SimpleNamespace

import pytest

from app.domestic_detail.plans import canonical_hash
from app.domestic_detail.review import DetailReviewError
from app.domestic_detail.source_comparison_evidence import (
    load_source_comparison_inventory,
    save_source_comparisons,
)
from app.domestic_detail.website_review import prompt


def fixture():
    data = b"exact synthetic comparison bytes"
    source = SimpleNamespace(
        asset_id="source-candidate-pair-0", source_role="source_candidate_comparison",
        payload=data, sha256=hashlib.sha256(data).hexdigest(),
    )
    return {"token": "signed-render", "pair_id": "P01"}, "a" * 64, source


def test_original_comparison_is_write_once_and_has_exact_recovery_inventory(tmp_path):
    native, candidate, source = fixture()
    save_source_comparisons(tmp_path, native, candidate, (source,))
    save_source_comparisons(tmp_path, native, candidate, (source,))
    expected = [{"asset_id": source.asset_id, "role": source.source_role,
                 "sha256": source.sha256}]
    assert load_source_comparison_inventory(tmp_path, native, candidate) == expected
    assert load_source_comparison_inventory(tmp_path, native, "b" * 64) == []
    assert load_source_comparison_inventory(
        tmp_path, {**native, "token": "other-render"}, candidate
    ) == []
    original = next((tmp_path / "reviews").glob("source-comparisons-*.json"))
    before = original.read_bytes()
    source.payload = b"replacement comparison"
    source.sha256 = hashlib.sha256(source.payload).hexdigest()
    with pytest.raises(DetailReviewError):
        save_source_comparisons(tmp_path, native, candidate, (source,))
    assert original.read_bytes() == before


@pytest.mark.parametrize(
    "fault", ["missing", "pixels", "scope", "path", "role", "duplicate", "extra"]
)
def test_missing_changed_or_foreign_comparisons_fail_closed(tmp_path, fault):
    native, candidate, source = fixture()
    save_source_comparisons(tmp_path, native, candidate, (source,))
    manifest = next((tmp_path / "reviews").glob("source-comparisons-*.json"))
    value = json.loads(manifest.read_bytes())
    image = tmp_path / "reviews" / value["sources"][0]["filename"]
    if fault == "missing":
        image.unlink()
    elif fault == "pixels":
        image.write_bytes(b"changed")
    else:
        if fault == "scope":
            value["binding"]["candidate_sha256"] = "b" * 64
        elif fault == "path":
            value["sources"][0]["filename"] = "../foreign.png"
        elif fault == "role":
            value["sources"][0]["role"] = "product_reference"
        elif fault == "duplicate":
            value["sources"] *= 2
        else:
            value["extra"] = "unexpected"
        value["manifest_sha256"] = canonical_hash({
            key: row for key, row in value.items() if key != "manifest_sha256"
        })
        manifest.write_text(json.dumps(value))
    with pytest.raises(DetailReviewError) as error:
        load_source_comparison_inventory(tmp_path, native, candidate)
    assert error.value.code == "NATIVE_DETAIL_REVIEW_PROVENANCE_INVALID"


@pytest.mark.parametrize(
    "transport,expected",
    [
        (None, "bf912e2d62fa627d37038b04c5ddb1b40f31dbe8916a0cfdc3083e650e2b6c95"),
        ("WEBSITE_OWNED_METADATA_V2",
         "a524bba42640452b69a20080e0c6d1c038b2e182aedebdc1652ca9632993745d"),
    ],
)
def test_deployed_review_prompts_remain_byte_identical(transport, expected):
    # Captured from deployed commit 8c4537e, not from the current implementation.
    context = {"pair_id": "P01", "module_ids": ["M01", "M02"]}
    if transport:
        context["review_transport_version"] = transport
    schema = {"type": "object", "required": ["status"]}
    assert hashlib.sha256(prompt(context, schema).encode()).hexdigest() == expected
    versioned = prompt(
        {**context, "review_prompt_version": "SOURCE_COMPARISON_V1"}, schema
    )
    assert versioned.startswith("SOURCE COMPARISON REVIEW V1\n")
    with pytest.raises(ValueError, match="Unknown"):
        prompt({**context, "review_prompt_version": "unknown"}, schema)
