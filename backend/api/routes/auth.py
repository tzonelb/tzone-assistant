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

    permissions = auth_service.get_permission_codes(
        current_user["id"],
        current_user.get("active_company_id"),
        bool(current_user.get("is_super_admin")),
    )

    return {
        "user": safe_user,
        "companies": companies,
        "permissions": permissions,
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