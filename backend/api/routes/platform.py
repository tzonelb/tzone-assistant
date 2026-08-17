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
    PlanCreateRequest,
    PlanOverrideRequest,
    PlanUpdateRequest,
    PlatformConfigUpdate,
    PlatformLoginRequest,
    PlatformLoginResponse,
    PlatformLogoutResponse,
    PlatformUserResponse,
    TotpConfirmRequest,
)
from backend.services.auth_service import (
    PLATFORM_SCOPE,
    auth_service,
    client_ip,
    get_platform_admin,
    get_platform_admin_enrolling,
)
from backend.services.plan_service import LIMIT_KEYS, plan_service
from backend.services.health_service import health_service
from backend.services.totp_service import TotpError, totp_service
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

# There is nobody above a platform administrator to unlock them, so this
# message names the only way back in rather than leaving them to guess.
LOCKED_ADMIN = (
    "This account is locked after too many failed attempts. A platform "
    "administrator account can only be recovered from the server: "
    "python -m tools.manage_platform unlock-user --email <address>"
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

    gate = auth_service.login_gate(email=email, ip_address=ip_address)

    if gate:
        logger.warning(
            "Platform login refused (%s) for %s", gate["kind"], ip_address
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                LOCKED_ADMIN if gate["kind"] == "account_locked" else TOO_MANY_ATTEMPTS
            ),
            headers={"Retry-After": str(int(gate["retry_after_seconds"]))},
        )

    user = auth_service.authenticate_platform(email=email, password=payload.password)

    if not user:
        auth_service.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="platform_invalid_credentials",
        )
        auth_service.register_failure(email=email, ip_address=ip_address)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    # The second factor, before the session exists.
    #
    # This sign-in is one factor by design: a platform administrator belongs to
    # no company, so there is no workspace code to type. It is also the account
    # that suspends companies, rotates workspace codes and reads the platform
    # audit — one guessed or reused password is the whole platform. So the
    # second factor is required here, and enrolling is not optional.
    #
    # A failed code is recorded as a failed sign-in and counts toward the same
    # lockout. Not doing so would leave an attacker who already has the password
    # an unlimited number of guesses at six digits.
    if bool(user.get("totp_enabled")):
        if not totp_service.verify(int(user["id"]), payload.totp_code or ""):
            auth_service.record_login_attempt(
                email=email,
                ip_address=ip_address,
                succeeded=False,
                failure_reason="platform_invalid_totp",
            )
            auth_service.register_failure(email=email, ip_address=ip_address)

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "totp_required",
                    "message": "Enter the code from your authenticator app.",
                },
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
        # An administrator who has not enrolled gets a session and nothing else:
        # `get_platform_admin` refuses every other route until they do. The flag
        # is here so the console can send them to enrolment rather than to a
        # dashboard that will 403 on every request it makes.
        "totp": totp_service.status(int(user["id"])),
    }


# ----------------------------------------------------------------------
# Two-factor authentication
#
# These three routes use `get_platform_admin_enrolling`, the one dependency
# that does not demand an enrolled second factor. Everything else in this file
# refuses until enrolment is finished, and a requirement with no reachable way
# to satisfy it is a locked door — on the account that has nobody above it to
# open one.
# ----------------------------------------------------------------------


@router.get("/auth/totp")
def platform_totp_status(
    current_user: dict[str, Any] = Depends(get_platform_admin_enrolling),
):
    return totp_service.status(int(current_user["id"]))


@router.post("/auth/totp/begin")
def platform_totp_begin(
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin_enrolling),
):
    """Issue a secret and the QR that carries it.

    The only response on the platform that ever contains the secret. Starting
    again issues a new one and discards the old, so an abandoned attempt cannot
    be resumed by somebody who photographed the first QR.
    """
    try:
        result = totp_service.begin_enrolment(int(current_user["id"]))
    except TotpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    platform_service.record_audit(
        action="platform.totp_enrolment_started",
        actor_user_id=_actor(current_user),
        target_type="user",
        target_id=_actor(current_user),
        ip_address=client_ip(request),
    )

    return result


@router.post("/auth/totp/confirm")
def platform_totp_confirm(
    payload: TotpConfirmRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin_enrolling),
):
    """Prove the app produces codes from the secret, and turn it on.

    Returns the recovery codes once. They are stored hashed, so this response
    is the only moment they exist in readable form anywhere.
    """
    try:
        result = totp_service.confirm_enrolment(
            int(current_user["id"]), payload.code
        )
    except TotpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    platform_service.record_audit(
        action="platform.totp_enabled",
        actor_user_id=_actor(current_user),
        target_type="user",
        target_id=_actor(current_user),
        ip_address=client_ip(request),
    )

    return result


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
    plans = platform_service.list_plans()

    # How many companies each plan carries, so an operator editing a ceiling
    # can see how many businesses the edit moves before making it.
    for plan in plans:
        plan["companies"] = platform_service.plan_usage(plan["code"])

    return {"items": plans, "limit_keys": list(LIMIT_KEYS)}


