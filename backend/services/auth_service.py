
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

            password_ok = self.verify_password(
                password,
                user_data.get("password_hash"),
            )
            print("PASSWORD OK:", password_ok)

            if not password_ok:
                print("FAILED: BAD PASSWORD")
                return None

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
                    roles.code AS role_code
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

            companies = [dict(row) for row in rows]

            all_permission_codes: list[str] | None = None

            for company in companies:
                if company.get("role_code") == "owner":
                    if all_permission_codes is None:
                        all_permission_codes = [
                            row["code"]
                            for row in conn.execute(
                                "SELECT code FROM permissions ORDER BY code"
                            ).fetchall()
                        ]
                    company["permission_codes"] = list(all_permission_codes)
                elif company.get("role_id"):
                    company["permission_codes"] = [
                        row["code"]
                        for row in conn.execute("""
                            SELECT permissions.code
                            FROM role_permissions
                            JOIN permissions
                                ON permissions.id = role_permissions.permission_id
                            WHERE role_permissions.role_id = ?
                            ORDER BY permissions.code
                        """, (company["role_id"],)).fetchall()
                    ]
                else:
                    company["permission_codes"] = []

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

    def get_permission_codes(
        self,
        user_id: int,
        company_id: int | None,
        is_super_admin: bool = False,
    ) -> list[str]:
        """Full list of permission codes the user holds for the given
        company. Super admins and the owner role hold every permission
        (mirrors the bypasses in has_permission), returned as "*"."""
        if is_super_admin:
            return ["*"]

        if company_id is None:
            return []

        with db.connect() as conn:
            role = conn.execute("""
                SELECT roles.id, roles.code
                FROM company_users
                JOIN roles ON roles.id = company_users.role_id
                WHERE company_users.user_id = ?
                  AND company_users.company_id = ?
                  AND company_users.status = 'active'
                LIMIT 1
            """, (user_id, company_id)).fetchone()

            if not role:
                return []

            if role["code"] == "owner":
                return ["*"]

            rows = conn.execute("""
                SELECT permissions.code
                FROM role_permissions
                JOIN permissions ON permissions.id = role_permissions.permission_id
                WHERE role_permissions.role_id = ?
            """, (role["id"],)).fetchall()

            return [row["code"] for row in rows]

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