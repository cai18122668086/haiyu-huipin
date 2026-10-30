from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .auth import Principal, SessionDependency, get_principal
from .processing_consent import (
    NOTICE,
    POLICY_VERSION,
    ProcessingConsentChoice,
    current_consent,
    record_consent,
    withdraw_consent,
)

router = APIRouter(prefix="/api/v1/me/processing-consent", tags=["identity"])
PrincipalDependency = Annotated[Principal, Depends(get_principal)]


class ConsentStatus(BaseModel):
    policy_version: str
    accepted: bool
    accepted_at: str | None
    notice: dict[str, str]


@router.get("", response_model=ConsentStatus)
def status(principal: PrincipalDependency, session: SessionDependency) -> dict:
    record = current_consent(session, principal.user_id)
    return {
        "policy_version": POLICY_VERSION,
        "accepted": record is not None,
        "accepted_at": record.accepted_at.isoformat() if record else None,
        "notice": NOTICE,
    }


@router.post("", response_model=ConsentStatus)
def accept(
    choice: ProcessingConsentChoice,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict:
    record_consent(session, principal.user_id, choice.model_dump(), source="account")
    session.commit()
    return status(principal, session)


@router.delete("", response_model=ConsentStatus)
def withdraw(principal: PrincipalDependency, session: SessionDependency) -> dict:
    withdraw_consent(session, principal.user_id)
    session.commit()
    return status(principal, session)
