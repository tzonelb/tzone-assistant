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
    PASSWORD_ITERATIONS = 310_000
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

    def is_login_blocked(self, *, email: str | None, ip_address: str | None) -> bool:
        """Return whether this email or address has burned its attempts.

        Counting happens in the database rather than in memory so the limit
        survives a restart and still applies across multiple workers.
        """
        window_start = (
            utc_now() - timedelta(minutes=config.LOGIN_LOCKOUT_MINUTES)
        ).isoformat()

        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS failures
                FROM login_attempts
                WHERE succeeded = 0
                  AND created_at >= ?
                  AND (
                        (email = ? AND email IS NOT NULL)
                     OR (ip_address = ? AND ip_address IS NOT NULL)
                  )
                """,
                (
                    window_start,
                    self.normalize_email(email) if email else None,
                    ip_address,
                ),
            ).fetchone()

        return int(row["failures"] if row else 0) >= config.LOGIN_MAX_ATTEMPTS

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

    def authenticate(
        self,
        *,
        workspace_code: str,
        company: str,
        email: str,
        password: str,
    ) -> dict[str, Any] | None:
        """Verify all four credentials and return the user, or None.

        The failure reason is deliberately not returned to the caller: the API
        answers every failure identically so nobody can probe which companies,
        codes or emails exist.
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

        # The decisive check. A wrong code fails to unseal the company key, so
        # possession of the code is proven rather than claimed.
        if not database_manager.verify_workspace_code(company_id, workspace_code):
            logger.warning(
                "Login rejected: bad workspace code for company id=%s", company_id
            )
            return None

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
                  AND users.status = 'active'
                  AND company_users.status = 'active'
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

    user["_raw_token"] = credentials.credentials
    return user


def require_permission(permission_code: str) -> Callable:
    """Build a dependency that enforces one permission.

    Every permission the platform seeds is enforced through this. A role screen
    that lists permissions the API never checks is worse than no role screen at
    all, because it tells an administrator they have restricted someone when
    they have not.
    """

    def dependency(
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
    """Best-effort client address, trusting the proxy header nginx sets."""
    forwarded = request.headers.get("x-forwarded-for", "")

    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.client.host if request.client else None
