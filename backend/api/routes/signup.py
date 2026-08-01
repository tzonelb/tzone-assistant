from fastapi import APIRouter, HTTPException

from pydantic import BaseModel, Field

from backend.services.signup_service import signup_service


router = APIRouter(prefix="/api/signup", tags=["Signup"])


class SignupRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=120)
    owner_full_name: str = Field(min_length=1, max_length=120)
    owner_email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    confirm_password: str = Field(min_length=8, max_length=200)
    email_code: str = Field(min_length=6, max_length=6)
    slug: str | None = Field(default=None, max_length=120)
    plan_id: int | None = None
    license_key: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=40)


class SendCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)


@router.get("/plans")
def list_public_plans():
    """PUBLIC — active plans for display on the signup page."""
    return {"plans": signup_service.public_plans()}


@router.post("/send-code")
def send_verification_code(payload: SendCodeRequest):
    """PUBLIC — emails a one-time code to confirm the address before an
    account is created with it."""
    try:
        sent, reason = signup_service.send_verification_code(payload.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not sent:
        raise HTTPException(status_code=502, detail=reason or "Could not send the verification email.")
    return {"sent": True}


@router.post("")
def signup(payload: SignupRequest):
    """PUBLIC — register a company + owner account, start a trial, and
    return an access token so the frontend can log the owner straight in."""
    try:
        return signup_service.signup(
            company_name=payload.company_name,
            owner_full_name=payload.owner_full_name,
            owner_email=payload.owner_email,
            password=payload.password,
            confirm_password=payload.confirm_password,
            email_code=payload.email_code,
            slug=payload.slug,
            plan_id=payload.plan_id,
            license_key=payload.license_key,
            country=payload.country,
            phone=payload.phone,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "Not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
