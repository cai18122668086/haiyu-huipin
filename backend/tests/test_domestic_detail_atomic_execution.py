"""New synthetic jobs exercise atomic decisions through the real coordinator."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy

import pytest

from app.aurora_detail_runtime import detail_workspace
from app.xinghe_agent import XingheImageProvider
from tests.test_api import WORKSPACE_ID, dispatch_pending, idem
from tests.test_domestic_detail_atomic_review import visible_failure
from tests.test_domestic_detail_execution import _aurora_approval_payload, _gateway
from tests.test_domestic_detail_plans import planned_job


@pytest.mark.parametrize(
    "finding",
    ["geometry", "brand", "content", "unknown_geometry", "contradictory_pass"],
)
def test_atomic_review_controls_exactly_one_pair_repair_without_rewriting_observations(
    client, monkeypatch, finding
):
    planned = planned_job(client)
    observed = []

    async def review(self, request, native, sources, artifact):
        slices = [
            source
            for source in sources
            if source.source_role.startswith("generated_module_candidate:")
        ]
        assert [source.sha256 for source in slices] == [
            row["sha256"] for row in native["pixel_lineage"]["slices"]
        ]
        assert all(
            hashlib.sha256(source.payload).hexdigest() == source.sha256
            for source in slices
        )
        judgment = self._native_detail_judgment(native, artifact)
        if native["pair_id"] == "P01":
            judgment.update(status="MATERIAL_FAIL", material_failures=[])
            report = judgment["module_judgments"][1]
            visible_failure(
                report,
                finding if finding in {"geometry", "brand", "content"} else "geometry",
            )
            if finding == "unknown_geometry":
                report["observations"]["structural_zone_count"] = None
            elif finding == "contradictory_pass":
                report.update(status="PASS", material_failures=[])
            observed.append(deepcopy(judgment["module_judgments"]))
        return self._normalize_native_pair_judgment(judgment, native, artifact)

    monkeypatch.setattr(
        XingheImageProvider, "_native_detail_judgment_with_review", review
    )
    client.app.state.settings.max_image_pixels = 6_000_000
    client.app.state.storage.validator.max_image_pixels = 6_000_000
    confirmed = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}/plan/confirm",
        headers=idem(),
        json=_aurora_approval_payload(planned),
    )
    assert confirmed.status_code == 202, confirmed.text
    dispatch_pending(client)
    current = client.get(
        f"/api/v1/workspaces/{WORKSPACE_ID}/jobs/{planned['id']}"
    ).json()
    gateway = _gateway(client)
    if finding in {"unknown_geometry", "contradictory_pass"}:
        assert current["status"] == "failed", current
        assert len(gateway.image_calls) == 1
        asyncio.run(client.app.state.task_runner.run(planned["id"]))
        assert len(gateway.image_calls) == 1
        return
    assert current["status"] == "awaiting_final_review", current
    assert len(gateway.image_calls) == 5 and len(observed) == 2
    state = json.loads(
        (detail_workspace(planned["id"]) / "session.json").read_text(encoding="utf-8")
    )
    first = state["modules"][:2]
    assert all(module["status"] == "COMPLETE_WITH_RESIDUALS" for module in first)
    assert all(module["cycle_attempt_count"] == 2 for module in first)
    for module in first:
        assert len(module["attempts"]) == 2
        assert module["repair_plan"]["passed_layers"] == []
        for index, attempt in enumerate(module["attempts"]):
            assert attempt["judgment"]["module_judgments"] == observed[index]
        assert module["residual_issues"]
