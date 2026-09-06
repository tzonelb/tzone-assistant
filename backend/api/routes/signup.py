"""Self-service sign-up, and redeeming an activation code.

Two audiences in one file, and the split matters more here than anywhere else
in the API.

`/api/signup/**` is **unauthenticated by definition** -- it is how somebody who
has no account gets one. Everything it answers is visible to the whole
internet, so each response is built from named fields rather than passed
through from a service: `platform_service.list_plans` does `SELECT *`, which is
right for the operator's console and wrong for a page anybody can open, and the
difference between those two is one careless `return`.

`/api/activation/redeem` is the other end, and it is authenticated: only
somebody signed in to a workspace may turn that workspace into a real one.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from backend.api import session_cookies
from backend.services.activation_service import ActivationError, activation_service
from backend.services.auth_service import auth_service, client_ip, require_permission
from backend.services.platform_service import platform_service
from backend.services.signup_service import SignupError, signup_service


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signup", tags=["Sign-up"])

activation_router = APIRouter(prefix="/api/activation", tags=["Activation"])


class SignupCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class SignupRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=120)
    owner_full_name: str = Field(min_length=1, max_length=120)
    owner_email: str = Field(min_length=3, max_length=320)
    # Bounded above as well as below: a password is hashed with 310,000
    # PBKDF2 rounds, so an unbounded one is a way to spend the server's CPU
    # from an endpoint that needs no account.
    password: str = Field(min_length=10, max_length=200)
    confirm_password: str = Field(min_length=10, max_length=200)
    email_code: str = Field(min_length=1, max_length=32)
    # The design's screen sends both; a workspace created here is a
    # demonstration either way, and the plan is what the owner is asking for
    # rather than what they have been given.
    plan_id: int | None = None
    license_key: str | None = Field(default=None, max_length=64)


class ActivationRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


# What a page anybody can open may know about a plan: its name, what it costs,
# and the two ceilings the screen prints. Not `max_ai_messages`, not the
# connector flags, not anything else a `SELECT *` would carry along.
_PUBLIC_PLAN_FIELDS = (
    "id",
    "code",
    "name",
    "price_monthly",
    "currency",
    "max_users",
    "max_channel_accounts",
)


@router.get("/plans")
def signup_plans() -> dict[str, Any]:
    """The plans a new workspace may ask for.

    Unauthenticated, so the projection is explicit. A plan added later with a
    column nobody meant to publish does not become public by default.
    """
    return {
        "plans": [
            {field: plan.get(field) for field in _PUBLIC_PLAN_FIELDS}
            for plan in platform_service.list_plans()
        ]
    }


@router.post("/code")
def send_signup_code(payload: SignupCodeRequest, request: Request) -> dict[str, Any]:
    try:
        result = signup_service.send_code(
            email=payload.email, ip_address=client_ip(request)
        )
    except SignupError as refusal:
        raise HTTPException(status_code=400, detail=str(refusal)) from refusal

    # Deliberately not echoing the address back. The screen already knows what
    # it typed, and echoing it is how a reflected value ends up rendered.
    return {"sent": bool(result["sent"]), "expires_at": result["expires_at"]}


@router.post("", status_code=status.HTTP_201_CREATED)
def sign_up(
    payload: SignupRequest, request: Request, response: Response
) -> dict[str, Any]:
    """Create a demonstration workspace and sign its owner in.

    The session is minted here rather than sending the owner to the login form
    to retype the password they typed thirty seconds ago -- a worse first
    minute, with nothing gained.

    Minted the same way `/api/auth/login` mints one, cookie included. Returning
    only a bearer token in the body would look correct in a test and leave the
    browser signed out: the app sends `credentials: "include"` and reads the
    httpOnly cookie, not the body.
    """
    if payload.password != payload.confirm_password:
        raise HTTPException(
            status_code=400, detail="The two passwords do not match."
        )

    address = client_ip(request)

    try:
        created = signup_service.create_demo_workspace(
            company_name=payload.company_name,
            owner_full_name=payload.owner_full_name,
            owner_email=payload.owner_email,
            password=payload.password,
            email_code=payload.email_code,
            ip_address=address,
        )
    except SignupError as refusal:
        raise HTTPException(status_code=400, detail=str(refusal)) from refusal

    # No re-authentication: `create_session` is the thing that mints a token,
    # and the owner was created by the call above. Running the password back
    # through `authenticate` would only re-derive PBKDF2 for an answer already
    # known.
    session = auth_service.create_session(
        user_id=created["owner_user_id"],
        ip_address=address,
        user_agent=request.headers.get("user-agent"),
        company_id=created["company_id"],
    )

    csrf_token = session_cookies.attach(
        response,
        request,
        token=session["access_token"],
        expires_in=session["expires_in"],
    )

    return {
        "access_token": session["access_token"],
        "token_type": "bearer",
        "expires_in": session["expires_in"],
        "csrf_token": csrf_token,
        "company_id": created["company_id"],
        "company_name": created["name"],
        "is_demo": True,
        "notice": (
            "This is a demonstration workspace. Everything works except "
            "connecting a real channel — enter an activation code to make it "
            "live."
        ),
    }


@activation_router.post("/redeem")
def redeem_activation_code(
    payload: ActivationRequest,
    current_user: dict[str, Any] = Depends(
        require_permission("subscriptions.manage")
    ),
) -> dict[str, Any]:
    """Turn the signed-in workspace into a real one.

    The company comes from the session and never from the request: a code plus
    a company id in the body would let anyone holding a code spend it on
    somebody else's workspace.

    `subscriptions.manage` rather than merely being signed in. Spending the
    company's activation code is a commercial act, and every employee in a
    demonstration workspace being able to spend it — once, irreversibly, on
    whichever plan it carries — is not a decision any of them should be able to
    take on the owner's behalf.
    """
    company_id = auth_service.resolve_company_id(current_user)

    try:
        result = activation_service.redeem(company_id=company_id, code=payload.code)
    except ActivationError as refusal:
        raise HTTPException(status_code=400, detail=str(refusal)) from refusal

    return {
        "activated": True,
        "activated_at": result["activated_at"],
        "plan_id": result["plan_id"],
    }
