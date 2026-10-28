# ruff: noqa: E501
"""Durable pre-generation style analysis; native checks and judgments stay intact."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from .replica_exact_contracts import canonical_hash
from .replica_native import (
    NativeReplicaRun,
    ReplicaNativeError,
    ReplicaPlanAuditRejected,
    digest,
    read_json,
    write_json,
)
from .replica_style_evidence import (
    FLAGS,
    PROVENANCE,
    StyleEvidenceInvalid,
    apply_specs,
    crop_errors,
    review_crops,
)

JOURNAL = "style-analysis-checkpoints.json"
CURSOR = JOURNAL
VERSION = "style-analysis-resume-v3"
LEGACY_REVIEW_POLICY = {
    "version": "style-analysis-resume-v2",
    "sha256": "e8cab24e436f9eecf04021a70433dc748d17b84ecfd3110c9acaa344c6192798",
}
__all__ = [
    "FLAGS",
    "PROVENANCE",
    "StyleEvidenceInvalid",
    "crop_errors",
    "DurableStyleAnalysisRun",
    "StyleReplicaRun",
    "policy",
    "revision_checkpoint",
    "JOURNAL",
    "CURSOR",
    "VERSION",
]


def policy() -> dict[str, str]:
    sha = hashlib.sha256()
    for path in (
        Path(__file__),
        Path(__file__).with_name("replica_style_evidence.py"),
        Path(__file__).with_name("replica_native.py"),
    ):
        sha.update(path.name.encode())
        sha.update(path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").encode())
    return {"version": VERSION, "sha256": sha.hexdigest()}


def _crop_policy_authority(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    from .new_detail_skill_provider import BundledSkillManifestValidator
    from .replica_analysis_recovery import document_file
    from .replica_native import SKILLS

    if (
        snapshot.get("schema") != "replica-native-v1"
        or snapshot.get("method") != "style"
        or snapshot.get("analysis_policy") != LEGACY_REVIEW_POLICY
        or snapshot.get("skill_id") != SKILLS["style"]
        or snapshot.get("source_hash")
        != BundledSkillManifestValidator().validate(SKILLS["style"])
    ):
        return None
    try:
        name = (
            Path(snapshot["run"]).relative_to(Path(snapshot["root"]))
            / "artifacts/workflow.json"
        ).as_posix()
    except (KeyError, ValueError, TypeError):
        return None
    workflow = document_file(snapshot, name)
    cursor = document_file(snapshot, JOURNAL).get("cursor", {})
    evidence = cursor.get("evidence", {})
    baseline = evidence.get("source_analysis")
    if (
        cursor.get("phase") != "review"
        or not isinstance(baseline, dict)
        or not baseline.get("overview_detection", {}).get("panels")
        or not baseline.get("source_content_analysis", {}).get("sequence")
        or not isinstance(evidence.get("candidate"), dict)
        or not evidence["candidate"].get("units")
        or workflow.get("state") not in {"ROUTE_CLASSIFIED", "REFERENCE_DECOMPOSED"}
        or workflow.get("active_token")
        or workflow.get("artwork")
        or (workflow.get("plan_audit") or {}).get("pass") is True
    ):
        return None
    return {
        "source_policy": LEGACY_REVIEW_POLICY,
        "parent_snapshot_sha256": canonical_hash(snapshot),
        "frozen_source_sha256": canonical_hash(baseline),
        "target_policy": policy(),
    }


def crop_policy_upgrade(
    payload: dict[str, Any], input_hash: str
) -> dict[str, Any] | None:
    """Admit only the known failed pre-generation review through normal retry."""
    if (
        payload.get("replication_method") != "style"
        or payload.get("error_code") != StyleEvidenceInvalid.code
        or payload.get("_analysis_input_hash") != input_hash
        or payload.get("job_ids")
        or payload.get("plans")
        or payload.get("native_snapshot")
    ):
        return None
    return _crop_policy_authority(payload.get("_analysis_checkpoint", {}))


def revision_checkpoint(payload: dict[str, Any]) -> bool:
    from .replica_analysis_recovery import document_file

    snapshot = payload.get("_analysis_checkpoint", {})
    cursor = document_file(snapshot, JOURNAL).get("cursor", {})
    return bool(
        snapshot.get("method") == "style"
        and snapshot.get("analysis_policy") == policy()
        and cursor.get("phase") == "rejected"
        and cursor.get("prior_audit", {}).get("pass") is False
    )


class DurableStyleAnalysisRun(NativeReplicaRun):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.journal: dict[str, Any] = {"requests": {}, "cursor": {}}
        self.on_checkpoint: Any = None
        self.authorized_revision: str | None = None
        self.crop_policy_adoption: dict[str, Any] | None = None

    async def checkpoint(self) -> None:
        write_json(self.root / JOURNAL, self.journal)
        write_json(self.root / "context.json", self.context)
        if self.on_checkpoint is not None:
            await self.on_checkpoint()

    async def snapshot(self) -> dict[str, Any]:
        return {**await super().snapshot(), "analysis_policy": policy()}

    async def restore(self, snapshot: dict[str, Any]) -> None:
        upgrading = snapshot.get("analysis_policy") != policy()
        authority = _crop_policy_authority(snapshot) if upgrading else None
        if upgrading and (not authority or authority != self.crop_policy_adoption):
            raise ReplicaNativeError("风格分析恢复规则已变化，请保留原记录并重新诊断。")
        prefix = f"{self.workspace_id}/replica/{self.document_id}/"
        if any(
            "object_key" in r and not r["object_key"].startswith(prefix)
            for r in snapshot["files"].values()
        ):
            raise ReplicaNativeError("Style analysis snapshot belongs to another task")
        await super().restore(snapshot)
        self.journal = read_json(self.root / JOURNAL)
        if upgrading:
            # The HTTP retry retains the complete original snapshot in lineage.
            # Keep old response bytes and attribution; none is a v3 pixel review.
            self.journal.setdefault("policy_adoptions", []).append(authority)
            for record in self.journal["requests"].values():
                if record["stage"].startswith("style-crop-") and any(
                    key not in record["response"]
                    for key in (
                        "overview_detection",
                        "source_content_analysis",
                        "narrative_logic",
                        "source_semantics",
                    )
                ):
                    record["invalid_contract"] = True
                    record["validation_failure"] = {
                        "command": "legacy-crop-review",
                        "detail": "Legacy response omitted frozen source fields",
                    }
            write_json(self.root / JOURNAL, self.journal)

    def planner_identity(self) -> dict[str, Any]:
        gateway = self.gateway
        return {
            "provider": gateway.provider_name,
            "model": gateway.planner_model,
            "pricing_version": getattr(gateway, "pricing_version", None),
            "routes": [
                {
                    "name": r.name,
                    "model": r.planner_model,
                    "endpoint": canonical_hash(r.planner_base_url),
                }
                for r in gateway._ordered_routes()
            ]
            if hasattr(gateway, "_ordered_routes")
            else [],
        }

    def cli(self, command: str, **arguments: Any) -> dict[str, Any]:
        try:
            return super().cli(command, **arguments)
        except ReplicaNativeError as error:
            # Invalid format/structure is not a reusable accepted contract.
            # Preserve its bytes and metadata, then request an explicit correction.
            for key in ("facts", "plan", "audit", "analysis"):
                path = arguments.get(key)
                if not isinstance(path, Path) or not path.is_file():
                    continue
                value = read_json(path)
                if command == "audit-plan" and value.get("pass") is False:
                    continue  # A real FAIL remains a reusable historical verdict.
                sha = canonical_hash(value)
                for record in self.journal["requests"].values():
                    if record["sha256"] == sha:
                        record["invalid_contract"] = True
                        record["validation_failure"] = {
                            "command": command,
                            "detail": str(error)[-5000:],
                        }
            raise

    def crop(self, source: Path, bbox: list[int], target: Path) -> Path:
        path = super().crop(source, bbox, target)
        if path.name.startswith("style-"):
            file = self.root / PROVENANCE
            rows = read_json(file) if file.exists() else {}
            rows[str(path)] = {
                "source_path": str(source.resolve()),
                "source_sha256": digest(source.read_bytes()),
                "bbox": bbox,
                "crop_sha256": digest(path.read_bytes()),
            }
            write_json(file, rows)
        return path

    def _apply_specs(self, analysis: dict[str, Any]) -> bool:
        return apply_specs(self, analysis)

    async def _review_crops(self, analysis: dict[str, Any]) -> dict[str, Any]:
        return await review_crops(self, analysis)

    async def ask(
        self, stage: str, instruction: str, context: Any, **kwargs: Any
    ) -> dict[str, Any]:
        if stage == "style-crop-review":
            return await self._review_crops(context["analysis"])
        return await self._ask_model(stage, instruction, context, **kwargs)

    async def _ask_model(
        self, stage: str, instruction: str, context: Any, **kwargs: Any
    ) -> dict[str, Any]:
        sources = self.sources(kwargs.get("paths"))
        identity = canonical_hash(
            {
                "stage": stage,
                "instruction": instruction,
                "context": context,
                "schemas": kwargs.get("schemas", ()),
                "images": [s.sha256 for s in sources],
                "source_hash": self.source_hash,
                "policy": policy(),
                "planner": self.planner_identity(),
            }
        )
        cached = self.journal["requests"].get(identity)
        if cached is not None and cached.get("invalid_contract"):
            revision = getattr(self, "evidence_revision", "initial")
            if context.get("_correction_revision") == revision:
                raise ReplicaNativeError(
                    "本轮合同修正仍未通过原生校验，已保留结果；请显式继续下一轮修正。"
                )
            context = {
                **context,
                "_contract_validation": cached["validation_failure"],
                "_prior_response_sha256": cached["sha256"],
                "_correction_revision": getattr(self, "evidence_revision", "initial"),
            }
            return await self._ask_model(stage, instruction, context, **kwargs)
        if cached is not None:
            if canonical_hash(cached["response"]) != cached["sha256"]:
                raise ReplicaNativeError("原风格分析结果哈希不一致。")
            value = deepcopy(cached["response"])
            self._validate_model_paths(value)
            return value
        try:
            value = await super().ask(stage, instruction, context, **kwargs)
            self.journal["requests"][identity] = {
                "stage": stage,
                "response": deepcopy(value),
                "sha256": canonical_hash(value),
                "planner": self.planner_identity(),
            }
            return value
        finally:
            await self.checkpoint()

    async def prepare(
        self, payload: dict[str, Any], records: dict[str, Any]
    ) -> list[dict[str, Any]]:
        identity = canonical_hash(
            {
                k: payload.get(k)
                for k in (
                    "replication_method",
                    "product_asset_ids",
                    "references",
                    "facts",
                    "language",
                    "platform_code",
                )
            }
        )
        if self.journal.get("input_identity", identity) != identity:
            raise ReplicaNativeError("风格分析检查点的原素材或事实已变化。")
        self.journal["input_identity"] = identity
        cursor = self.journal["cursor"]
        if not cursor:
            return await super().prepare(payload, records)
        for role, ids in (
            ("product", payload["product_asset_ids"]),
            ("reference", [r["asset_id"] for r in payload["references"]]),
        ):
            for i, asset_id in enumerate(ids):
                if (
                    self.snapshot_files.get(f"inputs/{role}-{i:02}.img", {}).get(
                        "sha256"
                    )
                    != records[asset_id][2]
                ):
                    raise ReplicaNativeError("风格分析原图哈希不一致。")
        if (
            self.authorized_revision
            and cursor.get("applied_revision") != self.authorized_revision
        ):
            if cursor.get("phase") != "rejected":
                raise ReplicaNativeError("当前风格方案无需重新修正。")
            cursor.update(
                phase="planning",
                planning_phase="plan",
                stop_after=cursor["revision"] + 3,
                applied_revision=self.authorized_revision,
            )
            await self.checkpoint()
        return await self._prepare_style(cursor["observation"], cursor["units"])

    async def _prepare_style(
        self, observation: dict[str, Any], units: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        cursor = self.journal["cursor"]
        if not cursor:
            route = observation["route"]
            count = len(units)
            if route == "DETAIL_PAGE":
                count = max(8, min(12, count))
            elif route == "MARKETING_VISUAL_SET":
                count = max(3, count)
            elif count > 1:
                route = "CAROUSEL_SET"
            self.cli(
                "classify-route",
                workspace=self.run,
                route=route,
                **{
                    "screen-count": count,
                    "count-evidence": "User selected these ordered reference images; physical inspection determines the complete route count.",
                },
            )
            template = self.root / "contracts/reference-analysis.json"
            self.cli("init-reference-analysis", workspace=self.run, analysis=template)
            analysis = read_json(template)
            refs = [
                x for x in self.state()["inputs"] if x["role"] == "benchmark_reference"
            ]
            analysis["overview_detection"] = {
                "is_overview": any(
                    n > 1 for n in Counter(u["reference_index"] for u in units).values()
                ),
                "panel_count": len(units),
                "reading_order": "用户选图顺序，各栏逐屏从上到下",
                "detection_notes": "; ".join(
                    u["screen_boundary_reason"] for u in units
                ),
                "panels": [
                    {
                        "panel_id": f"B{i + 1:02}",
                        "panel_order": i + 1,
                        "reference_input_id": refs[u["reference_index"]]["id"],
                        "bbox": u["bbox"],
                    }
                    for i, u in enumerate(units)
                ],
            }
            write_json(template, analysis)
            self.cli("crop-reference-panels", workspace=self.run, analysis=template)
            cursor.update(phase="reference", observation=observation, units=units)
            await self.checkpoint()
        template = self.root / "contracts/reference-analysis.json"
        if cursor["phase"] == "reference":
            cursor["analysis"] = await self.ask(
                "style-reference-analysis",
                REFERENCE_INSTRUCTION,
                {
                    "template": read_json(template),
                    "workflow": self.state(),
                    "user_context": self.context,
                },
            )
            cursor["phase"] = "crop_sources"
            await self.checkpoint()
        if cursor["phase"] == "crop_sources":
            analysis = deepcopy(cursor["analysis"])
            refs = [
                x for x in self.state()["inputs"] if x["role"] == "benchmark_reference"
            ]
            for spec in analysis.pop("crop_specs", []):
                ref = next(x for x in refs if x["id"] == spec["reference_input_id"])
                path = self.crop(
                    Path(ref["path"]),
                    spec["bbox"],
                    self.root / "crops" / f"style-{spec['id']}.png",
                )
                for row in analysis.get("units", []):
                    if row["id"] == spec["id"]:
                        row["source_crop_path"] = str(path)
            cursor.update(phase="review", analysis=analysis)
            await self.checkpoint()
        if cursor["phase"] == "review":
            cursor["analysis"] = await self.ask(
                "style-crop-review",
                CROP_INSTRUCTION,
                {"analysis": cursor["analysis"], "workflow": self.state()},
            )
            cursor["phase"] = "compile"
            await self.checkpoint()
        if cursor["phase"] == "compile":
            analysis = deepcopy(cursor["analysis"])
            for step in analysis.get("source_content_analysis", {}).get("sequence", []):
                if step.get("text_status") == "transcribed":
                    step.setdefault("visual_density", {})["text_chars"] = sum(
                        len("".join(t.split())) for t in step.get("verbatim_text", [])
                    )
            write_json(template, analysis)
            try:
                if self.state()["state"] in {
                    "ROUTE_CLASSIFIED",
                    "REFERENCE_DECOMPOSED",
                }:
                    self.cli(
                        "compile-reference-crops", workspace=self.run, analysis=template
                    )
                if self.state()["state"] == "REFERENCE_DECOMPOSED":
                    self.cli(
                        "compile-visual-passport", workspace=self.run, analysis=template
                    )
            except ReplicaNativeError as error:
                cursor["phase"] = "review"
                cursor.setdefault("evidence", {})["validation_errors"] = [
                    str(error)[-5000:]
                ]
                await self.checkpoint()
                raise StyleEvidenceInvalid(
                    "Native crop evidence contract needs correction"
                ) from error
            cursor["phase"] = "facts"
            await self.checkpoint()
        if cursor["phase"] == "facts":
            await self.contract(
                "style-facts",
                FACTS_INSTRUCTION,
                {
                    "workflow": self.state(),
                    "user_context": self.context,
                    "geometry": observation["product_geometry"],
                },
                "register-product-facts",
                "facts",
            )
            cursor.update(
                phase="planning",
                revision=0,
                stop_after=3,
                planning_phase="plan",
                prior_audit=None,
            )
            await self.checkpoint()
        if cursor["phase"] == "rejected":
            raise ReplicaPlanAuditRejected(cursor.get("prior_audit") or {})
        return await self.finish_style_planning(cursor.get("prior_audit"))

    async def finish_style_planning(
        self, previous_audit: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        cursor = self.journal["cursor"]
        while cursor["phase"] != "ready" and cursor["revision"] < cursor["stop_after"]:
            revision = cursor["revision"]
            if cursor["planning_phase"] == "plan":
                await self.contract(
                    f"style-plan-r{revision}",
                    PLAN_INSTRUCTION,
                    {
                        "workflow": self.state(),
                        "user_context": self.context,
                        "prior_visual_audit": cursor["prior_audit"],
                    },
                    "plan-target-page",
                    "plan",
                )
                cursor["planning_phase"] = "audit"
                await self.checkpoint()
            if cursor["planning_phase"] == "audit":
                try:
                    await self.contract(
                        f"style-plan-audit-r{revision}",
                        AUDIT_INSTRUCTION,
                        self.state(),
                        "audit-plan",
                        "audit",
                    )
                    cursor.update(phase="ready", planning_phase="accepted")
                    await self.checkpoint()
                    break
                except ReplicaPlanAuditRejected as error:
                    cursor.update(
                        prior_audit=error.judgment,
                        revision=revision + 1,
                        planning_phase="plan",
                    )
                    await self.checkpoint()
        else:
            if cursor["phase"] != "ready":
                cursor["phase"] = "rejected"
                await self.checkpoint()
                raise ReplicaPlanAuditRejected(cursor.get("prior_audit") or {})
        route = self.state()["route"]
        state = self.state()
        return [
            {
                "reference": self.context["references"][
                    min(i, len(self.context["references"]) - 1)
                ],
                "analysis": {
                    "commercial_intent": u.get("primary_duty", u.get("narrative_role"))
                },
                "prompt": json.dumps(
                    {
                        k: u.get(k)
                        for k in (
                            "primary_duty",
                            "composition",
                            "visible_copy",
                            "semantic_translation",
                        )
                    },
                    ensure_ascii=False,
                ),
                "unit_id": uid,
                "canvas": state["layout"]["jobs"][
                    i // 2 if route == "DETAIL_PAGE" else i
                ]["canvas"],
            }
            for i, (uid, u) in enumerate(state["units"].items())
        ]


REFERENCE_INSTRUCTION = "Fill the supplied native analysis template completely, including eight EXACT mechanism keys, per-panel content/OCR/density/narrative/semantic handoffs. Keep physical panel metadata unchanged. Also return crop_specs:[{id,reference_input_id,bbox}] to physically crop sanitized style mechanisms with no product, logo, packaging, brand, source copy or literal environment. One crop per panel at minimum. Use corresponding units ids, source_content_step_ids and source_order. source_crop_path will be bound by server. Include useful safe partial glyph/stroke fragments for typography and face-free/product-free garment/action fragments where available, not only background color swatches. Partial unreadable letterform fragments do not carry source wording. Ground GLOBAL composition, hierarchy, density and reading-path observations in the analysis-only physical panels; record source_panel_ids for those mechanisms as well as the native safe crop ids. Set review_status=visual_model only after inspection; source_product_presence and all seven contamination flags are explicit booleans."
CROP_INSTRUCTION = "Reinspect all actual style crops and panel images. Return the corrected COMPLETE analysis. Reject or recrop contamination; do not mark false without visible evidence. Preserve physical panel paths/hashes."
FACTS_INSTRUCTION = "Return native product facts {facts,evidence_inventory,product_category,palette_contract}. Include concrete product geometry (>=3 facts), with correct product input evidence ids. User prose/readable product copy is authorized; benchmark claims are not facts."
PLAN_INSTRUCTION = "Return native {units:[...]}, EXACT layout unit order. Correct every actual prior audit mismatch, without relabeling a failed audit as pass. Bind every source semantic/content step, density sample, transitions and confirmed facts. Rebuild the product world; never inherit literal composition. Visible copy uses target language. Keep the user's original physical product labels unchanged; font-form bans govern only NEW artwork copy. Use full-panel observations for global layout mechanisms and safe crops for supplementary texture/type/action cues. Originality must preserve the mapped persuasive action: if the reference proves human scale through holding/contact/leaning, retain credible human-product interaction in a DIFFERENT scene, pose, camera and composition. Never ban all contact or replace this obligation with only crossed feet. Do not add preserved physical label text to independently authored visible_copy. Fill all route fields and native non-fixture validators."
AUDIT_INSTRUCTION = "Freshly compare plan with original references, actual safe crops and cropped product identity pixels. Return {review_kind:visual_model,route,unit_ids,pass,evidence:[dimension-specific observations]}; fail honestly on wrong mappings, identity, contamination or missed screens. Global composition/density/hierarchy/reading-path evidence comes from physical full-panel analysis, not from product-free color patches alone. Safe supplementary crops must have real useful type/material/action evidence; do not require them to reintroduce the entire original scene. Original product wordmarks remain as explicitly requested; style font bans apply to NEW authored page copy. Never change a semantic failure merely to satisfy a gate."


# Public name used by the physical-evidence integration; one implementation/state.
StyleReplicaRun = DurableStyleAnalysisRun
