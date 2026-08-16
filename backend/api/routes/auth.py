import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
    PasswordResetRequest,
)
from backend.services.auth_service import (
    auth_service,
    client_ip,
    get_current_user,
    get_user_changing_password,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# One message for every failure. Telling the caller which of the four
# credentials was wrong would let them enumerate companies, codes and emails.
INVALID_CREDENTIALS = "Workspace code, company, email or password is incorrect."

# A locked account IS told it is locked, and that is a deliberate exception to
# the rule above. The anti-enumeration argument does not apply: reaching this
# state takes five failed attempts against a real address, so anyone who sees it
# has already established the account exists. Withholding it would only mislead
# the employee — who needs to know that waiting will not help and that their
# administrator can send them a reset link.
ACCOUNT_LOCKED = (
    "This account is locked after too many failed attempts. "
    "Ask an administrator at your company to send you a password reset link, "
    "which unlocks it immediately."
)

ADDRESS_BLOCKED = (
    "Too many failed attempts from this connection. Wait and try again."
)


def _refused(gate: dict, ip_address: str | None) -> HTTPException:
    """Turn a refusal from `login_gate` into the response for it."""
    if gate["kind"] == "account_locked":
        detail = ACCOUNT_LOCKED
    else:
        detail = ADDRESS_BLOCKED
        logger.warning("Login blocked by address throttle for %s", ip_address)

    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(int(gate["retry_after_seconds"]))},
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request):
    ip_address = client_ip(request)
    email = str(payload.email)

    gate = auth_service.login_gate(email=email, ip_address=ip_address)

    if gate:
        raise _refused(gate, ip_address)

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

        lock = auth_service.register_failure(email=email, ip_address=ip_address)

        if lock:
            logger.warning(
                "Account locked after repeated failures: user id=%s from %s",
                lock["user_id"],
                ip_address,
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
def get_me(current_user: dict = Depends(get_user_changing_password)):
    """Who am I — reachable even while a password change is being forced.

    The second route to use the permissive dependency, and for the same
    reason as the first: an employee who must change their password needs the
    change screen, and the interface cannot route them to it without being able
    to ask who they are. Blocking this made `must_change_password` the one fact
    the client could not read, so it had to be inferred from the shape of a 403.

    It publishes nothing extra — `sanitize_user` decides that, and it is the
    caller's own record either way.
    """
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


# ----------------------------------------------------------------------
# Passwords
# ----------------------------------------------------------------------


@router.post("/password", response_model=PasswordChangeResponse)
def change_password(
    payload: PasswordChangeRequest,
    current_user: dict = Depends(get_user_changing_password),
):
    """Change your own password.

    Uses `get_user_changing_password` rather than `get_current_user`: an
    employee whose administrator forced a reset is refused by every other route
    until they get here, so this one route must remain reachable.

    Succeeding ends every session, including the one that made this request.
    That is deliberate — a password is changed because it may be known to
    somebody else, and leaving their session alive would make the change
    cosmetic for the rest of the day.
    """
    changed = auth_service.change_own_password(
        user_id=int(current_user["id"]),
        current_password=payload.current_password,
        new_password=payload.new_password,
    )

    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The current password is incorrect.",
        )

    return {
        "success": True,
        "message": "Password changed. Sign in again with the new one.",
    }


@router.post("/password/reset/{token}", response_model=PasswordChangeResponse)
def reset_password(token: str, payload: PasswordResetRequest, request: Request):
    """Spend a reset link and set a new password.

    Unauthenticated by design — the whole point is that the person cannot sign
    in. The token is the credential, it is single-use, and it expires.

    One message for a token that is unknown, spent or expired. Distinguishing
    them would let somebody with a stale link learn whether it was used, which
    tells them something about the account they should not be told.
    """
    ip_address = client_ip(request)

    if not auth_service.consume_password_reset(
        token=token, new_password=payload.new_password
    ):
        logger.warning("Rejected password reset token from %s", ip_address)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This reset link is no longer valid. Ask an administrator at "
                "your company to send a new one."
            ),
        )

    return {
        "success": True,
        "message": "Password set. You can sign in now.",
    }
