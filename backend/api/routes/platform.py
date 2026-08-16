"""The Super Admin control-plane API.

Every route here is guarded by ``get_platform_admin``, which requires a token
minted in the platform scope belonging to a user who is still a super admin. The
customer dependencies — ``get_current_user`` and ``require_permission`` — are
never used in this file: a company token must not administer the platform, and a
platform token must not read a company. The two are separate credentials on
purpose.

The single exception is ``POST /api/platform/auth/login``, which is how a
platform session comes into existence and therefore cannot require one.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.schemas.platform import (
    CompanyCreateRequest,
    CompanyStatusRequest,
    PlanAssignRequest,
    PlatformConfigUpdate,
    PlatformLoginRequest,
    PlatformLoginResponse,
    PlatformLogoutResponse,
    PlatformUserResponse,
)
from backend.services.auth_service import (
    PLATFORM_SCOPE,
    auth_service,
    client_ip,
    get_platform_admin,
)
from backend.services.platform_service import (
    PlatformConflict,
    PlatformError,
    PlatformNotFound,
    platform_service,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/platform", tags=["Platform Administration"])


# One message for every failure, exactly as the company login does it. An
# unknown email, a wrong password and an account that exists but is not a
# platform administrator all answer identically — otherwise this endpoint would
# tell an attacker which accounts to spend their time on.
INVALID_CREDENTIALS = "Email or password is incorrect."

TOO_MANY_ATTEMPTS = (
    "Too many failed attempts. Wait a few minutes before trying again."
)


def _actor(current_user: dict[str, Any]) -> int:
    return int(current_user["id"])


def _handle(exc: PlatformError) -> HTTPException:
    """Map a service failure onto the status code it deserves."""
    if isinstance(exc, PlatformNotFound):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, PlatformConflict):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST

    return HTTPException(status_code=code, detail=str(exc))


# ----------------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------------


@router.post("/auth/login", response_model=PlatformLoginResponse)
def platform_login(payload: PlatformLoginRequest, request: Request):
    """Mint a platform session. The only route here without a token.

    Rate limited through the same counters as the company login, so failures on
    either door count against the same email and address rather than giving an
    attacker two independent budgets.
    """
    ip_address = client_ip(request)
    email = str(payload.email)

    if auth_service.is_login_blocked(email=email, ip_address=ip_address):
        logger.warning("Platform login blocked by rate limit for %s", ip_address)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=TOO_MANY_ATTEMPTS,
        )

    user = auth_service.authenticate_platform(email=email, password=payload.password)

    if not user:
        auth_service.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="platform_invalid_credentials",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    auth_service.record_login_attempt(
        email=email, ip_address=ip_address, succeeded=True
    )
    auth_service.clear_login_attempts(email)

    session_data = auth_service.create_session(
        user_id=user["id"],
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent"),
        # No company. A platform session has no company by construction, which
        # is what stops it ever opening a tenant database.
        company_id=None,
        scope=PLATFORM_SCOPE,
    )

    platform_service.record_audit(
        action="platform.signed_in",
        actor_user_id=int(user["id"]),
        target_type="user",
        target_id=int(user["id"]),
        ip_address=ip_address,
    )

    return {
        "access_token": session_data["access_token"],
        "token_type": "bearer",
        "scope": session_data["scope"],
        "expires_in": session_data["expires_in"],
        "user": user,
    }


@router.get("/auth/me", response_model=PlatformUserResponse)
def platform_me(current_user: dict[str, Any] = Depends(get_platform_admin)):
    safe_user = dict(current_user)
    safe_user.pop("_raw_token", None)

    return {"user": safe_user, "scope": PLATFORM_SCOPE}


@router.post("/auth/logout", response_model=PlatformLogoutResponse)
def platform_logout(current_user: dict[str, Any] = Depends(get_platform_admin)):
    raw_token = current_user.get("_raw_token")

    if raw_token:
        auth_service.revoke_token(raw_token)

    return {"success": True, "message": "Signed out of the platform console."}


# ----------------------------------------------------------------------
# Companies
# ----------------------------------------------------------------------


@router.get("/companies")
def list_companies(current_user: dict[str, Any] = Depends(get_platform_admin)):
    return {"items": platform_service.list_companies()}


@router.post("/companies", status_code=status.HTTP_201_CREATED)
def create_company(
    payload: CompanyCreateRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """Provision a company and hand back its workspace code once.

    The code is in this response and nowhere else, ever again.
    """
    try:
        created = platform_service.create_company(
            name=payload.name,
            slug=payload.slug,
            workspace=payload.workspace,
            owner_email=str(payload.owner_email),
            owner_name=payload.owner_name,
            owner_password=payload.owner_password,
            country=payload.country,
            currency=payload.currency,
            timezone_name=payload.timezone,
            language=payload.language,
            plan_code=payload.plan_code,
            actor_user_id=_actor(current_user),
            ip_address=client_ip(request),
        )
    except PlatformError as exc:
        raise _handle(exc) from exc
    except Exception as exc:  # noqa: BLE001 - provisioning touches the filesystem
        logger.exception("Company creation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Company creation failed and was rolled back: {exc}",
        ) from exc

    return created


@router.get("/companies/{company_id}")
def company_detail(
    company_id: int,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """One company's control-plane record, config and row counts.

    The statistics in this response are counts and a file size. There is no
    endpoint anywhere in this router that returns a company's rows.
    """
    try:
        return platform_service.company_detail(company_id)
    except PlatformError as exc:
        raise _handle(exc) from exc


@router.post("/companies/{company_id}/status")
def set_company_status(
    company_id: int,
    payload: CompanyStatusRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    try:
        return platform_service.set_company_status(
            company_id,
            payload.status,
            actor_user_id=_actor(current_user),
            ip_address=client_ip(request),
            reason=payload.reason,
        )
    except PlatformError as exc:
        raise _handle(exc) from exc


@router.post("/companies/{company_id}/workspace-code/rotate")
def rotate_workspace_code(
    company_id: int,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    try:
        return platform_service.rotate_workspace_code(
            company_id,
            actor_user_id=_actor(current_user),
            ip_address=client_ip(request),
        )
    except PlatformError as exc:
        raise _handle(exc) from exc


@router.post("/companies/{company_id}/plan")
def assign_plan(
    company_id: int,
    payload: PlanAssignRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    try:
        return platform_service.assign_plan(
            company_id=company_id,
            plan_code=payload.plan_code,
            expires_at=payload.expires_at,
            actor_user_id=_actor(current_user),
            ip_address=client_ip(request),
        )
    except PlatformError as exc:
        raise _handle(exc) from exc


@router.get("/companies/{company_id}/config")
def get_platform_config(
    company_id: int,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    try:
        return platform_service.get_platform_config(company_id)
    except PlatformError as exc:
        raise _handle(exc) from exc


@router.put("/companies/{company_id}/config")
def update_platform_config(
    company_id: int,
    payload: PlatformConfigUpdate,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """Module activation, brand tokens and layout flags for one company.

    An unknown key is a 400, not a silently stored typo.
    """
    try:
        return platform_service.update_platform_config(
            company_id,
            modules=payload.modules,
            branding=payload.branding,
            layout=payload.layout,
            actor_user_id=_actor(current_user),
            ip_address=client_ip(request),
        )
    except PlatformError as exc:
        raise _handle(exc) from exc


# ----------------------------------------------------------------------
# Plans
# ----------------------------------------------------------------------


@router.get("/plans")
def list_plans(current_user: dict[str, Any] = Depends(get_platform_admin)):
    return {"items": platform_service.list_plans()}


# ----------------------------------------------------------------------
# Platform administrators
# ----------------------------------------------------------------------


@router.get("/users")
def search_users(
    search: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """Look up an account so granting administrator rights needs no guessed id."""
    return {"items": platform_service.search_users(search=search, limit=limit)}


@router.get("/admins")
def list_platform_admins(current_user: dict[str, Any] = Depends(get_platform_admin)):
    return {"items": platform_service.list_platform_admins()}


@router.post("/admins/{user_id}/grant")
def grant_platform_admin(
    user_id: int,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    try:
        return platform_service.grant_platform_admin(
            user_id,
            actor_user_id=_actor(current_user),
            ip_address=client_ip(request),
        )
    except PlatformError as exc:
        raise _handle(exc) from exc


@router.post("/admins/{user_id}/revoke")
def revoke_platform_admin(
    user_id: int,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """Refused when it would leave the platform with no administrator."""
    try:
        return platform_service.revoke_platform_admin(
            user_id,
            actor_user_id=_actor(current_user),
            ip_address=client_ip(request),
        )
    except PlatformError as exc:
        raise _handle(exc) from exc


# ----------------------------------------------------------------------
# Health and audit
# ----------------------------------------------------------------------


@router.get("/health")
def platform_health(current_user: dict[str, Any] = Depends(get_platform_admin)):
    return platform_service.platform_health()


@router.get("/audit")
def list_audit(
    company_id: int | None = Query(default=None),
    action: str | None = Query(default=None, max_length=80),
    actor_user_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    return platform_service.list_audit(
        company_id=company_id,
        action=action,
        actor_user_id=actor_user_id,
        limit=limit,
        offset=offset,
    )
