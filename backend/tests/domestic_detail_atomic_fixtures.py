"""Complete minimal native plans and real fixed slices for isolated review tests."""

from __future__ import annotations

from pathlib import Path

from app.aurora_detail_runtime import AuroraDetailHarnessRunner


def minimal_atomic_plan(module_id, order, method="GRAPHIC_OVERLAY", width=3, height=4):
    return {
        "id": module_id,
        "order": order,
        "duty": "Synthetic buyer evidence for " + module_id,
        "expression_contract_version": "4.3",
        "primary_subject_class": "COMPLETE_PRODUCT",
        "presentation_form": "HERO_POSTER",
        "camera_scale": "close",
        "complete_product_role": "DOMINANT",
        "expression_signature": "COMPLETE_PRODUCT+HERO_POSTER+close+DOMINANT",
        "visual_proof_contract": {"minimum_visual_payload": 1},
        "visible_copy": [],
        "text_system": {
            "primary_mode": "centered_hero_headline",
            "anchor_zone": "upper",
        },
        "independent_logo_policy": "FORBIDDEN",
        "module_visual_expression_contract": {
            "primary_visual_carrier": "PRODUCT_HERO",
            "claim_visualizability": "VISUALIZABLE",
            "product_presence_policy": "REQUIRED_DOMINANT",
        },
        "product_presence_decision": {"synthetic": True},
        "content_unit_contract": {"units": [{"text": "Synthetic source-backed unit"}]},
        "module_spatial_structure_contract": {
            "structure_family": "FULL_BLEED_HERO",
            "zone_briefs": ["Synthetic dominant evidence zone"],
        },
        "transition_contract": {"method_id": method},
        "output": {"width_px": width, "height_px": height},
    }


def review_context(root):
    runner = AuroraDetailHarnessRunner()
    lineage = runner.run(
        "inspect-pair-artifacts",
        root,
        {"session": root / "session.json", "pair-id": "P01"},
    )
    contracts = runner.run(
        "inspect-pair-review-contracts",
        root,
        {"session": root / "session.json", "pair-id": "P01"},
    )["module_contracts"]
    return {
        "pair_id": "P01",
        "module_ids": ["M01", "M02"],
        "approved_module_contracts": contracts,
        "pixel_lineage": lineage,
        "pair_render": {
            "internal_seam_method_id": contracts[1]["contract"]["transition_contract"][
                "method_id"
            ]
        },
    }


def create_atomic_context(root, artifact, method="EDGE_BLEED"):
    from tests.test_native_detail_failure_transition import record_fixture

    root.mkdir(parents=True)
    source = root / "source.png"
    source.write_bytes(artifact.payload)
    current = {
        "version": "4.3.1",
        "state": "AWAITING_GENERATED_MODULE",
        "current_index": 0,
        "session_dir": str(root),
        "plan": {
            "output_strategy": {
                "module_count": 2,
                "canvas_width_px": artifact.width,
                "module_height_px": artifact.height // 2,
                "target_platform": "isolated native fixture",
                "verification_status": "BACKEND_CONFIRMATION_REQUIRED",
                "continuous_pair_rendering": {
                    "enabled": True,
                    "pair_merge_type": "PAIR_PLAN_MERGE",
                    "pair_canvas_height_px": artifact.height,
                    "pair_ids": [{"pair_id": "P01", "module_ids": ["M01", "M02"]}],
                },
            }
        },
        "modules": [
            {
                "id": f"M{index:02d}",
                "order": index,
                "pair_id": "P01",
                "status": "PENDING",
                "cycle_attempt_count": 0,
                "attempts": [],
                "plan": minimal_atomic_plan(
                    f"M{index:02d}", index, method, artifact.width, artifact.height // 2
                ),
            }
            for index in (1, 2)
        ],
    }
    record_fixture(root, current, source, "unit-native-token")
    return review_context(root)


def attached_atomic_sources(context, sources):
    from app.xinghe_agent import SourceImage

    return (
        *sources,
        *(
            SourceImage(
                asset_id="slice:" + row["module_id"],
                group_key="primary",
                filename=row["module_id"] + ".png",
                content_type="image/png",
                sha256=row["sha256"],
                payload=Path(row["path"]).read_bytes(),
                source_role="generated_module_candidate:" + row["module_id"],
            )
            for row in context["pixel_lineage"]["slices"]
        ),
    )
