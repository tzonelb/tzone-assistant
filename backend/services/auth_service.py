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

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        user_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
        company_id: int | None = None,
    ) -> dict[str, Any]:
        raw_token = secrets.token_urlsafe(48)
        expires_at = utc_now() + timedelta(
            minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
        )

        with database_manager.control() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (
                    user_id, company_id, token_hash, expires_at,
                    ip_address, user_agent, created_at, last_used_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    company_id,
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

            return self.sanitize_user(data)

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

    def sanitize_user(self, user: dict[str, Any]) -> dict[str, Any]:
        safe_user = dict(user)

        for secret_field in (
            "password_hash",
            "token_hash",
            "session_id",
            "revoked_at",
            "expires_at",
        ):
            safe_user.pop(secret_field, None)

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

        if current_user.get("is_super_admin"):
            resolved = requested_company_id or active_company_id

            if resolved is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Select a company for this request.",
                )

            return int(resolved)

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

    def company_employees(self, company_id: int) -> list[dict[str, Any]]:
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT
                    users.id,
                    users.full_name,
                    users.email,
                    users.phone,
                    roles.name AS role_name,
                    roles.code AS role_code,
                    company_users.branch_id
                FROM company_users
                JOIN users ON users.id = company_users.user_id
                LEFT JOIN roles ON roles.id = company_users.role_id
                WHERE company_users.company_id = ?
                  AND company_users.status = 'active'
                  AND users.status = 'active'
                ORDER BY users.full_name ASC, users.email ASC
                """,
                (company_id,),
            ).fetchall()

        return [
            {
                **dict(row),
                "display_name": (
                    row["full_name"] or row["email"] or f"User {row['id']}"
                ),
            }
            for row in rows
        ]


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
