"""Native final manifest creation/inspection; not visual-quality acceptance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from app.aurora_detail_runtime import (
    AuroraDetailHarnessError,
    AuroraDetailHarnessRunner,
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def accepted_delivery(tmp_path):
    root = tmp_path / "native-delivery"
    root.mkdir()
    modules, pairs = [], []
    for index in range(4):
        pair_id = f"P{index + 1:02d}"
        canvas = root / f"{pair_id}.png"
        image = Image.new("RGB", (3, 8), (30 + index, 60, 90))
        image.save(canvas)
        ids = []
        for position in range(2):
            module_id = f"M{index * 2 + position + 1:02d}"
            ids.append(module_id)
            path = root / f"{module_id}.png"
            image.crop((0, position * 4, 3, (position + 1) * 4)).save(path)
            modules.append(
                {
                    "id": module_id,
                    "order": len(modules) + 1,
                    "status": "COMPLETE",
                    "accepted_asset": str(path),
                    "residual_issues": [],
                    "attempts": [
                        {
                            "image": str(path),
                            "pair_canvas": str(canvas),
                            "pair_id": pair_id,
                            "artifact_lineage_sha256": "a" * 64,
                            "review_evidence": {"synthetic_flag": True},
                            "judgment": {
                                "artifact_binding": {
                                    "image_sha256": sha(canvas),
                                    "auditor": "synthetic-unit",
                                }
                            },
                        }
                    ],
                }
            )
        pairs.append({"pair_id": pair_id, "module_ids": ids})
    state = {
        "version": "4.3.1",
        "state": "AWAITING_USER_FINAL_REVIEW",
        "input": {
            "product_name": "Synthetic projection",
            "platform_profile": {"verification_status": "INTERNAL_ONLY"},
        },
        "plan": {
            "modules": [{"id": m["id"]} for m in modules],
            "output_strategy": {
                "module_height_px": 4,
                "continuous_pair_rendering": {"enabled": True, "pair_ids": pairs},
            },
        },
        "modules": modules,
        "events": [],
        "approval": {"delivery_mode": "USER_FINAL_REVIEW", "text": "plan approval"},
    }
    session = root / "session.json"
    session.write_text(json.dumps(state), encoding="utf-8")
    return root, session, state


def finish(fixture, mode="complete"):
    root, session, state = fixture
    runner = AuroraDetailHarnessRunner()
    output = root / "final-manifest.json"
    if mode == "residual":
        state["modules"][0].update(
            status="COMPLETE_WITH_RESIDUALS",
            residual_issues=["Synthetic measured material failure remains visible"],
        )
    if mode == "automatic":
        state["state"] = "AUTO_FINALIZE_AUTHORIZED"
        state["approval"]["delivery_mode"] = "AUTO_FINALIZE_AUTHORIZED"
    session.write_text(json.dumps(state), encoding="utf-8")
    command = "auto-finalize" if mode == "automatic" else "finalize"
    arguments = {"session": session, "output": output}
    if command == "finalize":
        arguments["approval-text"] = "I reviewed this synthetic delivery"
    result = runner.run(command, root, arguments)
    return runner, output, result


@pytest.mark.parametrize("mode", ["complete", "residual", "automatic"])
def test_native_final_manifest_preserves_hashes_residuals_and_explicit_mode(
    accepted_delivery, mode
):
    root, session, _ = accepted_delivery
    runner, output, result = finish(accepted_delivery, mode)
    original_session, original_manifest = session.read_bytes(), output.read_bytes()
    inspected = runner.run(
        "inspect-final-manifest", root, {"session": session, "manifest": output}
    )
    assert inspected["manifest"] == result
    assert inspected["manifest_file_sha256"] == sha(output)
    assert len(result["modules"]) == 8 and len(result["render_containers"]) == 4
    assert all(row["pair_canvas_sha256"] for row in result["render_containers"])
    assert bool(result["residual_issues"]) == (mode == "residual")
    assert result["long_page"] is None and result["long_page_audit"] is None
    assert result["approval"]["mode"] == (
        "AUTO_FINALIZE_AUTHORIZED" if mode == "automatic" else "USER_FINAL_REVIEW"
    )
    assert (
        session.read_bytes() == original_session
        and output.read_bytes() == original_manifest
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_manifest",
        "changed_bytes",
        "changed_residual_with_new_hash",
        "bool_to_int_with_new_hash",
        "unfinished_module",
        "missing_attempt",
        "changed_slice",
        "changed_canvas",
        "different_path",
    ],
)
def test_native_readonly_final_inspection_rejects_changed_originals(
    accepted_delivery, tamper
):
    root, session, _ = accepted_delivery
    runner, output, _ = finish(accepted_delivery)
    state = json.loads(session.read_bytes())
    target = output
    if tamper == "missing_manifest":
        output.unlink()
    elif tamper == "changed_bytes":
        output.write_bytes(output.read_bytes() + b" ")
    elif tamper in {"changed_residual_with_new_hash", "bool_to_int_with_new_hash"}:
        manifest = json.loads(output.read_bytes())
        if tamper == "changed_residual_with_new_hash":
            manifest["residual_issues"] = [{"module_id": "M01", "issues": ["changed"]}]
        else:
            manifest["modules"][0]["review_evidence"]["synthetic_flag"] = 1
        output.write_text(json.dumps(manifest), encoding="utf-8")
        state["final"]["manifest_sha256"] = sha(output)
    elif tamper == "unfinished_module":
        state["modules"][0]["status"] = "PENDING"
    elif tamper == "missing_attempt":
        state["modules"][0]["attempts"] = []
    elif tamper == "changed_slice":
        path = Path(state["modules"][0]["accepted_asset"])
        path.write_bytes(path.read_bytes() + b"changed slice")
    elif tamper == "changed_canvas":
        path = Path(state["modules"][0]["attempts"][0]["pair_canvas"])
        path.write_bytes(path.read_bytes() + b"changed canvas")
    else:
        target = root / "other-manifest.json"
        target.write_bytes(output.read_bytes())
    session.write_text(json.dumps(state), encoding="utf-8")
    before = session.read_bytes()
    with pytest.raises(AuroraDetailHarnessError):
        runner.run(
            "inspect-final-manifest", root, {"session": session, "manifest": target}
        )
    assert session.read_bytes() == before


def test_native_automatic_finalize_needs_actual_prior_authorization(accepted_delivery):
    root, session, state = accepted_delivery
    state["state"] = "AUTO_FINALIZE_AUTHORIZED"
    session.write_text(json.dumps(state), encoding="utf-8")
    before = session.read_bytes()
    output = root / "not-authorized.json"
    with pytest.raises(AuroraDetailHarnessError):
        AuroraDetailHarnessRunner().run(
            "auto-finalize", root, {"session": session, "output": output}
        )
    assert not output.exists() and session.read_bytes() == before
