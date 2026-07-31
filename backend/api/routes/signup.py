from fastapi import APIRouter, HTTPException

from pydantic import BaseModel, Field

from backend.services.signup_service import signup_service


router = APIRouter(prefix="/api/signup", tags=["Signup"])


class SignupRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=120)
    owner_full_name: str = Field(min_length=1, max_length=120)
    owner_email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    slug: str | None = Field(default=None, max_length=120)
    plan_id: int | None = None
    country: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=40)


@router.get("/plans")
def list_public_plans():
    """PUBLIC — active plans for display on the signup page."""
    return {"plans": signup_service.public_plans()}


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
            slug=payload.slug,
            plan_id=payload.plan_id,
            country=payload.country,
            phone=payload.phone,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "Not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