@router.post("/plans", status_code=status.HTTP_201_CREATED)
def create_plan(
    payload: PlanCreateRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """Add a plan.

    Until now the three seeded plans were the only ones there could ever be:
    they were written with `INSERT OR IGNORE` at first boot and no endpoint
    created or changed one, so the commercial offer was frozen at whatever
    shipped.
    """
    try:
        return platform_service.create_plan(
            code=payload.code,
            values={**payload.values, "name": payload.name},
            actor_user_id=_actor(current_user),
            ip_address=client_ip(request),
        )
    except PlatformError as exc:
        raise _handle(exc) from exc


@router.patch("/plans/{code}")
def update_plan(
    code: str,
    payload: PlanUpdateRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """Change a plan's numbers. Every company on it moves with it.

    That is intended, and it is also why per-company overrides exist:
    accommodating one customer by editing the plan raises the ceiling for
    everybody on it and records nothing about why.
    """
    try:
        return platform_service.update_plan(
            code=code,
            values=payload.values,
            actor_user_id=_actor(current_user),
            ip_address=client_ip(request),
        )
    except PlatformError as exc:
        raise _handle(exc) from exc


# ----------------------------------------------------------------------
# Per-company allowances and usage
# ----------------------------------------------------------------------


@router.get("/companies/{company_id}/limits")
def company_limits(
    company_id: int,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """The effective allowance for each limit, and where each one came from."""
    try:
        overrides = plan_service.overrides(company_id)
        subscription = plan_service.subscription(company_id)

        return {
            "company_id": company_id,
            "limits": plan_service.limits(company_id),
            "overrides": overrides,
            "features": plan_service.features(company_id),
            "plan_code": (subscription or {}).get("plan_code"),
            "subscription_active": plan_service.is_active(subscription),
            "sources": {
                key: ("override" if key in overrides else "plan")
                for key in LIMIT_KEYS
            },
        }
    except PlatformError as exc:
        raise _handle(exc) from exc


@router.put("/companies/{company_id}/limits/{limit_key}")
def set_company_limit(
    company_id: int,
    limit_key: str,
    payload: PlanOverrideRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    try:
        limits = plan_service.set_override(
            company_id=company_id,
            limit_key=limit_key,
            value=payload.value,
            note=payload.note,
            actor_user_id=_actor(current_user),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    platform_service.record_audit(
        action="company.limit_override_set",
        actor_user_id=_actor(current_user),
        company_id=company_id,
        target_type="company",
        target_id=company_id,
        data={"limit_key": limit_key, "value": payload.value, "note": payload.note},
        ip_address=client_ip(request),
    )

    return {"company_id": company_id, "limits": limits}


@router.delete("/companies/{company_id}/limits/{limit_key}")
def clear_company_limit(
    company_id: int,
    limit_key: str,
    request: Request,
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """Put this company back on its plan for one allowance."""
    limits = plan_service.clear_override(
        company_id=company_id, limit_key=limit_key
    )

    platform_service.record_audit(
        action="company.limit_override_cleared",
        actor_user_id=_actor(current_user),
        company_id=company_id,
        target_type="company",
        target_id=company_id,
        data={"limit_key": limit_key},
        ip_address=client_ip(request),
    )

    return {"company_id": company_id, "limits": limits}


@router.get("/companies/{company_id}/usage")
def company_usage(
    company_id: int,
    period: str | None = Query(default=None, max_length=7),
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """What this company used in a month. Numbers only, never content."""
    from backend.services.plan_service import current_period

    resolved = period or current_period()

    return {
        "company_id": company_id,
        "period": resolved,
        "breakdown": plan_service.usage_breakdown(
            company_id=company_id, period=resolved
        ),
        "ai_replies": plan_service.usage_total(
            company_id=company_id, metric="ai_replies", period=resolved
        ),
        "limits": plan_service.limits(company_id),
    }


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


@router.get("/health/report")
def platform_health_report(
    deep: bool = Query(
        default=False,
        description=(
            "Run SQLite's integrity check over every company database. It "
            "reads every page, so it is slow on a large platform — the "
            "background self-check already does this every fifteen minutes and "
            "its result is on /health/last."
        ),
    ),
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """Can the platform serve, right now, and prove it.

    The master key, the control database, every company database and the disk —
    plus the host's load, memory and uptime. `/health` on the public router
    stays a constant on purpose: a liveness probe that checks dependencies
    restarts the process when a database is slow, which is when restarting
    helps least.
    """
    return health_service.report(deep=deep)


@router.get("/health/last")
def platform_health_last(
    current_user: dict[str, Any] = Depends(get_platform_admin),
):
    """The most recent background sweep, without running another.

    What a dashboard should poll. The deep check reads every page of every
    company database, and a screen that re-ran it on each refresh would be its
    own load problem.
    """
    report = health_service.last_report()

    if report is None:
        return {
            "status": "pending",
            "detail": (
                "No self-check has completed yet. The first runs within seconds "
                "of startup; call /health/report to check now."
            ),
        }

    return report


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
