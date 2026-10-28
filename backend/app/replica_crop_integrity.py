"""The original Skill's physical-crop gate, before any detailed planning."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .replica_exact_contracts import canonical_hash
from .replica_native import (
    ReplicaAnalysisUnavailable,
    ReplicaNativeError,
    digest,
    write_json,
)

CHECKS = ("headlines_attached", "dependent_copy_complete", "visual_carriers_complete")


class SourceCropIncomplete(ReplicaAnalysisUnavailable):
    code = "REPLICA_SOURCE_CROP_INCOMPLETE"

    def __init__(self, column_id: str, reason: str) -> None:
        from .provider_diagnostics import safe_text

        self.message = (
            f"参考列 {column_id} 的物理裁切仍不完整：{safe_text(reason, 1000)}；"
            "已有素材与裁切记录已保留，尚未开始生图。"
        )
        ReplicaNativeError.__init__(self, self.message)


def review_schema(column_id: str, count: int, boundaries: list[int]) -> dict[str, Any]:
    properties = {
        "column_id": {"const": column_id},
        "screen_count": {"const": count},
        "decision": {"enum": ["PASS", "REVISE"]},
        "boundary_ys": {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": count - 1,
            "maxItems": count - 1,
        },
        "evidence": {"type": "string", "minLength": 20, "maxLength": 1600},
        **{name: {"type": "boolean"} for name in CHECKS},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
        "allOf": [
            {
                "if": {"properties": {"decision": {"const": "PASS"}}},
                "then": {
                    "properties": {
                        **{name: {"const": True} for name in CHECKS},
                        "boundary_ys": {"const": boundaries},
                    }
                },
            },
            {
                "if": {"properties": {"decision": {"const": "REVISE"}}},
                "then": {
                    "anyOf": [
                        {"properties": {name: {"const": False}}} for name in CHECKS
                    ]
                },
            },
        ],
    }


async def _review(
    run: Any, column: Any, proposal: Any, paths: list[Path]
) -> dict[str, Any]:
    from .replica_exact_compact import CompactResponseInvalid
    from .xinghe_agent import PlannerRequest

    sources = tuple(
        replace(
            source, source_role="reference_column" if i == 0 else "reference_screen"
        )
        for i, source in enumerate(run.sources(paths))
    )
    boundaries = proposal["boundary_ys"]
    data = {
        "column_id": column["column_id"],
        "screen_count": len(proposal["screens"]),
        "source_size": {"width": column["width"], "height": column["height"]},
        "boundary_ys": boundaries,
        "screens": proposal["screens"],
        "image_order": [
            {"image": i + 1, "role": s.source_role} for i, s in enumerate(sources)
        ],
    }
    schema = review_schema(column["column_id"], len(proposal["screens"]), boundaries)
    system = "\n".join(
        (run.skill_root / "references/automatic-segmentation.md")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    system += "\n" + Path(__file__).with_name("replica_exact_prompts").joinpath(
        "crop_integrity.md"
    ).read_text(encoding="utf-8")
    subject = canonical_hash(
        {
            "protocol": run.binding,
            "planner": run._planner_identity(),
            "context": data,
            "schema": schema,
            "system": system,
            "source_bytes": [digest(p.read_bytes()) for p in paths],
            "wire_images": [s.sha256 for s in sources],
        }
    )
    passes = run.checkpoints.setdefault("source_crop_passes", {})
    cached = passes.get(subject)
    if cached:
        if canonical_hash(cached["raw"]) != cached["response_sha256"]:
            raise CompactResponseInvalid("Cached physical crop verdict was changed")
        if not Draft202012Validator(schema).is_valid(cached["raw"]):
            raise CompactResponseInvalid(
                "Cached physical crop verdict violates its schema"
            )
        if cached["raw"]["decision"] != "PASS":
            raise CompactResponseInvalid("Only a real complete crop PASS may be reused")
        return deepcopy(cached["raw"])
    error = ""
    for attempt in range(2):
        envelope = {
            "stage": "crop_integrity",
            "context": data,
            "response_schema": schema,
            "image_order": data["image_order"],
            "validation_error": error,
        }
        result = await run.gateway.plan(
            PlannerRequest(
                workspace_id=run.workspace_id,
                job_id=run.document_id,
                group_key="source-crop-" + column["column_id"],
                sources=sources,
                page_count=1,
                language="zh-CN",
                product_name="原始参考图裁切",
                product_information=(json.dumps(envelope, ensure_ascii=False),),
                platform_code="tmall",
                size="1024x1024",
                idempotency_key=f"{run.document_id}:crop:{subject}:format-{attempt}",
                planning_profile="replica_exact_compact",
                system_prompt=system,
            )
        )
        run.record_usage(result, "analysis", 1)
        run.usage[-1].update(
            provider_request_id=getattr(result, "provider_request_id", None),
            http_status=getattr(result, "http_status", None),
        )
        await run.checkpoint()
        if result.error_code or not isinstance(result.payload, dict):
            raise ReplicaAnalysisUnavailable()
        raw = deepcopy(result.payload)
        write_json(
            run.root / "contracts" / f"crop-integrity-{subject}-{attempt}.json", raw
        )
        failures = list(Draft202012Validator(schema).iter_errors(raw))
        cuts = raw.get("boundary_ys", [])
        if failures:
            error = "; ".join(
                f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
                for e in failures
            )
            continue
        if cuts != sorted(set(cuts)) or any(not 0 < y < column["height"] for y in cuts):
            error = (
                "Boundary coordinates must increase within the original column height"
            )
            continue
        record = {
            "subject_sha256": subject,
            "raw": raw,
            "response_sha256": canonical_hash(raw),
            "provider_request_id": getattr(result, "provider_request_id", None),
            "source_sha256": [digest(p.read_bytes()) for p in paths],
        }
        run.context.setdefault("source_crop_integrity", []).append(record)
        if raw["decision"] == "PASS":
            passes[subject] = record
        write_json(
            run.root / "source-crop-integrity.json",
            run.context["source_crop_integrity"],
        )
        await run.checkpoint()
        return raw
    raise CompactResponseInvalid(
        "Physical source crop response failed its complete schema: " + error
    )


async def validate_source_crops(run: Any, columns: Any, split: Any) -> dict[str, Any]:
    split = deepcopy(split)
    if any(len(row["screens"]) > 1 for row in split["columns"]) and callable(
        run.on_progress
    ):
        run.on_progress(12, "核验拆屏内容是否完整")
    for column, proposal in zip(columns, split["columns"], strict=True):
        if len(proposal["screens"]) <= 1:
            continue
        for revision in range(2):
            edges = [0, *proposal["boundary_ys"], column["height"]]
            paths = [Path(column["crop_path"])]
            for index, (top, bottom) in enumerate(zip(edges, edges[1:], strict=False)):
                target = (
                    run.root
                    / "crops"
                    / f"source-review-{column['column_id']}-{revision}-{index}.png"
                )
                run.crop(paths[0], [0, top, column["width"], bottom], target)
                paths.append(target)
            verdict = await _review(run, column, proposal, paths)
            if verdict["decision"] == "PASS":
                break
            if revision == 1 or verdict["boundary_ys"] == proposal["boundary_ys"]:
                raise SourceCropIncomplete(column["column_id"], verdict["evidence"])
            proposal["boundary_ys"] = verdict["boundary_ys"]
            # A reviewer's y estimate can itself cut dependent copy. Resolve only
            # an unambiguous nearby source-pixel gap, then inspect the real crops.
            from .replica_boundary_geometry import resolve_boundaries

            corrected, _, receipts = resolve_boundaries(
                [column],
                {"columns": [proposal]},
                {"columns": [proposal]},
                include_agreed=True,
            )
            proposal["boundary_ys"] = corrected["columns"][0]["boundary_ys"]
            run.context.setdefault("source_crop_boundary_resolutions", []).extend(
                receipts
            )
    return split
