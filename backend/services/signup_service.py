import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.auth_service import auth_service
from backend.services.email_service import send_email
from backend.services.license_key_service import license_key_service
from backend.services.platform_admin_service import platform_admin_service
from config.settings import config
from database.database import db


CODE_TTL_MINUTES = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_code(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _slugify(value: str) -> str:
    """lowercase-hyphenated slug from a company name."""
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value


class SignupService:
    """Public self-service registration: create a company, seed its owner
    role, create the owner user, assign them, and start a trial subscription.

    Companies other than the seeded company_id=1 have ZERO rows in `roles`,
    so this flow must insert an 'owner' role for the brand-new company before
    calling auth_service.assign_user_to_company (which looks the role up by
    company_id + code and raises ValueError when missing).
    """

    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signup_email_verifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def send_verification_code(self, email: str) -> tuple[bool, str]:
        """Emails a 6-digit code to confirm this is a real, reachable
        address BEFORE creating an account with it. No user row exists yet
        at this point, so verification is keyed by the email itself."""
        email = (email or "").strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise ValueError("Please enter a valid email address.")

        normalized = auth_service.normalize_email(email)
        with db.connect() as conn:
            existing_user = conn.execute(
                "SELECT id FROM users WHERE LOWER(email) = ? LIMIT 1", (normalized,),
            ).fetchone()
            if existing_user:
                raise ValueError("A user with this email already exists.")

        code = f"{secrets.randbelow(1000000):06d}"
        now = _utc_now()
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO signup_email_verifications (email, code_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized, _hash_code(code), (now + timedelta(minutes=CODE_TTL_MINUTES)).isoformat(), now.isoformat()),
            )
            conn.commit()

        return send_email(
            to_email=email,
            subject="Confirm your email for T-ZONE",
            body=(
                f"Your T-ZONE sign-up verification code is: {code}\n\n"
                f"It expires in {CODE_TTL_MINUTES} minutes. "
                f"If you didn't request this, you can ignore this email."
            ),
        )

    def _consume_verification_code(self, *, email: str, code: str) -> None:
        normalized = auth_service.normalize_email(email)
        now = _utc_now()
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT id, code_hash, expires_at FROM signup_email_verifications
                WHERE email = ? AND consumed_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            if not row:
                raise ValueError("No verification code was requested for this email. Please request one first.")
            if row["code_hash"] != _hash_code(code or ""):
                raise ValueError("Incorrect verification code.")
            if datetime.fromisoformat(row["expires_at"]) < now:
                raise ValueError("This verification code has expired. Please request a new one.")
            conn.execute(
                "UPDATE signup_email_verifications SET consumed_at = ? WHERE id = ?",
                (now.isoformat(), row["id"]),
            )
            conn.commit()

    def _unique_slug(self, conn, base: str) -> str:
        base = base or "company"
        candidate = base
        suffix = 2
        while True:
            existing = conn.execute(
                "SELECT id FROM companies WHERE workspace_id = ? AND slug = ?",
                (config.DEFAULT_WORKSPACE_ID, candidate),
            ).fetchone()
            if not existing:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1

    def _default_plan_id(self, plan_id: int | None) -> int | None:
        active_plans = platform_admin_service.list_plans(active_only=True)
        if plan_id is not None:
            match = next((p for p in active_plans if p["id"] == plan_id), None)
            if not match:
                raise ValueError("The selected plan is not available.")
            return plan_id
        if not active_plans:
            return None
        # Default to the cheapest *paid* plan (the intended entry tier), not
        # simply the lowest price — a $0 plan is often a mis-seeded/"custom"
        # tier (e.g. Enterprise) that should never be the silent default.
        # Fall back to the absolute cheapest only if every plan is free.
        paid = [p for p in active_plans if (p.get("price_monthly") or 0) > 0]
        return (paid[0] if paid else active_plans[0])["id"]

    def signup(
        self,
        *,
        company_name: str,
        owner_full_name: str,
        owner_email: str,
        password: str,
        confirm_password: str | None = None,
        slug: str | None = None,
        plan_id: int | None = None,
        license_key: str | None = None,
        email_code: str | None = None,
        country: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        company_name = (company_name or "").strip()
        owner_full_name = (owner_full_name or "").strip()
        owner_email = (owner_email or "").strip()
        license_key = (license_key or "").strip() or None

        if not company_name:
            raise ValueError("Company name is required.")
        if not owner_full_name:
            raise ValueError("Your full name is required.")
        if not owner_email:
            raise ValueError("A work email is required.")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", owner_email):
            raise ValueError("Please enter a valid email address.")
        if len(password or "") < 8:
            raise ValueError("Password must contain at least 8 characters.")
        if confirm_password is not None and password != confirm_password:
            raise ValueError("Passwords do not match.")

        normalized_email = auth_service.normalize_email(owner_email)
        with db.connect() as conn:
            existing_user = conn.execute(
                "SELECT id FROM users WHERE LOWER(email) = ? LIMIT 1", (normalized_email,),
            ).fetchone()
            if existing_user:
                raise ValueError("A user with this email already exists.")

        # Confirm this is a real, reachable address before creating anything
        # else — checked after the duplicate-email check above so a doomed
        # request doesn't burn a valid, still-usable verification code.
        self._consume_verification_code(email=owner_email, code=email_code or "")

        # A license key (already purchased, e.g. through a reseller) carries
        # its own plan and overrides whatever the plan grid had selected.
        # Redeeming it happens AFTER the company is created, once we have a
        # company_id to attach it to; here we only validate + peek the plan.
        if license_key:
            resolved_plan_id = license_key_service.peek_plan_id(license_key)
        else:
            resolved_plan_id = self._default_plan_id(plan_id)

        # Slug resolution (duplicate-email already checked above).
        with db.connect() as conn:
            base_slug = _slugify(slug) or _slugify(company_name)
            resolved_slug = self._unique_slug(conn, base_slug)

        # 1) Create the company (also inserts a trialing subscription row when
        #    a plan is given, plus an audit log row).
        company = platform_admin_service.create_company(
            name=company_name,
            slug=resolved_slug,
            country=country,
            plan_id=resolved_plan_id,
            main_admin_email=normalized_email,
            contact_phone=phone,
        )
        company_id = int(company["id"])

        if license_key:
            # Re-check-and-redeem atomically-enough for this scale: the peek
            # above already validated it was unused; redeem() re-checks the
            # status itself so a double-submit can't redeem the same key twice.
            license_key_service.redeem(code=license_key, company_id=company_id)

        # 2) Seed the 'owner' role for the brand-new company (mirrors the
        #    company_id=1 seed in database.database).
        with db.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO roles (
                    company_id, name, code, description, is_system
                ) VALUES (?, 'Owner', 'owner', 'Full access to the company', 1)
                """,
                (company_id,),
            )
            conn.commit()

        # 3) Create the owner user.
        user_id = auth_service.create_user(
            email=normalized_email,
            password=password,
            full_name=owner_full_name,
            phone=phone,
            is_super_admin=False,
        )

        # 4) Assign the owner to the company (raises ValueError if the owner
        #    role is missing — which is why step 2 is mandatory).
        auth_service.assign_user_to_company(
            user_id=user_id,
            company_id=company_id,
            role_code="owner",
            branch_id=config.DEFAULT_BRANCH_ID,
        )

        # Build the same response shape the login endpoint returns so the
        # frontend can log the new owner straight in.
        with db.connect() as conn:
            user_row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()

        safe_user = auth_service.sanitize_user(dict(user_row))
        safe_user["active_company_id"] = company_id
        safe_user["active_company_name"] = company["name"]
        safe_user["active_company_slug"] = company["slug"]

        session_data = auth_service.create_session(
            user_id=user_id,
            company_id=company_id,
        )

        return {
            "access_token": session_data["access_token"],
            "token_type": "bearer",
            "expires_in": session_data["expires_in"],
            "user": safe_user,
        }

    def public_plans(self) -> list[dict[str, Any]]:
        """Active plans, trimmed to the fields the public signup page needs."""
        plans = platform_admin_service.list_plans(active_only=True)
        fields = (
            "id", "name", "code", "price_monthly", "currency",
            "max_users", "max_channel_accounts", "max_ai_messages",
            "max_knowledge_items", "voice_ai_enabled", "image_ai_enabled",
        )
        return [{k: plan.get(k) for k in fields} for plan in plans]


signup_service = SignupService()
