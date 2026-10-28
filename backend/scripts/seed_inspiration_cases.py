"""Import inspiration cases from a JSON file into the database.

The input is a JSON array matching InspirationCaseCreate, with optional
``assets`` entries. Images should be uploaded first through the normal asset
upload API; this keeps object storage and retention policies consistent.
Run from ``backend`` with ``python scripts/seed_inspiration_cases.py file.json``.
"""

import json
import sys

from sqlalchemy import select

from app.config import Settings
from app.database import build_database
from app.inspiration_routes import _save_assets
from app.inspiration_schemas import InspirationCaseCreate
from app.models import InspirationCaseRecord, UserRecord
from app.services import new_id


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: seed_inspiration_cases.py cases.json")
    with open(sys.argv[1], encoding="utf-8") as handle:
        source_items = json.load(handle)
    payloads = [InspirationCaseCreate.model_validate(item) for item in source_items]
    settings = Settings()
    engine, factory = build_database(settings.database_url)
    with factory() as session:
        user_id = session.scalar(select(UserRecord.id))
        if not user_id:
            raise SystemExit("no user exists; run the application once before seeding")
        for payload in payloads:
            item = InspirationCaseRecord(
                id=new_id("ins"),
                **payload.model_dump(exclude={"assets"}),
                status="published",
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
            )
            session.add(item)
            session.flush()
            _save_assets(item, payload, session)
        session.commit()
    engine.dispose()
    print(f"imported {len(payloads)} inspiration cases")


if __name__ == "__main__":
    main()
