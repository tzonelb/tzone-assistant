from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import get_current_user
from backend.services.security_verification_service import (
    security_verification_service,
    SecurityVerificationError,
)


router = APIRouter(prefix="/api/security", tags=["Security Verification"])


class SendCodeRequest(BaseModel):
    purpose: str = Field(min_length=1, max_length=50)


class VerifyCodeRequest(BaseModel):
    purpose: str = Field(min_length=1, max_length=50)
    code: str = Field(min_length=1, max_length=10)


@router.post("/send-code")
def send_code(
    payload: SendCodeRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    sent, reason = security_verification_service.request_code(
        user_id=current_user["id"], email=current_user["email"], purpose=payload.purpose,
    )
    if not sent:
        raise HTTPException(status_code=503, detail=reason)
    return {"status": "sent", "email_hint": _mask_email(current_user["email"])}


@router.post("/verify-code")
def verify_code(
    payload: VerifyCodeRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    try:
        token = security_verification_service.verify_code(
            user_id=current_user["id"], code=payload.code, purpose=payload.purpose,
        )
    except SecurityVerificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"elevated_token": token}


@router.get("/changes")
def get_changes(
    purpose: str,
    token: str,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    if not security_verification_service.check_elevated(user_id=current_user["id"], token=token, purpose=purpose):
        raise HTTPException(status_code=401, detail="Verification session expired.")
    return {"changes": security_verification_service.get_recent_changes(
        user_id=current_user["id"], purpose=purpose, token=token,
    )}


def _mask_email(email: str) -> str:
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked = name[0] + "*"
    else:
        masked = name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"
