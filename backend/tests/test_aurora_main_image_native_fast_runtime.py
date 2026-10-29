"""Native-fast keeps the original six rich prompts and truthful technical delivery."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

from app.new_detail_skill_provider import BundledSkillManifestValidator

BACKEND = Path(__file__).resolve().parents[1]
SKILL_ID = "aurora-domestic-ecommerce-main-image-native-fast"
ROOT = BACKEND / "skill_sources" / SKILL_ID
ORIGINAL = BACKEND / "skill_sources/aurora-domestic-ecommerce-main-image-v20"
SOURCE_MANIFEST = BACKEND / "source_manifests" / f"{SKILL_ID}-source.json"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def invoke(root, command, *, blocked=False, **arguments):
    argv = [sys.executable, str(root / "scripts/harness.py"), command]
    for key, value in arguments.items():
        argv.extend(["--" + key.replace("_", "-"), str(value)])
    result = subprocess.run(argv, capture_output=True, encoding="utf-8", check=False)
    payload = json.loads(result.stdout)
    assert result.returncode == (3 if blocked else 0), result.stderr + result.stdout
    if blocked:
        assert payload["status"] == "BLOCKED"
    return payload


@pytest.fixture
def native(tmp_path):
    image = tmp_path / "original.png"
    Image.new("RGB", (1024, 1024), "red").save(image)
    raw = {
        "request_id": "native_fast_runtime_test",
        "product": {
            "name": "便携保温杯",
            "category": "保温杯",
            "original_references": [str(image)],
            "verified_selling_points": ["轻量便携", "杯盖防误开"],
            "verified_attributes": ["可见杯盖结构"],
            "verified_scenes": ["城市通勤"],
            "verified_offers": [],
            "verified_service_or_trust_facts": [],
        },
    }
    identity = {
        "request_id": raw["request_id"],
        "product_form": "带盖随行杯",
        "identity_summary": "同一款红色杯体与杯盖，保留原始标识。",
        "evidence": [
            {"evidence_id": "product", "path": str(image), "view_tags": ["原始主视图"]}
        ],
        "must_preserve": ["红色杯体", "杯盖结构", "原始标识"],
        "must_not_change": ["不得增减部件"],
        "previous_generated_images_used": False,
    }
    raw_path, identity_path = tmp_path / "input.json", tmp_path / "identity.json"
    write(raw_path, raw)
    write(identity_path, identity)

    def prepare(root, filename):
        session = tmp_path / filename
        invoke(root, "start", input=raw_path, session=session)
        invoke(root, "lock-identity", session=session, identity=identity_path)
        invoke(root, "compile", session=session)
        invoke(
            root, "authorize", session=session, approval_text="提交六图并授权技术交付"
        )
        return session

    return tmp_path, prepare(ROOT, "native-session.json"), prepare


def generate_all(native):
    folder, session, _ = native
    for index in range(6):
        result = invoke(ROOT, "generate-next", session=session)
        request = result["generation_request"]
        assert request["page_index"] == index
        image = folder / f"generated-{index}.png"
        Image.new("RGB", (1254, 1254), (index * 30, 0, 0)).save(image)
        invoke(
            ROOT,
            "record-generation",
            session=session,
            token=request["generation_token"],
            image=image,
        )
    return session


def test_archive_provenance_and_all_unchanged_compiler_sources():
    provenance = load(SOURCE_MANIFEST)
    assert provenance["archive_sha256"] == (
        "0c5d3daa3889d0ff5964114996a49c428dce9b200b3591d9b690991170869d3c"
    )
    assert len(provenance["files"]) == provenance["source_file_count"] == 24
    assert provenance["all_24_files_match_bundled_v20"] is True
    permitted = {
        "SKILL.md",
        "scripts/harness.py",
        "scripts/runtime/session.py",
        "scripts/runtime/core.py",
        "schemas/session.schema.json",
    }
    changed = set()
    for entry in provenance["files"]:
        original = (ORIGINAL / entry["path"]).read_bytes()
        assert hashlib.sha256(original).hexdigest() == entry["sha256"]
        current = (ROOT / entry["path"]).read_bytes()
        if current != original:
            changed.add(entry["path"])
    assert changed == permitted


def test_manifest_valid_and_refresh_needs_no_private_archive():
    manifest = BACKEND / "app/skill_manifests" / f"{SKILL_ID}.json"
    before = manifest.read_bytes()
    subprocess.run(
        [
            sys.executable,
            str(BACKEND / "scripts/generate_main_image_native_fast_manifest.py"),
        ],
        check=True,
        capture_output=True,
    )
    assert manifest.read_bytes() == before
    assert (
        BundledSkillManifestValidator().validate(SKILL_ID)
        == load(manifest)["content_hash"]
    )
    assert (
        load(manifest)["policy"]["policy_version"] == "aurora-main-image-native-fast-v1"
    )


def test_compiled_plan_and_six_complete_prompts_match_original_bytes(native):
    folder, native_session, prepare = native
    original_session = prepare(ORIGINAL, "original-session.json")
    original = load(original_session)
    fast = load(native_session)
    assert fast["plan"] == original["plan"]
    assert fast["campaign_constitution"] == original["campaign_constitution"]
    for index in range(6):
        page_session = deepcopy(fast)
        page_session["active_page_index"] = index
        page_session["accepted_pages"] = []
        path = folder / f"independent-{index}.json"
        write(path, page_session)
        result = invoke(ROOT, "generate-next", session=path)
        request = result["generation_request"]
        assert request["page_index"] == index
        assert (
            request["prompt"].encode()
            == original["plan"]["pages"][index]["prompt"].encode()
        )
        assert len(request["prompt"]) > 1000
        assert "【页面版式合同】" in request["prompt"]
        assert "【视觉表现合同】" in request["prompt"]
        assert "【卖点证据合同】" in request["prompt"]
        state = load(path)
        assert state["accepted_pages"] == []
        assert state["search_calibrated"] is False
        assert state["style_calibrated"] is False


def test_six_real_images_finalize_without_audit_or_false_review(native):
    session = generate_all(native)
    state = load(session)
    assert state["state"] == "READY_FOR_DELIVERY"
    assert state["accepted_pages"] == list(range(6))
    assert state["search_calibrated"] is state["style_calibrated"] is False
    assert state["modification_history"] == []
    assert all(
        version["status"] == "GENERATED_NOT_REVIEWED"
        for version in state["generated_versions"]
    )
    output = session.parent / "manifest.json"
    result = invoke(
        ROOT,
        "finalize",
        session=session,
        output=output,
        approval_text="提交六图并授权技术交付",
    )
    assert result["status"] == "FINALIZED"
    manifest = load(output)
    assert len(manifest["pages"]) == 6
    assert manifest["visual_review_status"] == "NOT_REVIEWED"
    assert manifest["set_audit_performed"] is False
    assert manifest["automatic_repairs_used"] == 0
    assert "set_audit_passed" not in manifest and "final_user_approval" not in manifest
    assert "PASS" not in json.dumps(manifest)
    assert all(
        page["visual_review_status"] == "NOT_REVIEWED" for page in manifest["pages"]
    )


def test_stale_token_and_duplicate_issuance_are_blocked_without_state_mutation(native):
    _, session, _ = native
    invoke(ROOT, "generate-next", session=session)
    before = session.read_bytes()
    invoke(ROOT, "generate-next", session=session, blocked=True)
    invoke(
        ROOT,
        "record-generation",
        session=session,
        token="stale-token",
        image=session.parent / "original.png",
        blocked=True,
    )
    assert session.read_bytes() == before


def test_a_selected_page_cannot_be_issued_again(native):
    _, session, _ = native
    request = invoke(ROOT, "generate-next", session=session)["generation_request"]
    invoke(
        ROOT,
        "record-generation",
        session=session,
        token=request["generation_token"],
        image=session.parent / "original.png",
    )
    state = load(session)
    state["active_page_index"] = 0
    write(session, state)
    invoke(ROOT, "generate-next", session=session, blocked=True)


@pytest.mark.parametrize("dimensions", [(768, 768), (1024, 1100)])
def test_invalid_original_canvas_is_blocked(native, dimensions):
    _, session, _ = native
    request = invoke(ROOT, "generate-next", session=session)["generation_request"]
    path = session.parent / "bad-canvas.png"
    Image.new("RGB", dimensions).save(path)
    invoke(
        ROOT,
        "record-generation",
        session=session,
        token=request["generation_token"],
        image=path,
        blocked=True,
    )
    assert load(session)["accepted_pages"] == []


@pytest.mark.parametrize(
    "fault", ["changed_sha", "missing_file", "missing_page", "fake_pass"]
)
def test_finalizer_requires_six_real_unmodified_unreviewed_files(native, fault):
    session = generate_all(native)
    state = load(session)
    if fault == "changed_sha":
        Path(state["generated_versions"][0]["file"]).write_bytes(b"altered")
    elif fault == "missing_file":
        Path(state["generated_versions"][0]["file"]).unlink()
    elif fault == "missing_page":
        state["generated_versions"].pop()
        write(session, state)
    else:
        state["generated_versions"][0]["status"] = "ACCEPTED"
        write(session, state)
    invoke(
        ROOT,
        "finalize",
        session=session,
        output=session.parent / "blocked.json",
        approval_text="提交六图并授权技术交付",
        blocked=True,
    )
    assert not (session.parent / "blocked.json").exists()


def test_finalizer_rejects_incomplete_or_stale_authorization(native):
    _, session, _ = native
    invoke(
        ROOT,
        "finalize",
        session=session,
        output=session.parent / "blocked.json",
        approval_text="提交授权",
        blocked=True,
    )
    state = load(session)
    state["authorization"]["plan_digest"] = "stale"
    write(session, state)
    invoke(ROOT, "generate-next", session=session, blocked=True)


@pytest.mark.parametrize(
    "command",
    [
        "audit-current",
        "prepare-repair",
        "calibrate-search",
        "calibrate-style",
        "audit-set",
        "apply-user-adjustment",
        "restart-campaign",
    ],
)
def test_historical_quality_and_modification_commands_are_explicitly_disabled(command):
    result = invoke(ROOT, command, blocked=True)
    assert "disabled" in result["error"].lower()
