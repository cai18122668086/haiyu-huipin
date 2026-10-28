"""Repair the approved inspiration case catalog and its asset-role map.

The migration is intentionally fail-closed: it renames the two slug-backed
cards before applying title-keyed canonical content, archives the three legacy
cards, and creates the missing generated-AI-model card without borrowing any
existing asset.
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op

from app.inspiration_workflows import (
    CANONICAL_ASSETS_BY_TITLE,
    CANONICAL_PROMPT_BY_TITLE,
    CANONICAL_SHA_BY_FILENAME,
    CASES_REQUIRING_ASSET_REVIEW,
    FINAL_INSPIRATION_WORKFLOW_BY_TITLE,
)

revision = "20260831_0015"
down_revision = "20260828_0014"
branch_labels = None
depends_on = None


def _asset_ids_by_sha(bind: sa.Connection) -> dict[str, str]:
    rows = bind.execute(
        sa.text("SELECT id, sha256 FROM assets WHERE deleted_at IS NULL")
    )
    return {row.sha256: row.id for row in rows}


def _case_ids(bind: sa.Connection) -> dict[str, str]:
    rows = bind.execute(sa.text("SELECT id, title FROM inspiration_cases"))
    return {row.title: row.id for row in rows}


def _stable_relation_id(case_id: str, asset_id: str, role: str, ordinal: int) -> str:
    digest = hashlib.sha256(
        f"{case_id}:{asset_id}:{role}:{ordinal}".encode()
    ).hexdigest()[:32]
    return f"ica_{digest}"


def _prepare_catalog(bind: sa.Connection) -> None:
    # The source catalog uses slugs as the stable identity for these two cards.
    # Resolve the duplicate historical titles before any title-keyed update.
    bind.execute(
        sa.text(
            "UPDATE inspiration_cases SET title = :new_title "
            "WHERE slug = :slug AND title = :old_title"
        ),
        {
            "slug": "focus-03",
            "old_title": "场景图",
            "new_title": "场景图（服装）",
        },
    )
    bind.execute(
        sa.text(
            "UPDATE inspiration_cases SET title = :new_title "
            "WHERE slug = :slug AND title = :old_title"
        ),
        {
            "slug": "focus-06",
            "old_title": "场景图",
            "new_title": "场景图（玩偶）",
        },
    )

    # These cards are outside the approved 16-card public catalog. Keep their
    # rows and assets for rollback/audit, but make them unavailable publicly.
    bind.execute(
        sa.text(
            "UPDATE inspiration_cases SET status = 'archived', "
            "archived_at = CURRENT_TIMESTAMP, configuration_status = 'archived', "
            "is_pinned = FALSE WHERE slug IN ('detail', 'white', 'poster')"
        )
    )

    # There is no verified historical asset pair for this newly approved card.
    # Create a transparent, incomplete record rather than borrowing focus-00 or
    # any other case's cover/result. It becomes executable only after a later
    # admin configuration provides the missing verified content.
    existing = bind.execute(
        sa.text("SELECT id FROM inspiration_cases WHERE title = :title"),
        {"title": "生成AI模特"},
    ).scalar_one_or_none()
    if existing is None:
        owner = bind.execute(
            sa.text("SELECT id FROM users ORDER BY id LIMIT 1")
        ).first()
        if owner is None:
            raise RuntimeError("Cannot create generated-AI-model case without an owner")
        bind.execute(
            sa.text(
                "INSERT INTO inspiration_cases "
                "(id, slug, title, description, role, type, category, platform, ratio, "
                "intent, prompt, status, sort_order, is_pinned, created_by_user_id, "
                "updated_by_user_id, published_at, workflow_type, reference_source, "
                "configuration_status, button_label) VALUES "
                "(:id, :slug, :title, :description, :role, :type, :category, "
                " :platform, "
                ":ratio, :intent, :prompt, 'published', :sort_order, FALSE, "
                ":created_by, "
                ":updated_by, CURRENT_TIMESTAMP, 'product_prompt', "
                "'none', 'incomplete', "
                ":button_label)"
            ),
            {
                "id": "ins_generated_ai_model",
                "slug": "generated-ai-model",
                "title": "生成AI模特",
                "description": "根据用户商品图生成原创电商模特展示图。",
                "role": "AI 模特商品图",
                "type": "场景图",
                "category": "其他",
                "platform": "国内",
                "ratio": "自动",
                "intent": "让商品在自然、真实的模特关系中清晰展示。",
                "prompt": CANONICAL_PROMPT_BY_TITLE["生成AI模特"],
                "sort_order": -1,
                "created_by": owner.id,
                "updated_by": owner.id,
                "button_label": "一键做同款",
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    # The canonical catalog is provisioned separately for a fresh database.
    # Keep the schema migration valid before that provisioning step runs.
    case_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM inspiration_cases")
    ).scalar_one()
    if case_count == 0:
        return
    _prepare_catalog(bind)
    cases = _case_ids(bind)
    assets = _asset_ids_by_sha(bind)

    missing_cases = sorted(set(FINAL_INSPIRATION_WORKFLOW_BY_TITLE) - set(cases))
    if missing_cases:
        raise RuntimeError(
            "Approved inspiration cases are missing after catalog preparation: "
            + ", ".join(missing_cases)
        )

    for title, workflow in FINAL_INSPIRATION_WORKFLOW_BY_TITLE.items():
        case_id = cases[title]
        expected_assets = CANONICAL_ASSETS_BY_TITLE.get(title, ())
        missing_assets = [
            filename
            for filename, _role, _ordinal in expected_assets
            if CANONICAL_SHA_BY_FILENAME.get(filename) not in assets
        ]

        bind.execute(
            sa.text(
                "UPDATE inspiration_cases SET prompt = :prompt, "
                "workflow_type = :workflow_type, "
                "reference_source = :reference_source, "
                "configuration_status = :configuration_status "
                "WHERE id = :case_id"
            ),
            {
                "prompt": CANONICAL_PROMPT_BY_TITLE[title],
                "workflow_type": workflow,
                "reference_source": (
                    "admin_reference" if workflow == "reference_remix" else "none"
                ),
                "configuration_status": (
                    "incomplete"
                    if title in CASES_REQUIRING_ASSET_REVIEW
                    or missing_assets
                    or (
                        workflow == "reference_remix"
                        and not any(
                            role == "reference" for _f, role, _o in expected_assets
                        )
                    )
                    else "ready"
                ),
                "case_id": case_id,
            },
        )

        # Rebuild only the managed case relationships. Results remain display-
        # only and can never be promoted to a reference by this migration.
        bind.execute(
            sa.text("DELETE FROM inspiration_case_assets WHERE case_id = :case_id"),
            {"case_id": case_id},
        )
        for filename, role, ordinal in expected_assets:
            asset_id = assets.get(CANONICAL_SHA_BY_FILENAME[filename])
            if asset_id is None:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO inspiration_case_assets "
                    "(id, case_id, asset_id, role, ordinal, label) "
                    "VALUES (:id, :case_id, :asset_id, :role, :ordinal, :label)"
                ),
                {
                    "id": _stable_relation_id(case_id, asset_id, role, ordinal),
                    "case_id": case_id,
                    "asset_id": asset_id,
                    "role": role,
                    "ordinal": ordinal,
                    "label": "管理员参考图" if role == "reference" else None,
                },
            )


def downgrade() -> None:
    # The previous migration did not contain the approved prompts or asset
    # relationships. Do not silently recreate the known mismatch.
    pass
