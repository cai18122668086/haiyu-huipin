"""Financial support actions share the wallet lock and permanent receipts."""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import ApiError
from .models import CreditAdminActionRecord
from .services import new_id, require_idempotency_key


def claim(
    session: Session,
    workspace: str,
    actor: str,
    key: str,
    operation: str,
    payload: dict[str, Any],
) -> tuple[CreditAdminActionRecord, bool]:
    key = require_idempotency_key(key)
    digest = hashlib.sha256(
        json.dumps(
            {"operation": operation, "payload": payload}, sort_keys=True
        ).encode()
    ).hexdigest()
    row = session.scalar(
        select(CreditAdminActionRecord).where(
            CreditAdminActionRecord.workspace_id == workspace,
            CreditAdminActionRecord.actor_user_id == actor,
            CreditAdminActionRecord.idempotency_key == key,
        )
    )
    if row:
        legacy_digest = hashlib.sha256(
            (
                actor + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            ).encode()
        ).hexdigest()
        compatible = operation == "adjustment" and row.request_hash == legacy_digest
        if row.request_hash != digest and not compatible:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "此请求编号已用于其他操作。")
        return row, True
    row = CreditAdminActionRecord(
        id=new_id("adm"),
        workspace_id=workspace,
        actor_user_id=actor,
        idempotency_key=key,
        request_hash=digest,
        response_body={},
    )
    session.add(row)
    return row, False
