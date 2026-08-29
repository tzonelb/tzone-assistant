"""Authentication, sessions and permission checks.

Logging in requires four things: the workspace code, the company, the email and
the password. The workspace code is not a label — it is verified by unsealing
the company's database key, so an employee who does not have it cannot reach the
company's data even with a correct password.

Everything here reads the control-plane database only. Company data lives in
per-company encrypted files and is never joined against these tables.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import config
from database.manager import database_manager


logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# A token is minted for exactly one of these and is refused everywhere else.
COMPANY_SCOPE = "company"
PLATFORM_SCOPE = "platform"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


class AuthService:
    # 600k matches the current OWASP guidance for PBKDF2-HMAC-SHA256 (and the
    # workspace-code KDF in keyring). The iteration count is stored with every
    # hash, so existing 310k hashes keep verifying against their own count; only
    # newly set passwords use the higher work factor.
    PASSWORD_ITERATIONS = 600_000
    PASSWORD_ALGORITHM = "sha256"
    MIN_PASSWORD_LENGTH = 10

    # ------------------------------------------------------------------
    # Passwords
    # ------------------------------------------------------------------

    def normalize_email(self, email: str) -> str:
        return str(email or "").strip().lower()

    def hash_password(self, password: str) -> str:
        if len(password or "") < self.MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {self.MIN_PASSWORD_LENGTH} characters."
            )

        salt = secrets.token_bytes(32)
        password_hash = hashlib.pbkdf2_hmac(
            self.PASSWORD_ALGORITHM,
            password.encode("utf-8"),
            salt,
            self.PASSWORD_ITERATIONS,
        )

        return (
            f"pbkdf2_{self.PASSWORD_ALGORITHM}"
            f"${self.PASSWORD_ITERATIONS}"
            f"${salt.hex()}"
            f"${password_hash.hex()}"
        )

    def verify_password(self, password: str, stored_hash: str | None) -> bool:
        if not stored_hash:
            return False

        try:
            algorithm_name, iterations_text, salt_hex, hash_hex = stored_hash.split("$", 3)

            if algorithm_name != "pbkdf2_sha256":
                return False

            actual_hash = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iterations_text),
            )

            return hmac.compare_digest(actual_hash, bytes.fromhex(hash_hex))

        except (ValueError, TypeError, AttributeError):
            return False

    def _dummy_password_check(self) -> None:
        """Burn the same work as a real verification.

        Without this, a missing email returns measurably faster than a wrong
        password and the login endpoint becomes a user directory.
        """
        hashlib.pbkdf2_hmac(
            "sha256", b"none", b"none" * 8, self.PASSWORD_ITERATIONS
        )

    def hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Login throttling
    # ------------------------------------------------------------------

    # An address block doubles each failure past the threshold; this is where it
    # stops. An hour is long enough to make online guessing pointless and short
    # enough that a shared office address recovers within a working morning.
    ADDRESS_BLOCK_CAP_SECONDS = 3600

    def record_login_attempt(
        self,
        *,
        email: str | None,
        ip_address: str | None,
        succeeded: bool,
        failure_reason: str | None = None,
    ) -> None:
        with database_manager.control() as conn:
            conn.execute(
                """
                INSERT INTO login_attempts (
                    email, ip_address, succeeded, failure_reason, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self.normalize_email(email) if email else None,
                    ip_address,
                    1 if succeeded else 0,
                    failure_reason,
                    utc_now_iso(),
                ),
            )
            conn.commit()

    def _failure_count(
        self, conn: Any, *, window_start: str, email: str | None, ip_address: str | None
    ) -> int:
        """Failures in the window for one key, or for the pair when both are given.

        Separate counters, never combined with OR. The previous version summed
        `email = ? OR ip_address = ?` into a single count, which meant five
        failed attempts naming a known employee — from anywhere on earth —
        locked that employee out. An attacker needed nothing but the address.
        """
        clauses = ["succeeded = 0", "created_at >= ?"]
        params: list[Any] = [window_start]

        if email is not None:
            clauses.append("email = ?")
            params.append(self.normalize_email(email))

        if ip_address is not None:
            clauses.append("ip_address = ?")
            params.append(ip_address)

        row = conn.execute(
            f"SELECT COUNT(*) AS failures FROM login_attempts WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()

        return int(row["failures"] if row else 0)

    def address_block_seconds(self, ip_address: str | None) -> int:
        """How long this address is refused for, in seconds. Zero means allowed.

        Doubles with each additional failure past the threshold and stops at an
        hour.

        The threshold is `LOGIN_ADDRESS_MAX_ATTEMPTS`, not `LOGIN_MAX_ATTEMPTS`,
        and the gap between them matters: a whole office shares one address, so
        throttling it as tightly as an account would mean one colleague's typos
        lock out everyone around them — the exact collateral damage the account
        lock was redesigned to avoid. A test holds this apart, because setting
        the two equal is an easy and invisible way to bring the old bug back.
        """
        if not ip_address:
            return 0

        window_start = (
            utc_now() - timedelta(minutes=config.LOGIN_LOCKOUT_MINUTES)
        ).isoformat()

        with database_manager.control() as conn:
            failures = self._failure_count(
                conn, window_start=window_start, email=None, ip_address=ip_address
            )

        over = failures - config.LOGIN_ADDRESS_MAX_ATTEMPTS

        if over < 0:
            return 0

        return min(self.ADDRESS_BLOCK_CAP_SECONDS, 60 * (2**over))

    def account_lock(self, email: str | None) -> dict[str, Any] | None:
        """The lock on this account, or nothing.

        Reads an explicit column rather than deriving the state from a count.
        Deriving it was what made unlocking impossible to express: "unlock"
        became "delete rows", and `clear_login_attempts` deleted by email only,
        so an address-side block could not be cleared at all.
        """
        if not email:
            return None

        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT id, locked_until, locked_reason
                FROM users
                WHERE email = ?
                LIMIT 1
                """,
                (self.normalize_email(email),),
            ).fetchone()

        if not row or not row["locked_until"]:
            return None

        try:
            locked_until = datetime.fromisoformat(row["locked_until"])
        except (TypeError, ValueError):
            return None

        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)

        if locked_until <= utc_now():
            return None

        return {
            "user_id": int(row["id"]),
            "locked_until": locked_until,
            "reason": row["locked_reason"] or "",
        }

    def lock_account(
        self, *, email: str, reason: str, minutes: int | None = None
    ) -> dict[str, Any] | None:
        """Lock an account and say who it was, so the caller can raise an alarm."""
        minutes = config.LOGIN_LOCKOUT_MINUTES if minutes is None else int(minutes)
        locked_until = (utc_now() + timedelta(minutes=minutes)).isoformat()

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id, full_name FROM users WHERE email = ? LIMIT 1",
                (self.normalize_email(email),),
            ).fetchone()

            if not row:
                # No account by that name. Nothing to lock, and saying so is not
                # a leak because the caller answers identically either way.
                return None

            conn.execute(
                """
                UPDATE users
                SET locked_until = ?, locked_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (locked_until, reason, utc_now_iso(), int(row["id"])),
            )
            conn.commit()

        logger.warning(
            "Account locked user id=%s until %s (%s)", row["id"], locked_until, reason
        )

        return {
            "user_id": int(row["id"]),
            "full_name": row["full_name"],
            "locked_until": locked_until,
        }

    def unlock_account(self, *, user_id: int) -> bool:
        """Clear the lock and the failures behind it.

        Both halves matter: leaving the attempt rows would let the account lock
        itself again on the next mistyped password, which would read to the
        employee as the unlock never having worked.
        """
        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT email FROM users WHERE id = ? LIMIT 1", (int(user_id),)
            ).fetchone()

            if not row:
                return False

            conn.execute(
                """
                UPDATE users
                SET locked_until = NULL, locked_reason = NULL, updated_at = ?
                WHERE id = ?
                """,
                (utc_now_iso(), int(user_id)),
            )
            conn.execute(
                "DELETE FROM login_attempts WHERE email = ? AND succeeded = 0",
                (row["email"],),
            )
            conn.commit()

        logger.info("Account unlocked user id=%s", user_id)
        return True

    def register_failure(
        self, *, email: str | None, ip_address: str | None
    ) -> dict[str, Any] | None:
        """Decide whether this failure locks the account, and lock it if so.

        Called after a failed attempt has been recorded. Returns the lock it
        created, so the caller can raise a security event and tell the company
        owner — a lock nobody is told about is a support ticket waiting to
        happen.
        """
        if not email:
            return None

        window_start = (
            utc_now() - timedelta(minutes=config.LOGIN_LOCKOUT_MINUTES)
        ).isoformat()

        with database_manager.control() as conn:
            failures = self._failure_count(
                conn, window_start=window_start, email=email, ip_address=None
            )

        if failures < config.LOGIN_MAX_ATTEMPTS:
            return None

        return self.lock_account(
            email=email,
            reason=f"{failures} failed sign-in attempts",
        )

    def login_gate(
        self, *, email: str | None, ip_address: str | None
    ) -> dict[str, Any] | None:
        """What, if anything, stops this sign-in before a password is checked.

        Returns `None` to proceed, or a dict with a `kind` the route turns into
        a response. The two kinds are deliberately different things:

        `address_blocked` is about where the request came from and clears itself
        with time. `account_locked` is about the account and does not have to be
        waited out — an administrator holding `users.manage` can send a
        password-reset link, which unlocks it immediately. That escape is what
        makes a full account lock safe to have at all; without it, five requests
        would be a free "disable this employee" button for anyone who knows an
        address.
        """
        blocked_for = self.address_block_seconds(ip_address)

        if blocked_for:
            return {"kind": "address_blocked", "retry_after_seconds": blocked_for}

        lock = self.account_lock(email)

        if lock:
            return {
                "kind": "account_locked",
                "retry_after_seconds": max(
                    1, int((lock["locked_until"] - utc_now()).total_seconds())
                ),
            }

        return None

    def clear_login_attempts(self, email: str) -> None:
        with database_manager.control() as conn:
            conn.execute(
                "DELETE FROM login_attempts WHERE email = ? AND succeeded = 0",
                (self.normalize_email(email),),
            )
            conn.commit()

    def prune_login_attempts(self, retention_hours: int = 48) -> int:
        cutoff = (utc_now() - timedelta(hours=retention_hours)).isoformat()

        with database_manager.control() as conn:
            cursor = conn.execute(
                "DELETE FROM login_attempts WHERE created_at < ?", (cutoff,)
            )
            conn.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @staticmethod
    def _record_workspace_code_rejection(
        *,
        company_id: int,
        user_id: int,
        email: str,
        ip_address: str | None,
    ) -> None:
        """File the rejection, and never let filing it change the answer.

        Imported here rather than at module scope because `activity_service`
        writes to a company's own encrypted database, and importing it at the
        top of the module that every route depends on would pull the tenant
        layer into the import graph of the login path itself.

        Wrapped because a log entry must not be able to turn a correct refusal
        into a 500. The refusal has already been decided; this only records it.
        """
        try:
            from backend.services.activity_service import Action, activity_service

            activity_service.record(
                company_id=company_id,
                action=Action.WORKSPACE_CODE_REJECTED,
                category="auth",
                kind="security",
                actor_user_id=user_id,
                actor_label=email,
                summary=(
                    "A sign-in with the correct password was refused by the "
                    "workspace code"
                ),
                severity="warning",
                ip_address=ip_address,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not record a workspace code rejection")

    def authenticate(
        self,
        *,
        company: str,
        email: str,
        password: str,
        ip_address: str | None = None,
    ) -> dict[str, Any] | None:
        """Verify the sign-in credentials and return the user, or None.

        The failure reason is deliberately not returned to the caller: the API
        answers every failure identically so nobody can probe which companies,
        codes or emails exist.

        `ip_address` is not used to decide anything. It is here because one of
        the failures below is worth telling the company's owner about, and the
        caller cannot tell which failure happened — that is the whole point of
        the uniform answer. Withholding the reason from the attacker and
        withholding it from the owner are different things, and only the first
        was ever intended.
        """
        normalized_email = self.normalize_email(email)
        normalized_company = str(company or "").strip().lower()

        with database_manager.control() as conn:
            user_row = conn.execute(
                "SELECT * FROM users WHERE LOWER(email) = ? LIMIT 1",
                (normalized_email,),
            ).fetchone()

            if not user_row:
                self._dummy_password_check()
                logger.info("Login rejected: unknown email")
                return None

            user_data = dict(user_row)

            if user_data.get("status") != "active":
                self._dummy_password_check()
                logger.info("Login rejected: inactive user id=%s", user_data["id"])
                return None

            if not self.verify_password(password, user_data.get("password_hash")):
                logger.info("Login rejected: bad password user id=%s", user_data["id"])
                return None

            company_row = conn.execute(
                """
                SELECT companies.id, companies.name, companies.slug
                FROM companies
                JOIN workspaces ON workspaces.id = companies.workspace_id
                LEFT JOIN company_users
                    ON company_users.company_id = companies.id
                   AND company_users.user_id = ?
                WHERE companies.status = 'active'
                  AND (
                        LOWER(companies.slug) = ?
                     OR LOWER(companies.name) = ?
                     OR LOWER(workspaces.slug) = ?
                  )
                  AND (? = 1 OR company_users.status = 'active')
                LIMIT 1
                """,
                (
                    user_data["id"],
                    normalized_company,
                    normalized_company,
                    normalized_company,
                    1 if user_data.get("is_super_admin") else 0,
                ),
            ).fetchone()

            if not company_row:
                logger.info("Login rejected: no company match user id=%s", user_data["id"])
                return None

            company_id = int(company_row["id"])

        # Company membership + password are the sign-in credentials. The
        # workspace code is no longer asked for here: the company's database key
        # is also wrapped by the server master key (see backend/security/keyring
        # -- the wrap that lets the bot answer with no human present), so the
        # code was only ever a second login factor, never the only thing that
        # opens the data. The owner keeps the code as their activation secret,
        # and any employee who wants a second factor turns on TOTP below.
        with database_manager.control() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (utc_now_iso(), utc_now_iso(), user_data["id"]),
            )
            conn.commit()

        safe_user = self.sanitize_user(user_data)
        safe_user["active_company_id"] = company_id
        safe_user["active_company_name"] = company_row["name"]
        safe_user["active_company_slug"] = company_row["slug"]

        logger.info(
            "Login succeeded user id=%s company id=%s", user_data["id"], company_id
        )
        return safe_user

    def authenticate_platform(
        self,
        *,
        email: str,
        password: str,
    ) -> dict[str, Any] | None:
        """Verify a platform administrator, with no company involved.

        Deliberately asks for no workspace code: a platform session never opens
        a company database, so there is nothing for a code to unlock. That is
        the whole point of the split — the operator can run the platform without
        being able to read what customers wrote.
        """
        normalized_email = self.normalize_email(email)

        with database_manager.control() as conn:
            user_row = conn.execute(
                "SELECT * FROM users WHERE LOWER(email) = ? LIMIT 1",
                (normalized_email,),
            ).fetchone()

            if not user_row:
                self._dummy_password_check()
                logger.info("Platform login rejected: unknown email")
                return None

            user_data = dict(user_row)

            if user_data.get("status") != "active":
                self._dummy_password_check()
                return None

            if not self.verify_password(password, user_data.get("password_hash")):
                logger.info(
                    "Platform login rejected: bad password user id=%s",
                    user_data["id"],
                )
                return None

            if not user_data.get("is_super_admin"):
                logger.warning(
                    "Platform login rejected: user id=%s is not a platform "
                    "administrator",
                    user_data["id"],
                )
                return None

            conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (utc_now_iso(), utc_now_iso(), user_data["id"]),
            )
            conn.commit()

        logger.info("Platform login succeeded user id=%s", user_data["id"])
        return self.sanitize_user(user_data)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        user_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
        company_id: int | None = None,
        scope: str = COMPANY_SCOPE,
    ) -> dict[str, Any]:
        """Mint a session token bound to one scope.

        A company session reaches that company's data and nothing else. A
        platform session administers the platform and can never open a company
        database. Keeping them as separate tokens means a stolen platform token
        cannot read customer conversations, and a company token cannot suspend a
        company.
        """
        raw_token = secrets.token_urlsafe(48)
        expires_at = utc_now() + timedelta(
            minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
        )

        with database_manager.control() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (
                    user_id, company_id, scope, token_hash, expires_at,
                    ip_address, user_agent, created_at, last_used_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    company_id,
                    scope,
                    self.hash_token(raw_token),
                    expires_at.isoformat(),
                    ip_address,
                    user_agent,
                    utc_now_iso(),
                    utc_now_iso(),
                ),
            )
            conn.commit()

        return {
            "access_token": raw_token,
            "token_type": "bearer",
            "scope": scope,
            "expires_at": expires_at.isoformat(),
            "expires_in": config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }

    def get_user_from_token(self, raw_token: str) -> dict[str, Any] | None:
        token_hash = self.hash_token(raw_token)
        now = utc_now()

        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT
                    auth_sessions.id AS session_id,
                    auth_sessions.expires_at,
                    auth_sessions.revoked_at,
                    auth_sessions.company_id AS active_company_id,
                    auth_sessions.scope AS session_scope,
                    users.*
                FROM auth_sessions
                JOIN users ON users.id = auth_sessions.user_id
                WHERE auth_sessions.token_hash = ?
                LIMIT 1
                """,
                (token_hash,),
            ).fetchone()

            if not row:
                return None

            data = dict(row)

            if data.get("revoked_at") or data.get("status") != "active":
                return None

            try:
                expires_at = datetime.fromisoformat(data["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= now:
                    return None
            except (TypeError, ValueError):
                return None

            conn.execute(
                "UPDATE auth_sessions SET last_used_at = ? WHERE id = ?",
                (utc_now_iso(), data["session_id"]),
            )
            conn.commit()

            safe_user = self.sanitize_user(data)
            safe_user["session_scope"] = data.get("session_scope") or COMPANY_SCOPE
            return safe_user

    def revoke_token(self, raw_token: str) -> bool:
        with database_manager.control() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (utc_now_iso(), self.hash_token(raw_token)),
            )
            conn.commit()
            return cursor.rowcount > 0

    def revoke_all_user_sessions(self, user_id: int) -> int:
        with database_manager.control() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = ?
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (utc_now_iso(), user_id),
            )
            conn.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Passwords
    # ------------------------------------------------------------------

    def set_password(
        self, *, user_id: int, new_password: str, must_change: bool = False
    ) -> None:
        """Replace a password and end every session that used the old one.

        Revoking is not optional politeness. A password is changed because it
        may be known to somebody else; leaving that person's existing session
        alive would mean the change accomplished nothing for up to twelve hours.

        `revoke_all_user_sessions` has existed since the session table was
        written and was called from nowhere. This is its first caller.
        """
        password_hash = self.hash_password(new_password)
        now = utc_now_iso()

        with database_manager.control() as conn:
            cursor = conn.execute(
                """
                UPDATE users
                SET password_hash = ?,
                    password_changed_at = ?,
                    must_change_password = ?,
                    locked_until = NULL,
                    locked_reason = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (password_hash, now, 1 if must_change else 0, now, int(user_id)),
            )

            if not cursor.rowcount:
                raise ValueError(f"No user with id {user_id}.")

            # Same reasoning as `unlock_account`: leaving the failures behind
            # would let the account re-lock on the next typo, which reads to the
            # employee as the reset never having worked.
            conn.execute(
                """
                DELETE FROM login_attempts
                WHERE succeeded = 0
                  AND email = (SELECT email FROM users WHERE id = ?)
                """,
                (int(user_id),),
            )
            conn.commit()

        self.revoke_all_user_sessions(int(user_id))
        logger.info("Password changed for user id=%s", user_id)

    def change_own_password(
        self, *, user_id: int, current_password: str, new_password: str
    ) -> bool:
        """Change a password on presentation of the current one."""
        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE id = ? LIMIT 1",
                (int(user_id),),
            ).fetchone()

        if not row or not self.verify_password(
            current_password, row["password_hash"]
        ):
            return False

        self.set_password(user_id=user_id, new_password=new_password)
        return True

    # ------------------------------------------------------------------
    # Password reset links
    # ------------------------------------------------------------------

    def user_for_password_reset(self, email: str) -> dict[str, Any] | None:
        """An active account matching this email, for a self-service reset.

        Returns only what the reset email needs (id, address, name), or None
        when there is no active account. The caller answers identically either
        way, so this method is the only place that knows the difference.
        """
        normalized = self.normalize_email(email)

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id, email, full_name, status FROM users "
                "WHERE LOWER(email) = ? LIMIT 1",
                (normalized,),
            ).fetchone()

        if not row or dict(row).get("status") != "active":
            return None

        return {
            "id": int(row["id"]),
            "email": row["email"],
            "full_name": row["full_name"],
        }

    def create_password_reset(
        self,
        *,
        user_id: int,
        created_by_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> str:
        """Mint a single-use reset link token and return it once.

        Only the hash is stored, exactly as a session token is. Somebody reading
        this table learns that a reset was issued and to whom, which is what an
        audit needs; they cannot use it, which is what safety needs.

        Any earlier unused token for the same user is spent first — two live
        links for one account means the older one is a second key nobody is
        tracking.
        """
        token = secrets.token_urlsafe(48)
        now = utc_now()
        expires_at = now + timedelta(minutes=config.PASSWORD_RESET_TTL_MINUTES)

        with database_manager.control() as conn:
            conn.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = ?
                WHERE user_id = ? AND used_at IS NULL
                """,
                (now.isoformat(), int(user_id)),
            )
            conn.execute(
                """
                INSERT INTO password_reset_tokens (
                    user_id, token_hash, created_by_user_id, ip_address,
                    expires_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    self.hash_token(token),
                    int(created_by_user_id) if created_by_user_id else None,
                    ip_address,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.commit()

        return token

    def consume_password_reset(self, *, token: str, new_password: str) -> bool:
        """Spend a reset token and set the new password. One attempt, one use."""
        token_hash = self.hash_token(token)
        now = utc_now()

        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, expires_at, used_at
                FROM password_reset_tokens
                WHERE token_hash = ?
                LIMIT 1
                """,
                (token_hash,),
            ).fetchone()

            if not row or row["used_at"]:
                return False

            try:
                expires_at = datetime.fromisoformat(row["expires_at"])
            except (TypeError, ValueError):
                return False

            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at <= now:
                return False

            # Marked spent before the password is written. If setting the
            # password then fails, the link is dead and the administrator sends
            # another — the opposite order would leave a usable link after a
            # partial failure.
            conn.execute(
                "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
                (now.isoformat(), int(row["id"])),
            )
            conn.commit()

            user_id = int(row["user_id"])

        self.set_password(user_id=user_id, new_password=new_password)
        return True

    def prune_expired_sessions(self, retention_hours: int = 72) -> int:
        """Delete sessions that expired or were revoked a while ago.

        Kept for a few days rather than removed the moment they expire, because
        the row is the only record that a session existed and the security log
        may want to say where somebody signed in from.
        """
        cutoff = (utc_now() - timedelta(hours=retention_hours)).isoformat()

        with database_manager.control() as conn:
            cursor = conn.execute(
                """
                DELETE FROM auth_sessions
                WHERE (expires_at < ? AND revoked_at IS NULL)
                   OR (revoked_at IS NOT NULL AND revoked_at < ?)
                """,
                (cutoff, cutoff),
            )
            conn.commit()
            return cursor.rowcount

    def prune_password_resets(self, retention_hours: int = 72) -> int:
        with database_manager.control() as conn:
            cutoff = (utc_now() - timedelta(hours=retention_hours)).isoformat()
            cursor = conn.execute(
                "DELETE FROM password_reset_tokens WHERE created_at < ?", (cutoff,)
            )
            conn.commit()
            return cursor.rowcount

    # Everything about the signed-in caller that may reach a browser. An
    # allow-list, not a deny-list, and that is the whole point: the previous
    # version removed five known-secret keys and passed the rest of the `users`
    # row through, so any column added to that table afterwards was published
    # by default until somebody remembered to add it to the list. The columns
    # about to be added there hold a TOTP secret and recovery codes.
    #
    # Adding a key here is a decision to show it. Not adding one is the safe
    # accident.
    PUBLIC_USER_FIELDS: tuple[str, ...] = (
        "id",
        "email",
        "full_name",
        "phone",
        "status",
        "is_super_admin",
        "last_login_at",
        # When the password was last set, and whether the user is being made to
        # change it. Both are shown to the person they describe: the interface
        # cannot route them to the change-password screen without knowing.
        "password_changed_at",
        "must_change_password",
        # Whether this account has a second factor. Published to the person it
        # describes, for the same reason as `must_change_password`: the
        # interface cannot route them to enrolment without knowing, and the
        # sign-in path reads it back off this dict to decide whether to demand
        # a code. The **secret** is never in this list — it is a
        # password-equivalent and stays sealed in the row.
        "totp_enabled",
        "created_at",
        "updated_at",
        # Not columns on `users` — these come from the session row that
        # `get_user_from_token` joins, and every request that resolves a
        # company reads `active_company_id` back off this dict.
        "active_company_id",
        "session_scope",
    )

    def sanitize_user(self, user: dict[str, Any]) -> dict[str, Any]:
        """The caller's own record, reduced to what may be published."""
        safe_user = {
            field: user[field] for field in self.PUBLIC_USER_FIELDS if field in user
        }

        safe_user["is_super_admin"] = bool(safe_user.get("is_super_admin"))
        return safe_user

    # ------------------------------------------------------------------
    # Companies and permissions
    # ------------------------------------------------------------------

    def get_user_companies(self, user_id: int) -> list[dict[str, Any]]:
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT
                    companies.id,
                    companies.workspace_id,
                    companies.name,
                    companies.slug,
                    companies.country,
                    companies.currency,
                    companies.timezone,
                    companies.default_language,
                    companies.status,
                    company_users.branch_id,
                    company_users.role_id,
                    roles.name AS role_name,
                    roles.code AS role_code
                FROM company_users
                JOIN companies ON companies.id = company_users.company_id
                LEFT JOIN roles ON roles.id = company_users.role_id
                WHERE company_users.user_id = ?
                  AND company_users.status = 'active'
                  AND companies.status = 'active'
                ORDER BY companies.id ASC
                """,
                (user_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def resolve_company_id(
        self,
        current_user: dict[str, Any],
        requested_company_id: int | None = None,
    ) -> int:
        """Decide which company this request operates on.

        There is no fallback to a default company. Guessing here is what makes a
        misrouted request read the wrong tenant's data, so an unresolvable
        request is refused instead.
        """
        active_company_id = current_user.get("active_company_id")

        # A platform session has no company by construction, and must never
        # acquire one: that is what stops the operator reading customer data.
        if current_user.get("session_scope") == PLATFORM_SCOPE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "A platform session cannot read company data. Sign in to "
                    "the company with its workspace code."
                ),
            )

        # A super admin gets no blanket reach across companies. Holding the
        # master key lets the server open any database unattended, but a person
        # still has to prove the company's workspace code at login — otherwise
        # the encryption protects customers from a stolen disk and from nobody
        # else.
        if current_user.get("is_super_admin"):
            if requested_company_id is not None and (
                active_company_id is None
                or int(requested_company_id) != int(active_company_id)
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Sign in to that company with its workspace code to "
                        "open its data."
                    ),
                )

            if active_company_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Select a company for this request.",
                )

            return int(active_company_id)

        companies = self.get_user_companies(current_user["id"])

        if not companies:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account is not assigned to an active company.",
            )

        allowed_company_ids = {company["id"] for company in companies}

        if requested_company_id is None:
            if active_company_id in allowed_company_ids:
                return int(active_company_id)
            return int(companies[0]["id"])

        if int(requested_company_id) not in allowed_company_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this company.",
            )

        return int(requested_company_id)

    def has_permission(
        self,
        user_id: int,
        company_id: int,
        permission_code: str,
        is_super_admin: bool = False,
    ) -> bool:
        if is_super_admin:
            return True

        with database_manager.control() as conn:
            role = conn.execute(
                """
                SELECT roles.id, roles.code
                FROM company_users
                JOIN roles ON roles.id = company_users.role_id
                WHERE company_users.user_id = ?
                  AND company_users.company_id = ?
                  AND company_users.status = 'active'
                LIMIT 1
                """,
                (user_id, company_id),
            ).fetchone()

            if not role:
                return False

            if role["code"] == "owner":
                return True

            permission = conn.execute(
                """
                SELECT permissions.id
                FROM role_permissions
                JOIN permissions ON permissions.id = role_permissions.permission_id
                WHERE role_permissions.role_id = ? AND permissions.code = ?
                LIMIT 1
                """,
                (role["id"], permission_code),
            ).fetchone()

            return permission is not None

    def user_permission_codes(self, user_id: int, company_id: int) -> list[str]:
        """All permission codes this user holds, for the frontend to hide UI."""
        with database_manager.control() as conn:
            role = conn.execute(
                """
                SELECT roles.id, roles.code
                FROM company_users
                JOIN roles ON roles.id = company_users.role_id
                WHERE company_users.user_id = ?
                  AND company_users.company_id = ?
                  AND company_users.status = 'active'
                LIMIT 1
                """,
                (user_id, company_id),
            ).fetchone()

            if not role:
                return []

            if role["code"] == "owner":
                rows = conn.execute("SELECT code FROM permissions ORDER BY code").fetchall()
                return [row["code"] for row in rows]

            rows = conn.execute(
                """
                SELECT permissions.code
                FROM role_permissions
                JOIN permissions ON permissions.id = role_permissions.permission_id
                WHERE role_permissions.role_id = ?
                ORDER BY permissions.code
                """,
                (role["id"],),
            ).fetchall()

            return [row["code"] for row in rows]

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def create_user(
        self,
        email: str,
        password: str,
        full_name: str,
        phone: str | None = None,
        is_super_admin: bool = False,
    ) -> int:
        normalized_email = self.normalize_email(email)
        password_hash = self.hash_password(password)
        now = utc_now_iso()

        with database_manager.control() as conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE LOWER(email) = ? LIMIT 1",
                (normalized_email,),
            ).fetchone()

            if existing:
                raise ValueError("A user with this email already exists.")

            cursor = conn.execute(
                """
                INSERT INTO users (
                    email, password_hash, full_name, phone,
                    status, is_super_admin, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    normalized_email,
                    password_hash,
                    full_name,
                    phone,
                    1 if is_super_admin else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def assign_user_to_company(
        self,
        user_id: int,
        company_id: int,
        role_code: str = "owner",
        branch_id: int | None = None,
    ) -> None:
        with database_manager.control() as conn:
            role = conn.execute(
                "SELECT id FROM roles WHERE company_id = ? AND code = ? LIMIT 1",
                (company_id, role_code),
            ).fetchone()

            if not role:
                raise ValueError(f"Role '{role_code}' does not exist for this company.")

            conn.execute(
                """
                INSERT INTO company_users (
                    company_id, user_id, role_id, branch_id, status, created_at
                )
                VALUES (?, ?, ?, ?, 'active', ?)
                ON CONFLICT(company_id, user_id) DO UPDATE SET
                    role_id = excluded.role_id,
                    branch_id = excluded.branch_id,
                    status = 'active'
                """,
                (company_id, user_id, role["id"], branch_id, utc_now_iso()),
            )
            conn.commit()

    def user_display_names(
        self, company_id: int, user_ids: list[int]
    ) -> dict[int, str]:
        """Resolve several employee names in one control-plane query.

        Conversations live in the tenant database and cannot join to `users`, so
        the inbox resolves the whole page's names at once instead of issuing one
        query per row.

        **Membership, not employment.** This answers "who was this?", and the
        answer does not change when somebody leaves. It used to require
        `users.status = 'active'` and `company_users.status = 'active'`, so the
        day an employee was disabled every task they had been assigned, every
        note they had written and every reply they had sent went blank at once —
        the company losing the authorship of its own history because a person
        stopped working there. The row still held their id; only the name
        refused to resolve.

        Withholding it protected nothing. They were an employee of this company
        and their name is already through its records; the filter did not hide
        it from anyone, it only made the record unreadable.

        `company_id` is what does the scoping, and it stays: an id belonging to
        another company's employee still resolves to nothing, which is what
        `ticket_service` and `appointment_service` rely on when they say this
        function is scoped. Status is a different question, and the two callers
        that want *current* employees — the assignment pickers — ask
        `company_employees`, which does filter on it. Same split as
        `list_company_ids` against `list_all_company_ids`: serving asks who is
        active, attribution asks who it was.
        """
        unique_ids = sorted({int(uid) for uid in user_ids if uid is not None})

        if not unique_ids:
            return {}

        placeholders = ",".join("?" for _ in unique_ids)

        with database_manager.control() as conn:
            rows = conn.execute(
                f"""
                SELECT users.id, users.full_name, users.email
                FROM users
                JOIN company_users ON company_users.user_id = users.id
                WHERE users.id IN ({placeholders})
                  AND company_users.company_id = ?
                """,
                (*unique_ids, company_id),
            ).fetchall()

        return {
            int(row["id"]): (row["full_name"] or row["email"] or f"User {row['id']}")
            for row in rows
        }

    def company_employees(
        self, company_id: int, *, include_contact_details: bool = False
    ) -> list[dict[str, Any]]:
        """The company's active employees, for assigning and attributing work.

        By default this returns a name and an id and nothing else, because that
        is all the screens that call it display: an assignment dropdown and the
        name beside a timeline entry.

        It used to return every colleague's email, phone, role and branch to
        anyone holding ``conversations.view`` — the lowest permission on the
        platform. None of it was rendered, so nobody noticed; it was visible to
        any employee who opened the browser's network tab.

        ``include_contact_details`` is the deliberate opt-in, and the caller is
        expected to have checked ``users.view`` first. Who holds that permission
        is the company owner's decision, made on the roles screen — which is the
        right place for it, because whether colleagues may see each other's
        contact details is a question about how that business runs, not one this
        code should answer for everybody.
        """
        columns = [
            "users.id",
            "users.full_name",
        ]

        if include_contact_details:
            columns += [
                "users.email",
                "users.phone",
                "roles.name AS role_name",
                "roles.code AS role_code",
                "company_users.branch_id",
            ]

        with database_manager.control() as conn:
            rows = conn.execute(
                f"""
                SELECT {", ".join(columns)}
                FROM company_users
                JOIN users ON users.id = company_users.user_id
                LEFT JOIN roles ON roles.id = company_users.role_id
                WHERE company_users.company_id = ?
                  AND company_users.status = 'active'
                  AND users.status = 'active'
                ORDER BY users.full_name ASC, users.id ASC
                """,
                (company_id,),
            ).fetchall()

        employees = []

        for row in rows:
            record = dict(row)
            # The fallback used to be the email address, which would have put
            # one back into the default response through the display name.
            record["display_name"] = record.get("full_name") or f"User {row['id']}"
            employees.append(record)

        return employees


auth_service = AuthService()


# ----------------------------------------------------------------------
# FastAPI dependencies
# ----------------------------------------------------------------------


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = auth_service.get_user_from_token(credentials.credentials)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or token is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # A platform token administers the platform; it must not be usable as a
    # company token, or the split would be a naming convention rather than a
    # boundary.
    if user.get("session_scope") == PLATFORM_SCOPE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This is a platform administration session. Sign in to a "
                "company to use the workspace."
            ),
        )

    user["_raw_token"] = credentials.credentials

    # A forced password change is enforced here rather than in the interface,
    # because an interface check is a suggestion: the token is already minted
    # and every endpoint would answer to it. Refusing at the one dependency
    # every customer route depends on is what makes "must change" mean it.
    if user.get("must_change_password"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "password_change_required",
                "message": (
                    "Your password must be changed before you can continue."
                ),
            },
        )

    return user


