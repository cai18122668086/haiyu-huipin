"""Provision all 18 historical inspiration cards into a local development API.

Existing examples are kept; archived legacy prompt templates can be restored.
All canonical files are verified before any writes;
uploads use the normal API; catalog records use the operator seeding path and
the same asset-role and publication validation, without changing account roles.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import select  # noqa: E402

from app.database import build_database  # noqa: E402
from app.inspiration_legacy_templates import LEGACY_PROMPT_TEMPLATES  # noqa: E402
from app.inspiration_routes import _save_assets, _sync_configuration_status  # noqa: E402
from app.inspiration_schemas import InspirationCaseCreate  # noqa: E402
from app.inspiration_workflows import (  # noqa: E402
    CANONICAL_ASSETS_BY_TITLE,
    CANONICAL_METADATA_BY_TITLE,
    CANONICAL_PROMPT_BY_TITLE,
    CANONICAL_SHA_BY_FILENAME,
    FINAL_INSPIRATION_WORKFLOW_BY_TITLE,
)
from app.models import InspirationCaseRecord, UserRecord  # noqa: E402
from app.services import new_id  # noqa: E402
from scripts.import_inspiration_references import (  # noqa: E402
    _asset_input,
    _multipart_upload,
    _request_json,
)


def catalog_specifications() -> list[dict]:
    """Keep all 18 original cards, with the three prompt-only templates explicit."""
    entries = []
    for index, (title, metadata) in enumerate(CANONICAL_METADATA_BY_TITLE.items()):
        workflow = FINAL_INSPIRATION_WORKFLOW_BY_TITLE[title]
        entries.append(
            {
                **metadata,
                "slug": f"canonical-{hashlib.sha256(title.encode()).hexdigest()[:16]}",
                "title": title,
                "prompt": CANONICAL_PROMPT_BY_TITLE[title],
                "workflow_type": workflow,
                "reference_source": "admin_reference"
                if workflow == "reference_remix"
                else "none",
                "button_label": "一键做同款",
                "sort_order": index
                if index < 3
                else index + len(LEGACY_PROMPT_TEMPLATES),
                "asset_specs": CANONICAL_ASSETS_BY_TITLE[title],
            }
        )
    entries.extend(
        {
            **item,
            "workflow_type": "product_prompt",
            "reference_source": "none",
            "button_label": "使用模板",
            "sort_order": index + 3,
            "asset_specs": (),
        }
        for index, item in enumerate(LEGACY_PROMPT_TEMPLATES)
    )
    return sorted(entries, key=lambda item: item["sort_order"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:18821")
    parser.add_argument("--workspace-id", default="ws_development")
    parser.add_argument("--user-id", default="usr_development")
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "backend/.data/haiyu-preview-latest/preview.db",
    )
    args = parser.parse_args()
    if urlsplit(args.api).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit(
            "This provisioning command only targets a local development API."
        )
    source = ROOT / "public/images/inspiration"
    for filename, digest in CANONICAL_SHA_BY_FILENAME.items():
        if hashlib.sha256((source / filename).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Canonical asset verification failed: {filename}")

    def request(path: str, **kwargs):
        return _request_json(args.api, path, user_id=args.user_id, **kwargs)

    if not args.database.is_file():
        raise SystemExit("The local catalog database must already exist.")
    engine, factory = build_database("sqlite:///" + args.database.resolve().as_posix())
    with factory() as session:
        if session.get(UserRecord, args.user_id) is None:
            raise SystemExit("The creator must already exist in the local database.")
        by_title = {
            item.title: item.status
            for item in session.scalars(select(InspirationCaseRecord))
        }
    catalog = catalog_specifications()
    template_titles = {item["title"] for item in LEGACY_PROMPT_TEMPLATES}
    pending = [
        item
        for item in catalog
        if item["title"] not in by_title
        or (item["title"] in template_titles and by_title[item["title"]] == "archived")
    ]
    if not pending:
        print(f"Catalog already provisioned: {len(catalog)} original cards.")
        engine.dispose()
        return
    assets = request(f"/api/v1/workspaces/{args.workspace_id}/assets?limit=100")[
        "items"
    ]
    by_sha = {asset["sha256"]: asset["id"] for asset in assets}
    imported = 0
    for definition in pending:
        title = definition["title"]
        relations = []
        for filename, role, ordinal in definition["asset_specs"]:
            digest = CANONICAL_SHA_BY_FILENAME[filename]
            if digest not in by_sha:
                by_sha[digest] = _multipart_upload(
                    args.api, args.workspace_id, args.user_id, source / filename
                )["id"]
            relations.append(_asset_input(by_sha[digest], role, ordinal))
        payload = {
            **{key: value for key, value in definition.items() if key != "asset_specs"},
            "assets": relations,
        }
        validated = InspirationCaseCreate.model_validate(payload)
        with factory() as session:
            # Recheck inside the transaction so a rerun cannot duplicate a case.
            record = session.scalar(
                select(InspirationCaseRecord).where(
                    InspirationCaseRecord.title == title
                )
            )
            if record is not None:
                if title not in template_titles or record.status != "archived":
                    continue
                for key, value in validated.model_dump(exclude={"assets"}).items():
                    setattr(record, key, value)
                record.status = "draft"
                record.archived_at = None
            else:
                record = InspirationCaseRecord(
                    id=new_id("ins"),
                    **validated.model_dump(exclude={"assets"}),
                    status="draft",
                    created_by_user_id=args.user_id,
                    updated_by_user_id=args.user_id,
                )
                session.add(record)
            session.flush()
            _save_assets(record, validated, session)
            session.flush()
            errors = _sync_configuration_status(record, session)
            if errors:
                raise RuntimeError(f"Cannot publish {title}: {errors}")
            record.status = "published"
            record.published_at = datetime.now(UTC)
            record.configuration_status = "ready"
            session.commit()
        imported += 1
        print(f"Published {title} ({len(relations)} assets)")
    with factory() as session:
        # Restore the historical card order without overwriting existing copy or assets.
        for definition in catalog:
            item = session.scalar(
                select(InspirationCaseRecord).where(
                    InspirationCaseRecord.title == definition["title"]
                )
            )
            if item is not None:
                item.sort_order = definition["sort_order"]
        session.commit()
    print(
        f"Imported {imported} original cards. "
        "Existing case copy and assets were preserved."
    )
    engine.dispose()


if __name__ == "__main__":
    main()
