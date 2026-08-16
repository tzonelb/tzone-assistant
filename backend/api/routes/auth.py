import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
)
from backend.services.auth_service import (
    auth_service,
    client_ip,
    get_current_user,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# One message for every failure. Telling the caller which of the four
# credentials was wrong would let them enumerate companies, codes and emails.
INVALID_CREDENTIALS = "Workspace code, company, email or password is incorrect."


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request):
    ip_address = client_ip(request)
    email = str(payload.email)

    if auth_service.is_login_blocked(email=email, ip_address=ip_address):
        logger.warning("Login blocked by rate limit for %s", ip_address)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many failed attempts. "
                "Wait a few minutes before trying again."
            ),
        )

    user = auth_service.authenticate(
        workspace_code=payload.workspace_code,
        company=payload.company,
        email=email,
        password=payload.password,
    )

    if not user:
        auth_service.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="invalid_credentials",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    auth_service.record_login_attempt(
        email=email, ip_address=ip_address, succeeded=True
    )
    auth_service.clear_login_attempts(email)

    company_id = int(user["active_company_id"])

    session_data = auth_service.create_session(
        user_id=user["id"],
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent"),
        company_id=company_id,
    )

    return {
        "access_token": session_data["access_token"],
        "token_type": "bearer",
        "expires_in": session_data["expires_in"],
        "user": user,
        "permissions": auth_service.user_permission_codes(user["id"], company_id),
    }


@router.get("/me", response_model=CurrentUserResponse)
def get_me(current_user: dict = Depends(get_current_user)):
    companies = auth_service.get_user_companies(current_user["id"])

    safe_user = dict(current_user)
    safe_user.pop("_raw_token", None)

    permissions: list[str] = []
    company_id = current_user.get("active_company_id")

    if company_id is not None:
        permissions = auth_service.user_permission_codes(
            current_user["id"], int(company_id)
        )

    return {
        "user": safe_user,
        "companies": companies,
        "permissions": permissions,
    }


@router.post("/logout", response_model=LogoutResponse)
def logout(current_user: dict = Depends(get_current_user)):
    raw_token = current_user.get("_raw_token")

    if raw_token:
        auth_service.revoke_token(raw_token)

    return {"success": True, "message": "Logged out successfully."}
