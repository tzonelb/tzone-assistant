
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import config
from database.database import db


security = HTTPBearer(
    auto_error=False,
)


class AuthService:
    PASSWORD_ITERATIONS = 310_000
    PASSWORD_ALGORITHM = "sha256"

    def create_tables(self):
        with db.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TIMESTAMP NOT NULL,
                    revoked_at TIMESTAMP,
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id)
                        REFERENCES users(id)
                        ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_auth_sessions_user
                ON auth_sessions(user_id)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_auth_sessions_token
                ON auth_sessions(token_hash)
            """)

            columns = {row["name"] for row in cursor.execute("PRAGMA table_info(auth_sessions)").fetchall()}
            if "company_id" not in columns:
                cursor.execute("ALTER TABLE auth_sessions ADD COLUMN company_id INTEGER")

            # Two-factor authentication (TOTP) columns — additive migration
            # so existing installations pick them up on startup.
            user_columns = {
                row["name"]
                for row in cursor.execute("PRAGMA table_info(users)").fetchall()
            }
            if "totp_secret" not in user_columns:
                cursor.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
            if "totp_enabled" not in user_columns:
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0"
                )

            conn.commit()

    def normalize_email(self, email: str) -> str:
        return email.strip().lower()

    def hash_password(self, password: str) -> str:
        if len(password) < 8:
            raise ValueError(
                "Password must contain at least 8 characters."
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

    def verify_password(
        self,
        password: str,
        stored_hash: str | None,
    ) -> bool:
        if not stored_hash:
            return False

        try:
            algorithm_name, iterations_text, salt_hex, hash_hex = (
                stored_hash.split("$", 3)
            )

            if algorithm_name != "pbkdf2_sha256":
                return False

            iterations = int(iterations_text)
            salt = bytes.fromhex(salt_hex)
            expected_hash = bytes.fromhex(hash_hex)

            actual_hash = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                iterations,
            )

            return hmac.compare_digest(
                actual_hash,
                expected_hash,
            )

        except (
            ValueError,
            TypeError,
            AttributeError,
        ):
            return False

    def hash_token(self, token: str) -> str:
        return hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

    MAX_FAILED_LOGIN_ATTEMPTS = 5
    LOCKOUT_MINUTES = 15

    def _register_failed_login(self, conn, *, user_id: int) -> None:
        """Called with the SAME connection/transaction authenticate() is
        already using, so the failed-attempt count is committed even
        though authenticate() returns None right after (the `with`
        block still exits normally, not via exception)."""
        row = conn.execute(
            "SELECT failed_login_attempts FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        attempts = int(row["failed_login_attempts"] or 0) + 1

        locked_until = None
        if attempts >= self.MAX_FAILED_LOGIN_ATTEMPTS:
            locked_until = (
                datetime.now(timezone.utc) + timedelta(minutes=self.LOCKOUT_MINUTES)
            ).isoformat()
            attempts = 0

        conn.execute(
            "UPDATE users SET failed_login_attempts = ?, locked_until = ? WHERE id = ?",
            (attempts, locked_until, user_id),
        )

    def authenticate(
        self,
        email: str,
        password: str,
        company: str,
    ) -> dict[str, Any] | None:
        normalized_email = self.normalize_email(email)
        normalized_company = company.strip().lower()

        print("=" * 70)
        print("AUTHENTICATE()")
        print(f"EMAIL: {normalized_email}")
        print(f"COMPANY: {normalized_company}")

        with db.connect() as conn:
            user = conn.execute(
                """
                SELECT users.*
                FROM users
                WHERE LOWER(users.email) = ?
                LIMIT 1
                """,
                (normalized_email,),
            ).fetchone()

            print("USER FOUND:", user is not None)

            if not user:
                print("FAILED: USER NOT FOUND")
                return None

            user_data = dict(user)
            print("STATUS:", user_data.get("status"))
            print("SUPER ADMIN:", user_data.get("is_super_admin"))

            if user_data.get("status") != "active":
                print("FAILED: USER NOT ACTIVE")
                return None

            locked_until = user_data.get("locked_until")
            if locked_until:
                try:
                    locked_until_dt = datetime.fromisoformat(locked_until)
                    if locked_until_dt.tzinfo is None:
                        locked_until_dt = locked_until_dt.replace(tzinfo=timezone.utc)
                    if locked_until_dt > datetime.now(timezone.utc):
                        print("FAILED: ACCOUNT LOCKED")
                        return None
                except ValueError:
                    pass

            password_ok = self.verify_password(
                password,
                user_data.get("password_hash"),
            )
            print("PASSWORD OK:", password_ok)

            if not password_ok:
                print("FAILED: BAD PASSWORD")
                self._register_failed_login(conn, user_id=user_data["id"])
                return None

            conn.execute(
                "UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
                (user_data["id"],),
            )

            company_row = conn.execute(
                """
                SELECT companies.id, companies.name, companies.slug
                FROM companies
                JOIN workspaces
                    ON workspaces.id = companies.workspace_id
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

            print("COMPANY FOUND:", company_row is not None)
            print("COMPANY ROW:", dict(company_row) if company_row else None)

            if not company_row:
                print("FAILED: COMPANY NOT FOUND")
                return None

            conn.execute(
                """
                UPDATE users
                SET last_login_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (user_data["id"],),
            )
            conn.commit()

            safe_user = self.sanitize_user(user_data)
            safe_user["active_company_id"] = company_row["id"]
            safe_user["active_company_name"] = company_row["name"]
            safe_user["active_company_slug"] = company_row["slug"]

            print("LOGIN SUCCESS")
            return safe_user

    def authenticate_super_admin(
        self,
        email: str,
        password: str,
    ) -> dict[str, Any] | None:
        """Company-free login for the dedicated Super Admin portal — same
        password/lockout checks as authenticate(), but requires no workspace
        code and rejects anyone whose account isn't is_super_admin=1 (a
        correct password for a regular company user must never grant entry
        here). active_company_id is set to config.DEFAULT_COMPANY_ID purely
        as a session anchor; resolve_company_id() already treats a super
        admin's active_company_id as a default, not a scope restriction."""
        normalized_email = self.normalize_email(email)

        with db.connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE LOWER(email) = ? LIMIT 1",
                (normalized_email,),
            ).fetchone()

            if not user:
                return None

            user_data = dict(user)

            if not user_data.get("is_super_admin"):
                return None

            if user_data.get("status") != "active":
                return None

            locked_until = user_data.get("locked_until")
            if locked_until:
                try:
                    locked_until_dt = datetime.fromisoformat(locked_until)
                    if locked_until_dt.tzinfo is None:
                        locked_until_dt = locked_until_dt.replace(tzinfo=timezone.utc)
                    if locked_until_dt > datetime.now(timezone.utc):
                        return None
                except ValueError:
                    pass

            if not self.verify_password(password, user_data.get("password_hash")):
                self._register_failed_login(conn, user_id=user_data["id"])
                return None

            conn.execute(
                "UPDATE users SET failed_login_attempts = 0, locked_until = NULL, "
                "last_login_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user_data["id"],),
            )
            conn.commit()

            safe_user = self.sanitize_user(user_data)
            safe_user["active_company_id"] = config.DEFAULT_COMPANY_ID
            safe_user["active_company_name"] = None
            safe_user["active_company_slug"] = None
            return safe_user

    def create_session(
        self,
        user_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
        company_id: int | None = None,
    ) -> dict[str, Any]:
        raw_token = secrets.token_urlsafe(48)
        token_hash = self.hash_token(raw_token)

        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(
                minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES
            )
        )

        with db.connect() as conn:
            conn.execute("""
                INSERT INTO auth_sessions (
                    user_id,
                    token_hash,
                    expires_at,
                    ip_address,
                    user_agent,
                    company_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                token_hash,
                expires_at.isoformat(),
                ip_address,
                user_agent,
                company_id,
            ))

            conn.commit()

        return {
            "access_token": raw_token,
            "token_type": "bearer",
            "expires_at": expires_at.isoformat(),
            "expires_in": (
                config.ACCESS_TOKEN_EXPIRE_MINUTES * 60
            ),
        }

    def get_user_from_token(
        self,
        raw_token: str,
    ) -> dict[str, Any] | None:
        token_hash = self.hash_token(raw_token)
        now = datetime.now(timezone.utc)

        with db.connect() as conn:
            row = conn.execute("""
                SELECT
                    auth_sessions.id AS session_id,
                    auth_sessions.expires_at,
                    auth_sessions.revoked_at,
                    auth_sessions.company_id AS active_company_id,
                    users.*
                FROM auth_sessions
                JOIN users
                    ON users.id = auth_sessions.user_id
                WHERE auth_sessions.token_hash = ?
                LIMIT 1
            """, (token_hash,)).fetchone()

            if not row:
                return None

            data = dict(row)

            if data.get("revoked_at"):
                return None

            if data.get("status") != "active":
                return None

            try:
                expires_at = datetime.fromisoformat(
                    data["expires_at"]
                )

                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(
                        tzinfo=timezone.utc
                    )

                if expires_at <= now:
                    return None

            except (
                TypeError,
                ValueError,
            ):
                return None

            conn.execute("""
                UPDATE auth_sessions
                SET last_used_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (data["session_id"],))

            conn.commit()

            return self.sanitize_user(data)

    def revoke_token(
        self,
        raw_token: str,
    ) -> bool:
        token_hash = self.hash_token(raw_token)

        with db.connect() as conn:
            cursor = conn.execute("""
                UPDATE auth_sessions
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE token_hash = ?
                  AND revoked_at IS NULL
            """, (token_hash,))

            conn.commit()

            return cursor.rowcount > 0

    def revoke_all_user_sessions(
        self,
        user_id: int,
    ) -> int:
        with db.connect() as conn:
            cursor = conn.execute("""
                UPDATE auth_sessions
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                  AND revoked_at IS NULL
            """, (user_id,))

            conn.commit()

            return cursor.rowcount

    def sanitize_user(
        self,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        safe_user = dict(user)

        safe_user.pop(
            "password_hash",
            None,
        )

        safe_user.pop(
            "totp_secret",
            None,
        )

        safe_user.pop(
            "token_hash",
            None,
        )

        safe_user.pop(
            "session_id",
            None,
        )

        safe_user.pop(
            "revoked_at",
            None,
        )

        safe_user.pop(
            "expires_at",
            None,
        )

        safe_user["is_super_admin"] = bool(
            safe_user.get("is_super_admin")
        )

        return safe_user

    def get_user_companies(
        self,
        user_id: int,
    ) -> list[dict[str, Any]]:
        """Includes each company's real granted permission_codes (not just
        role_code) so the frontend can gate admin-only nav/UI on the actual
        permission a custom role was given, instead of a role_code ==
        "owner" heuristic that misses everyone else."""
        with db.connect() as conn:
            rows = conn.execute("""
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
                    roles.code AS role_code,
                    (
                        SELECT GROUP_CONCAT(permissions.code)
                        FROM role_permissions
                        JOIN permissions ON permissions.id = role_permissions.permission_id
                        WHERE role_permissions.role_id = company_users.role_id
                    ) AS permission_codes_raw
                FROM company_users
                JOIN companies
                    ON companies.id = company_users.company_id
                LEFT JOIN roles
                    ON roles.id = company_users.role_id
                WHERE company_users.user_id = ?
                  AND company_users.status = 'active'
                  AND companies.status = 'active'
                ORDER BY companies.id ASC
            """, (user_id,)).fetchall()

            companies = []
            for row in rows:
                company = dict(row)
                raw = company.pop("permission_codes_raw", None)
                company["permission_codes"] = raw.split(",") if raw else []
                companies.append(company)
            return companies

    def resolve_company_id(
        self,
        current_user: dict[str, Any],
        requested_company_id: int | None = None,
    ) -> int:
        active_company_id = current_user.get("active_company_id")

        if current_user.get("is_super_admin"):
            return requested_company_id or active_company_id or config.DEFAULT_COMPANY_ID

        companies = self.get_user_companies(
            current_user["id"]
        )

        if not companies:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an active company.",
            )

        allowed_company_ids = {
            company["id"]
            for company in companies
        }

        if requested_company_id is None:
            if active_company_id in allowed_company_ids:
                return active_company_id
            return companies[0]["id"]

        if requested_company_id not in allowed_company_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this company.",
            )

        return requested_company_id

    def require_permission(
        self,
        current_user: dict[str, Any],
        company_id: int,
        permission_code: str,
    ) -> None:
        """One-liner for route handlers: raises 403 unless the current
        user holds permission_code (or is a super admin). Real,
        per-permission-code enforcement — not the old pattern of every
        admin-only route reusing the same generic users.manage code."""
        if not self.has_permission(
            user_id=current_user.get("id"), company_id=company_id,
            permission_code=permission_code, is_super_admin=bool(current_user.get("is_super_admin")),
        ):
            raise HTTPException(status_code=403, detail=f'You do not have permission to do this ("{permission_code}" required).')

    def has_permission(
        self,
        user_id: int,
        company_id: int,
        permission_code: str,
        is_super_admin: bool = False,
    ) -> bool:
        if is_super_admin:
            return True

        with db.connect() as conn:
            role = conn.execute("""
                SELECT
                    roles.id,
                    roles.code
                FROM company_users
                JOIN roles
                    ON roles.id = company_users.role_id
                WHERE company_users.user_id = ?
                  AND company_users.company_id = ?
                  AND company_users.status = 'active'
                LIMIT 1
            """, (
                user_id,
                company_id,
            )).fetchone()

            if not role:
                return False

            if role["code"] == "owner":
                return True

            # A per-user override always wins over the role default —
            # this is how an owner grants or revokes one specific
            # permission for one specific employee without creating a
            # whole new role for them.
            override = conn.execute("""
                SELECT allowed
                FROM user_permission_overrides
                WHERE company_id = ?
                  AND user_id = ?
                  AND permission_code = ?
                LIMIT 1
            """, (
                company_id,
                user_id,
                permission_code,
            )).fetchone()

            if override is not None:
                return bool(override["allowed"])

            permission = conn.execute("""
                SELECT permissions.id
                FROM role_permissions
                JOIN permissions
                    ON permissions.id =
                       role_permissions.permission_id
                WHERE role_permissions.role_id = ?
                  AND permissions.code = ?
                LIMIT 1
            """, (
                role["id"],
                permission_code,
            )).fetchone()

            return permission is not None

    def list_permission_overrides(
        self,
        company_id: int,
        user_id: int,
    ) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute("""
                SELECT permission_code, allowed
                FROM user_permission_overrides
                WHERE company_id = ? AND user_id = ?
                ORDER BY permission_code
            """, (company_id, user_id)).fetchall()
            return [
                {"permission_code": row["permission_code"], "allowed": bool(row["allowed"])}
                for row in rows
            ]

    def set_permission_overrides(
        self,
        company_id: int,
        user_id: int,
        overrides: list[dict[str, Any]],
    ) -> None:
        """Replaces the full override set for one user with the given
        list of {permission_code, allowed}. An empty list clears every
        override, returning the user to plain role defaults."""
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM user_permission_overrides WHERE company_id = ? AND user_id = ?",
                (company_id, user_id),
            )
            for item in overrides:
                conn.execute("""
                    INSERT INTO user_permission_overrides (company_id, user_id, permission_code, allowed)
                    VALUES (?, ?, ?, ?)
                """, (company_id, user_id, item["permission_code"], 1 if item["allowed"] else 0))
            conn.commit()

    def admin_reset_password(self, user_id: int) -> str:
        """Generates a fresh temporary password for a user, stores its
        hash, and revokes every existing session of theirs so a lost
        session token can't keep using the old password's session. The
        plaintext temporary password is returned once so the admin can
        hand it to the employee — nothing else in this codebase stores
        or emails it."""
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
        temporary_password = "".join(secrets.choice(alphabet) for _ in range(12))
        password_hash = self.hash_password(temporary_password)
        with db.connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (password_hash, user_id),
            )
            if cursor.rowcount == 0:
                conn.commit()
                raise ValueError("User not found.")
            conn.commit()
        self.revoke_all_user_sessions(user_id)
        return temporary_password

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

        with db.connect() as conn:
            existing = conn.execute("""
                SELECT id
                FROM users
                WHERE LOWER(email) = ?
                LIMIT 1
            """, (normalized_email,)).fetchone()

            if existing:
                raise ValueError(
                    "A user with this email already exists."
                )

            cursor = conn.execute("""
                INSERT INTO users (
                    email,
                    password_hash,
                    full_name,
                    phone,
                    status,
                    is_super_admin
                )
                VALUES (?, ?, ?, ?, 'active', ?)
            """, (
                normalized_email,
                password_hash,
                full_name,
                phone,
                1 if is_super_admin else 0,
            ))

            conn.commit()

            return cursor.lastrowid

    def assign_user_to_company(
        self,
        user_id: int,
        company_id: int,
        role_code: str = "owner",
        branch_id: int | None = None,
    ):
        with db.connect() as conn:
            role = conn.execute("""
                SELECT id
                FROM roles
                WHERE company_id = ?
                  AND code = ?
                LIMIT 1
            """, (
                company_id,
                role_code,
            )).fetchone()

            if not role:
                raise ValueError(
                    f"Role '{role_code}' does not exist."
                )

            conn.execute("""
                INSERT INTO company_users (
                    company_id,
                    user_id,
                    role_id,
                    branch_id,
                    status
                )
                VALUES (?, ?, ?, ?, 'active')
                ON CONFLICT(company_id, user_id)
                DO UPDATE SET
                    role_id = excluded.role_id,
                    branch_id = excluded.branch_id,
                    status = 'active'
            """, (
                company_id,
                user_id,
                role["id"],
                branch_id,
            ))

            conn.commit()

    # ------------------------------------------------------------------
    # Two-factor authentication (TOTP)
    # ------------------------------------------------------------------
    PENDING_2FA_TTL_SECONDS = 300  # 5 minutes

    def user_has_2fa(self, user_id: int) -> bool:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT totp_enabled FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return bool(row and row["totp_enabled"])

    def begin_totp_enrollment(self, user_id: int) -> dict[str, Any]:
        """Generate + store a fresh secret (NOT yet enabled) and return the
        secret plus an otpauth:// provisioning URI for authenticator apps."""
        from backend.services import totp_utils

        with db.connect() as conn:
            user = conn.execute(
                "SELECT id, email FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not user:
                raise ValueError("User not found.")

            secret = totp_utils.generate_secret()
            conn.execute(
                "UPDATE users SET totp_secret = ?, totp_enabled = 0 WHERE id = ?",
                (secret, user_id),
            )
            conn.commit()

        uri = totp_utils.provisioning_uri(secret, account_name=user["email"])
        return {"secret": secret, "otpauth_uri": uri}

    def confirm_totp_enrollment(self, user_id: int, code: str) -> None:
        from backend.services import totp_utils

        with db.connect() as conn:
            row = conn.execute(
                "SELECT totp_secret FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            secret = row["totp_secret"] if row else None
            if not secret:
                raise ValueError("Start enrollment before confirming a code.")
            if not totp_utils.verify(secret, code):
                raise ValueError("Invalid authentication code.")
            conn.execute(
                "UPDATE users SET totp_enabled = 1 WHERE id = ?", (user_id,)
            )
            conn.commit()

    def verify_totp_code(self, user_id: int, code: str) -> bool:
        from backend.services import totp_utils

        with db.connect() as conn:
            row = conn.execute(
                "SELECT totp_secret, totp_enabled FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if not row or not row["totp_enabled"] or not row["totp_secret"]:
            return False
        return totp_utils.verify(row["totp_secret"], code)

    def disable_totp(self, user_id: int, password: str, code: str) -> None:
        from backend.services import totp_utils

        with db.connect() as conn:
            row = conn.execute(
                "SELECT password_hash, totp_secret, totp_enabled FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                raise ValueError("User not found.")
            if not self.verify_password(password, row["password_hash"]):
                raise ValueError("Password is incorrect.")
            if not row["totp_enabled"] or not row["totp_secret"]:
                raise ValueError("Two-factor authentication is not enabled.")
            if not totp_utils.verify(row["totp_secret"], code):
                raise ValueError("Invalid authentication code.")
            conn.execute(
                "UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE id = ?",
                (user_id,),
            )
            conn.commit()

    def build_login_user(
        self, user_id: int, company_id: int | None
    ) -> dict[str, Any] | None:
        """Reconstruct the sanitized login user payload (with active company
        fields) used after a successful 2FA challenge."""
        with db.connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE id = ? LIMIT 1", (user_id,)
            ).fetchone()
            if not user:
                return None
            safe_user = self.sanitize_user(dict(user))
            if company_id is not None:
                company = conn.execute(
                    "SELECT id, name, slug FROM companies WHERE id = ? LIMIT 1",
                    (company_id,),
                ).fetchone()
                if company:
                    safe_user["active_company_id"] = company["id"]
                    safe_user["active_company_name"] = company["name"]
                    safe_user["active_company_slug"] = company["slug"]
        return safe_user

    # -- Stateless short-lived pending token (signed user_id + company_id + exp) --
    def create_pending_2fa_token(
        self, user_id: int, company_id: int | None
    ) -> str:
        exp = int(datetime.now(timezone.utc).timestamp()) + self.PENDING_2FA_TTL_SECONDS
        payload = f"2fa:{user_id}:{company_id if company_id is not None else ''}:{exp}"
        signature = hmac.new(
            config.JWT_SECRET.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        raw = f"{payload}:{signature}"
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    def verify_pending_2fa_token(self, token: str) -> dict[str, Any] | None:
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            marker, user_id_text, company_text, exp_text, signature = raw.split(":")
        except (ValueError, TypeError, Exception):
            return None
        if marker != "2fa":
            return None
        payload = f"2fa:{user_id_text}:{company_text}:{exp_text}"
        expected = hmac.new(
            config.JWT_SECRET.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        try:
            exp = int(exp_text)
        except ValueError:
            return None
        if exp < int(datetime.now(timezone.utc).timestamp()):
            return None
        return {
            "user_id": int(user_id_text),
            "company_id": int(company_text) if company_text else None,
        }


auth_service = AuthService()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        security
    ),
) -> dict[str, Any]:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme.",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    user = auth_service.get_user_from_token(
        credentials.credentials
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or token is invalid.",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    user["_raw_token"] = credentials.credentials

    return user