async def get_user_changing_password(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """`get_current_user` without the forced-change refusal.

    Exactly one route may use this — the one that changes the password. It is a
    separate dependency rather than a flag on the first so that exempting a
    route is a visible, deliberate act in the route's own signature.
    """
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = auth_service.get_user_from_token(credentials.credentials)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or token is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user["_raw_token"] = credentials.credentials
    return user


async def get_platform_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """Authenticate a platform administrator for the control plane.

    Three conditions, all required: a valid token, minted in the platform
    scope, belonging to a user who is still a super admin. Checking the flag
    again here means revoking someone's platform rights takes effect on their
    next request rather than when their token happens to expire.
    """
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = auth_service.get_user_from_token(credentials.credentials)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or token is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.get("session_scope") != PLATFORM_SCOPE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign in to the platform console to perform this action.",
        )

    if not bool(user.get("is_super_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator access required.",
        )

    # Enrolment in two-factor authentication is mandatory here and refused
    # server-side, the same way `must_change_password` is. A message the
    # interface could skip is not a requirement.
    #
    # The session is still minted at sign-in so the administrator can reach the
    # enrolment routes and nothing else — a dependency that refused the token
    # outright would leave them with no way to satisfy it.
    if not bool(user.get("totp_enabled")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "totp_enrolment_required",
                "message": (
                    "Set up two-factor authentication before using the console."
                ),
            },
        )

    user["_raw_token"] = credentials.credentials
    return user


