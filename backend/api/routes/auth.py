import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from backend.api import session_cookies
from backend.api.schemas.platform import TotpConfirmRequest
from backend.api.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
    PasswordForgotRequest,
    PasswordResetRequest,
)
from backend.services import mailer
from backend.services.activity_service import Action, activity_service
from backend.services.totp_service import TotpError, totp_service
from backend.services.auth_service import (
    auth_service,
    client_ip,
    get_current_user,
    get_user_changing_password,
)
from config.settings import config


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# One message for every failure. Telling the caller which of the four
# credentials was wrong would let them enumerate companies, codes and emails.
INVALID_CREDENTIALS = "Company, email or password is incorrect."

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


def _record_auth_failure(
    *,
    email: str,
    ip_address: str | None,
    action: str,
    summary: str,
    user_id: int | None = None,
    severity: str = "notice",
) -> None:
    """File a refused sign-in.

    Where it goes depends on whether the account is known. A lock names a real
    user, so their company's own log gets it — an owner learning about a locked
    employee from a phone call is an owner who thinks the platform is down.

    A plain refusal is *not* attributed. Looking the email up to find a company
    would take a different amount of time depending on whether the account
    exists, which is a timing oracle for enumerating employees on the one
    endpoint an attacker is already pointed at. `authenticate` runs a dummy
    password check to avoid exactly that; spending it back to write a tidier log
    entry would be a poor trade. It goes to the control plane unattributed,
    where the shape of the attack across the platform is what matters anyway.
    """
    if user_id is None:
        activity_service.record_unattributed(
            action=action, summary=summary, ip_address=ip_address
        )

        return

    companies = auth_service.get_user_companies(user_id)
    company_id = companies[0]["id"] if companies else None

    if company_id is None:
        activity_service.record_unattributed(
            action=action, summary=summary, ip_address=ip_address
        )

        return

    activity_service.record(
        company_id=int(company_id),
        action=action,
        category="auth",
        kind="security",
        actor_user_id=user_id,
        actor_label=email,
        summary=summary,
        severity=severity,
        ip_address=ip_address,
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response):
    ip_address = client_ip(request)
    email = str(payload.email)

    gate = auth_service.login_gate(email=email, ip_address=ip_address)

    if gate:
        raise _refused(gate, ip_address)

    user = auth_service.authenticate(
        company=payload.company,
        email=email,
        password=payload.password,
        ip_address=ip_address,
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

            # The owner is told their employee's account was locked, in their
            # own log, at the moment it happens. A lock the owner learns about
            # from the employee's phone call is a lock that looks like an
            # outage.
            _record_auth_failure(
                email=email,
                ip_address=ip_address,
                action=Action.ACCOUNT_LOCKED,
                summary="Account locked after repeated failed sign-ins",
                user_id=lock.get("user_id"),
                severity="warning",
            )
        else:
            _record_auth_failure(
                email=email,
                ip_address=ip_address,
                action=Action.SIGN_IN_FAILED,
                summary="A sign-in was refused",
                severity="notice",
            )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
        )

    # The second factor, when this employee has chosen to have one.
    #
    # Optional on a company account and mandatory only for a platform
    # administrator: the platform decides what protects the platform, and the
    # company's owner decides what protects the company. An owner can see who
    # on their team has it on and require it of them by policy.
    #
    # A failed code counts toward the same lockout as a failed password. Not
    # counting it would leave an attacker who already has the password an
    # unlimited number of guesses at six digits.
    if bool(user.get("totp_enabled")):
        if not totp_service.verify(int(user["id"]), payload.totp_code or ""):
            auth_service.record_login_attempt(
                email=email,
                ip_address=ip_address,
                succeeded=False,
                failure_reason="invalid_totp",
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

    company_id = int(user["active_company_id"])

    # A sign-in belongs in the company's own log — an owner should be able to
    # see who accessed their workspace and from where — and is mirrored to the
    # control plane, because an attack spread across a thousand companies is
    # invisible in any single one of their logs.
    activity_service.record(
        company_id=company_id,
        action=Action.SIGNED_IN,
        category="auth",
        kind="security",
        actor_user_id=user["id"],
        actor_label=user.get("full_name") or user.get("email"),
        summary="Signed in",
        ip_address=ip_address,
    )

    session_data = auth_service.create_session(
        user_id=user["id"],
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent"),
        company_id=company_id,
    )

    # The token goes into an httpOnly cookie as well as the body. `localStorage`
    # is readable by any script on the page, so one XSS hole anywhere — in a
    # dependency, in a rendered customer name — hands an attacker a session that
    # outlives the tab they stole it from. A cookie the script cannot read turns
    # that into an attack that ends when the page closes.
    #
    # Still returned in the body, because removing it would break the CLI, the
    # tests and any integration a customer has built. The cookie is an
    # additional path, not a replacement.
    csrf_token = session_cookies.attach(
        response,
        request,
        token=session_data["access_token"],
        expires_in=session_data["expires_in"],
    )

    return {
        "access_token": session_data["access_token"],
        "token_type": "bearer",
        "expires_in": session_data["expires_in"],
        "user": user,
        "permissions": auth_service.user_permission_codes(user["id"], company_id),
        # Also in the body so a client does not have to parse cookies to find
        # it. The cookie copy is what makes the double-submit comparison work.
        "csrf_token": csrf_token,
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
def logout(response: Response, current_user: dict = Depends(get_current_user)):
    raw_token = current_user.get("_raw_token")

    if raw_token:
        auth_service.revoke_token(raw_token)

    # Revoked server-side *and* removed from the browser. Either alone leaves a
    # half-signed-out state: a live cookie for a dead session means every
    # request 401s with no way for the user to see why, and a revoked token
    # still in the jar is a credential nobody meant to keep.
    session_cookies.clear(response)

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


@router.post("/password/forgot", response_model=PasswordChangeResponse)
def forgot_password(payload: PasswordForgotRequest, request: Request):
    """Ask for a password reset link by email. Unauthenticated by design.

    The answer is identical whether or not the address matches an account: a
    difference would turn this into a directory of who has one. The reset link
    is single-use and short-lived, and any earlier unused link for the account
    is spent the moment a new one is issued.
    """
    ip_address = client_ip(request)

    generic = PasswordChangeResponse(
        success=True,
        message="If that email is registered, a reset link is on its way.",
    )

    user = auth_service.user_for_password_reset(str(payload.email))

    if not user:
        return generic

    if not mailer.is_configured():
        # Never told to the caller (it would confirm the account exists and
        # reveal a server-side gap); recorded so an operator can see why links
        # are not arriving.
        logger.warning(
            "Password reset requested for a real account but email delivery is "
            "not configured; no link was sent."
        )
        return generic

    token = auth_service.create_password_reset(
        user_id=user["id"], ip_address=ip_address
    )
    link = f"{config.APP_PUBLIC_URL.rstrip('/')}/reset-password/{token}"
    minutes = config.PASSWORD_RESET_TTL_MINUTES

    mailer.send(
        to=user["email"],
        subject="Reset your T-ZONE password",
        body=(
            f"Hello {user['full_name'] or ''},\n\n"
            "We received a request to reset your password. Open this link to "
            "choose a new one:\n\n"
            f"  {link}\n\n"
            f"The link works once and expires in {minutes} minutes.\n\n"
            "If you did not ask for this, ignore this email — your password "
            "stays the same.\n"
        ),
    )

    return generic


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


# ----------------------------------------------------------------------
# Two-factor authentication
#
# Optional on a company account, and the employee's own decision. These routes
# act only on the caller's own record — a user id is never taken from a
# parameter, so an administrator cannot enrol or disable somebody else's second
# factor, which would defeat the point of it being a factor only they hold.
# ----------------------------------------------------------------------


@router.get("/totp")
def totp_status(current_user: dict = Depends(get_current_user)):
    return totp_service.status(int(current_user["id"]))


@router.post("/totp/begin")
def totp_begin(request: Request, current_user: dict = Depends(get_current_user)):
    """Issue a secret and the QR that carries it.

    The only response that ever contains the secret. Starting again issues a new
    one and discards the old, so an abandoned attempt cannot be resumed by
    somebody who photographed the first QR.
    """
    try:
        return totp_service.begin_enrolment(int(current_user["id"]))
    except TotpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/totp/confirm")
def totp_confirm(
    payload: TotpConfirmRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Turn it on, and return the recovery codes once.

    They are stored hashed, so this response is the only moment they exist in
    readable form anywhere.
    """
    try:
        result = totp_service.confirm_enrolment(int(current_user["id"]), payload.code)
    except TotpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    company_id = current_user.get("active_company_id")

    if company_id is not None:
        activity_service.record(
            company_id=int(company_id),
            action=Action.PASSWORD_CHANGED,
            category="auth",
            kind="security",
            actor_user_id=int(current_user["id"]),
            actor_label=current_user.get("full_name") or current_user.get("email"),
            summary="Turned on two-factor authentication",
            ip_address=client_ip(request),
        )

    return result


@router.delete("/totp")
def totp_disable(
    payload: TotpConfirmRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Turn it off, after proving the caller still holds the second factor.

    A code is required. Without it, anyone who walked up to an unlocked screen
    could remove the protection with one click — which would make the second
    factor only as strong as the session, and the session is what it exists to
    defend.
    """
    if not totp_service.verify(int(current_user["id"]), payload.code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Enter a current code, or one of your recovery codes.",
        )

    try:
        totp_service.disable(int(current_user["id"]))
    except TotpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    company_id = current_user.get("active_company_id")

    if company_id is not None:
        activity_service.record(
            company_id=int(company_id),
            action=Action.PASSWORD_CHANGED,
            category="auth",
            kind="security",
            actor_user_id=int(current_user["id"]),
            actor_label=current_user.get("full_name") or current_user.get("email"),
            summary="Turned off two-factor authentication",
            severity="warning",
            ip_address=client_ip(request),
        )

    return {"success": True, "enabled": False}
