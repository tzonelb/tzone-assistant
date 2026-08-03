from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)

from backend.api.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    SuperAdminLoginRequest,
    TwoFactorDisableRequest,
    TwoFactorEnrollConfirmRequest,
    TwoFactorVerifyRequest,
)
from backend.services.auth_service import (
    auth_service,
    get_current_user,
)


router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


@router.post(
    "/login",
    response_model=LoginResponse,
)
def login(
    payload: LoginRequest,
    request: Request,
):
    user = auth_service.authenticate(
        email=payload.email,
        password=payload.password,
        company=payload.company,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Company, email or password is incorrect.",
        )

    # Password was correct. If the account has 2FA enabled, stop here and
    # return a short-lived pending token instead of a full session.
    if auth_service.user_has_2fa(user["id"]):
        pending_token = auth_service.create_pending_2fa_token(
            user_id=user["id"],
            company_id=user.get("active_company_id"),
        )
        return {
            "twofa_required": True,
            "pending_token": pending_token,
        }

    ip_address = None

    if request.client:
        ip_address = request.client.host

    user_agent = request.headers.get(
        "user-agent"
    )

    session_data = auth_service.create_session(
        user_id=user["id"],
        ip_address=ip_address,
        user_agent=user_agent,
        company_id=user.get("active_company_id"),
    )

    return {
        "access_token": session_data["access_token"],
        "token_type": "bearer",
        "expires_in": session_data["expires_in"],
        "user": user,
    }


@router.post(
    "/super-admin-login",
    response_model=LoginResponse,
)
def super_admin_login(
    payload: SuperAdminLoginRequest,
    request: Request,
):
    """Dedicated, company-free entry point for the Super Admin portal —
    separate from the normal per-company /login form on purpose, so a super
    admin never has to type an arbitrary workspace code just to reach
    platform-wide tools. Rejects any account that isn't is_super_admin=1,
    even with a correct password."""
    user = auth_service.authenticate_super_admin(
        email=payload.email,
        password=payload.password,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
        )

    if auth_service.user_has_2fa(user["id"]):
        pending_token = auth_service.create_pending_2fa_token(
            user_id=user["id"],
            company_id=user.get("active_company_id"),
        )
        return {
            "twofa_required": True,
            "pending_token": pending_token,
        }

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    session_data = auth_service.create_session(
        user_id=user["id"],
        ip_address=ip_address,
        user_agent=user_agent,
        company_id=user.get("active_company_id"),
    )

    return {
        "access_token": session_data["access_token"],
        "token_type": "bearer",
        "expires_in": session_data["expires_in"],
        "user": user,
    }


@router.post(
    "/2fa/verify",
    response_model=LoginResponse,
)
def verify_two_factor(
    payload: TwoFactorVerifyRequest,
    request: Request,
):
    pending = auth_service.verify_pending_2fa_token(payload.pending_token)

    if not pending:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your verification session expired. Please sign in again.",
        )

    if not auth_service.verify_totp_code(pending["user_id"], payload.code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication code.",
        )

    user = auth_service.build_login_user(
        pending["user_id"], pending["company_id"]
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is no longer available.",
        )

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    session_data = auth_service.create_session(
        user_id=user["id"],
        ip_address=ip_address,
        user_agent=user_agent,
        company_id=user.get("active_company_id"),
    )

    return {
        "access_token": session_data["access_token"],
        "token_type": "bearer",
        "expires_in": session_data["expires_in"],
        "user": user,
    }


@router.get("/2fa/status")
def two_factor_status(
    current_user: dict = Depends(get_current_user),
):
    return {
        "enabled": auth_service.user_has_2fa(current_user["id"]),
    }


@router.post("/2fa/enroll/start")
def two_factor_enroll_start(
    current_user: dict = Depends(get_current_user),
):
    result = auth_service.begin_totp_enrollment(current_user["id"])
    return {
        "secret": result["secret"],
        "otpauth_uri": result["otpauth_uri"],
    }


@router.post("/2fa/enroll/confirm")
def two_factor_enroll_confirm(
    payload: TwoFactorEnrollConfirmRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        auth_service.confirm_totp_enrollment(current_user["id"], payload.code)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        )
    return {"success": True, "enabled": True}


@router.post("/2fa/disable")
def two_factor_disable(
    payload: TwoFactorDisableRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        auth_service.disable_totp(
            current_user["id"], payload.password, payload.code
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        )
    return {"success": True, "enabled": False}


@router.get(
    "/me",
    response_model=CurrentUserResponse,
)
def get_me(
    current_user: dict = Depends(
        get_current_user
    ),
):
    companies = auth_service.get_user_companies(
        current_user["id"]
    )

    safe_user = dict(current_user)
    safe_user.pop(
        "_raw_token",
        None,
    )

    return {
        "user": safe_user,
        "companies": companies,
    }


@router.post(
    "/logout",
    response_model=LogoutResponse,
)
def logout(
    current_user: dict = Depends(
        get_current_user
    ),
):
    raw_token = current_user.get(
        "_raw_token"
    )

    if raw_token:
        auth_service.revoke_token(
            raw_token
        )

    return {
        "success": True,
        "message": "Logged out successfully.",
    }