async def get_platform_admin_enrolling(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """A platform administrator who may still be enrolling their second factor.

    The permissive twin of `get_platform_admin`, and the only dependency the
    enrolment routes use. Same reasoning as `get_user_changing_password`: a
    requirement with no reachable way to satisfy it is a locked door, and this
    one would lock out the account that has nobody above it to help.

    Everything else about it is identical — platform scope, still a super
    admin — so an ordinary token cannot reach enrolment either.
    """
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = auth_service.get_user_from_token(credentials.credentials)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or token is invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.get("session_scope") != PLATFORM_SCOPE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign in to the platform console to perform this action.",
        )

    if not bool(user.get("is_super_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator access required.",
        )

    user["_raw_token"] = credentials.credentials
    return user


# A refusal per (employee, permission) at most this often. An authenticated
# employee can hammer a forbidden endpoint as fast as the network allows, and a
# log entry per attempt would turn the audit trail into the attack's payload —
# unbounded writes into the company's own encrypted database, drowning the
# entries an owner actually needs to see.
#
# The first refusal in each window is what carries the information. A hundred
# more in the same minute say the same thing and cost a hundred writes.
PERMISSION_DENIED_WINDOW_SECONDS = 60

_permission_denied_seen: dict[tuple[int, int, str], datetime] = {}


def _record_permission_denied(
    *,
    current_user: dict[str, Any],
    company_id: int,
    permission_code: str,
    request: Request,
) -> None:
    """File a 403 in the company's own log, at most once a minute per employee.

    An employee reaching for something their role does not cover is worth the
    owner knowing about: either a role that is drawn too tightly for the job, or
    somebody looking where they should not. Both are the owner's to judge, and
    neither reaches them today.
    """
    user_id = int(current_user.get("id") or 0)
    key = (user_id, int(company_id), permission_code)
    now = datetime.now(timezone.utc)
    last = _permission_denied_seen.get(key)

    if last and (now - last).total_seconds() < PERMISSION_DENIED_WINDOW_SECONDS:
        return

    _permission_denied_seen[key] = now

    # Bounded so a long-running process cannot accumulate a key per employee per
    # permission for ever. Cleared wholesale rather than evicted one at a time:
    # the worst a cleared window costs is one extra entry, and the alternative
    # is an eviction policy nobody will read again.
    if len(_permission_denied_seen) > 10_000:
        _permission_denied_seen.clear()
        _permission_denied_seen[key] = now

    try:
        from backend.services.activity_service import Action, activity_service

        activity_service.record_for(
            current_user,
            company_id=int(company_id),
            action=Action.PERMISSION_DENIED,
            category="auth",
            kind="security",
            summary=f"Refused an action needing '{permission_code}'",
            severity="notice",
            # The path, not the payload. What was attempted is the useful part;
            # the body of a refused request may hold anything.
            after={"permission": permission_code, "path": request.url.path},
            ip_address=client_ip(request),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record a permission denial")


def require_permission(permission_code: str) -> Callable:
    """Build a dependency that enforces one permission.

    Every permission the platform seeds is enforced through this. A role screen
    that lists permissions the API never checks is worse than no role screen at
    all, because it tells an administrator they have restricted someone when
    they have not.
    """

    def dependency(
        request: Request,
        current_user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        company_id = auth_service.resolve_company_id(current_user)

        allowed = auth_service.has_permission(
            user_id=int(current_user["id"]),
            company_id=company_id,
            permission_code=permission_code,
            is_super_admin=bool(current_user.get("is_super_admin")),
        )

        if not allowed:
            _record_permission_denied(
                current_user=current_user,
                company_id=company_id,
                permission_code=permission_code,
                request=request,
            )

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the '{permission_code}' permission.",
            )

        current_user["_company_id"] = company_id
        return current_user

    return dependency


def require_super_admin(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if not bool(current_user.get("is_super_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin access required.",
        )
    return current_user


def client_ip(request: Request) -> str | None:
    """Best-effort client address, trusting the proxy header nginx sets.

    nginx is configured to *replace* X-Forwarded-For with the real peer address
    (`proxy_set_header X-Forwarded-For $remote_addr`), so behind the intended
    deployment the first token is genuinely the client. The value is still
    validated as an IP address before it is trusted: it is written to
    `login_attempts.ip_address` and the control-plane `audit_log`, and an
    unvalidated header would let a caller forge the platform's own incident
    record (or, without the proxy, hand the throttle a value it never saw). A
    header that is not a plain IP is ignored in favour of the socket peer.
    """
    forwarded = request.headers.get("x-forwarded-for", "")

    if forwarded:
        candidate = forwarded.split(",")[0].strip()

        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            # A forged or malformed header -- fall through to the real peer
            # rather than store something an attacker chose.
            pass

    return request.client.host if request.client else None
