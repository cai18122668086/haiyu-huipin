"""Versioned, explicit processing choices; never infer consent from a session."""

import hashlib
import json
from importlib.resources import files
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import ApiError
from .models import ProcessingConsentRecord, UserRecord, utc_now

NOTICE = json.loads(files("app").joinpath("processing_notice.json").read_text("utf-8"))
POLICY_VERSION: str = NOTICE["version"]
NOTICE_HASH = hashlib.sha256(
    json.dumps(NOTICE, ensure_ascii=False, sort_keys=True).encode()
).hexdigest()


class ProcessingConsentChoice(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    policyVersion: str
    hongKongProcessing: bool
    aiTraining: bool


def validate_choice(value: Any) -> ProcessingConsentChoice:
    try:
        choice = ProcessingConsentChoice.model_validate(value)
    except ValidationError as exc:
        raise ApiError(
            400,
            "PROCESSING_CONSENT_REQUIRED",
            "请分别阅读并确认香港处理与模型训练说明。",
        ) from exc
    if choice.policyVersion != POLICY_VERSION:
        raise ApiError(
            409, "PROCESSING_POLICY_CHANGED", "处理说明已更新，请刷新后重新确认。"
        )
    if not choice.hongKongProcessing or not choice.aiTraining:
        raise ApiError(
            400,
            "PROCESSING_CONSENT_REQUIRED",
            "请分别阅读并确认香港处理与模型训练说明。",
        )
    return choice


def current_consent(session: Session, user_id: str) -> ProcessingConsentRecord | None:
    return session.scalar(
        select(ProcessingConsentRecord)
        .where(
            ProcessingConsentRecord.user_id == user_id,
            ProcessingConsentRecord.policy_version == POLICY_VERSION,
            ProcessingConsentRecord.notice_sha256 == NOTICE_HASH,
            ProcessingConsentRecord.withdrawn_at.is_(None),
        )
        .order_by(ProcessingConsentRecord.accepted_at.desc())
        .limit(1)
    )


def record_consent(
    session: Session, user_id: str, value: Any, *, source: str
) -> ProcessingConsentRecord:
    choice = validate_choice(value)
    # Serialize acceptance/revocation on the existing user without changing
    # identity, credential versions, wallet balances, or running tasks.
    session.execute(
        select(UserRecord.id).where(UserRecord.id == user_id).with_for_update()
    )
    existing = current_consent(session, user_id)
    if existing is not None:
        return existing
    record = ProcessingConsentRecord(
        id="pc_" + uuid4().hex,
        user_id=user_id,
        policy_version=POLICY_VERSION,
        notice_sha256=NOTICE_HASH,
        notice=dict(NOTICE),
        choices=choice.model_dump(),
        source=source,
        accepted_at=utc_now(),
    )
    session.add(record)
    session.flush()
    return record


def withdraw_consent(session: Session, user_id: str) -> None:
    session.execute(
        select(UserRecord.id).where(UserRecord.id == user_id).with_for_update()
    )
    records = session.scalars(
        select(ProcessingConsentRecord).where(
            ProcessingConsentRecord.user_id == user_id,
            ProcessingConsentRecord.withdrawn_at.is_(None),
        )
    )
    for record in records:
        record.withdrawn_at = utc_now()


def require_processing_consent(session: Session, user_id: str) -> None:
    if current_consent(session, user_id) is None:
        raise ApiError(
            403,
            "PROCESSING_CONSENT_REQUIRED",
            "请先在账户设置中分别确认香港处理与模型训练说明，再上传素材或开始新创作。",
        )
