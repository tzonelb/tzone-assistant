import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from config.settings import config
from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_license_code() -> str:
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous chars (0/O, 1/I)
    groups = ["".join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return f"TZ-{'-'.join(groups)}"


class PlatformAdminService:
    """Super-admin-only operations across every company on the platform.

    Every method here is a platform-wide operation (no company_id scoping
    on the caller side) — routes calling this MUST verify
    current_user["is_super_admin"] before calling anything here. This
    service does not check that itself; it assumes the caller already did.
    """

    def ensure_schema(self) -> None:
        """Adds the license/admin-contact columns this feature needs.
        Additive only — never touches existing columns or data."""
        with db.connect() as conn:
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(companies)").fetchall()
            }
            new_columns = {
                "main_admin_email": "TEXT",
                "contact_phone": "TEXT",
                "license_code": "TEXT",
                "purchased_at": "TEXT",
            }
            for column, column_type in new_columns.items():
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE companies ADD COLUMN {column} {column_type}")
            conn.commit()

    # ---- Companies -------------------------------------------------

    def list_companies(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT
                c.id, c.name, c.slug, c.country, c.currency, c.status,
                c.created_at,
                c.main_admin_email, c.contact_phone, c.license_code, c.purchased_at,
                s.id AS subscription_id, s.status AS subscription_status,
                s.expires_at, s.auto_renew,
                p.id AS plan_id, p.name AS plan_name, p.code AS plan_code,
                p.price_monthly, p.currency AS plan_currency,
                p.max_users, p.max_channel_accounts,
                (SELECT COUNT(*) FROM company_users cu
                    WHERE cu.company_id = c.id AND cu.status = 'active') AS active_users,
                (SELECT COUNT(*) FROM channel_accounts ca
                    WHERE ca.company_id = c.id AND ca.status = 'active') AS active_channels
            FROM companies c
            LEFT JOIN subscriptions s
                ON s.company_id = c.id AND s.status IN ('active', 'trialing', 'past_due')
            LEFT JOIN plans p ON p.id = s.plan_id
        """
        params: list[Any] = []
        if status:
            query += " WHERE c.status = ?"
            params.append(status)
        query += " ORDER BY c.created_at DESC"

        with db.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_company_detail(self, *, company_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            company = conn.execute(
                "SELECT * FROM companies WHERE id = ?", (company_id,),
            ).fetchone()
            if not company:
                raise KeyError("Company not found")

            subscription = conn.execute(
                """
                SELECT s.*, p.name AS plan_name, p.code AS plan_code,
                       p.price_monthly, p.max_users, p.max_channel_accounts,
                       p.max_ai_messages, p.max_knowledge_items
                FROM subscriptions s
                JOIN plans p ON p.id = s.plan_id
                WHERE s.company_id = ?
                ORDER BY s.created_at DESC LIMIT 1
                """,
                (company_id,),
            ).fetchone()

            users = conn.execute(
                """
                SELECT u.id, u.email, u.full_name, u.status, cu.status AS membership_status
                FROM company_users cu
                JOIN users u ON u.id = cu.user_id
                WHERE cu.company_id = ?
                ORDER BY u.full_name
                """,
                (company_id,),
            ).fetchall()

            usage_this_month = conn.execute(
                """
                SELECT usage_type, SUM(quantity) AS total_quantity, SUM(cost) AS total_cost
                FROM usage_records
                WHERE company_id = ?
                  AND created_at >= date('now', 'start of month')
                GROUP BY usage_type
                """,
                (company_id,),
            ).fetchall()

        result = dict(company)
        result["subscription"] = dict(subscription) if subscription else None
        result["users"] = [dict(u) for u in users]
        result["usage_this_month"] = [dict(u) for u in usage_this_month]
        return result

    def create_company(
        self,
        *,
        name: str,
        slug: str,
        country: str | None = None,
        currency: str = "USD",
        plan_id: int | None = None,
        trial_days: int = 14,
        main_admin_email: str | None = None,
        contact_phone: str | None = None,
        license_code: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        resolved_license_code = license_code or _generate_license_code()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM companies WHERE workspace_id = ? AND slug = ?",
                (config.DEFAULT_WORKSPACE_ID, slug),
            ).fetchone()
            if existing:
                raise ValueError(f"A company with slug '{slug}' already exists")

            existing_license = conn.execute(
                "SELECT id FROM companies WHERE license_code = ?", (resolved_license_code,),
            ).fetchone()
            if existing_license:
                raise ValueError(f"License code '{resolved_license_code}' is already in use")

            cursor = conn.execute(
                """
                INSERT INTO companies (
                    workspace_id, name, slug, country, currency, status,
                    main_admin_email, contact_phone, license_code, purchased_at,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    config.DEFAULT_WORKSPACE_ID, name, slug, country, currency,
                    main_admin_email, contact_phone, resolved_license_code, now,
                    now, now,
                ),
            )
            company_id = int(cursor.lastrowid)

            if plan_id:
                plan = conn.execute("SELECT id FROM plans WHERE id = ?", (plan_id,)).fetchone()
                if not plan:
                    raise ValueError(f"Plan {plan_id} does not exist")
                starts_at = now
                expires_at = (datetime.now(timezone.utc) + timedelta(days=trial_days)).isoformat()
                conn.execute(
                    """
                    INSERT INTO subscriptions (
                        company_id, plan_id, status, starts_at, expires_at, auto_renew, created_at, updated_at
                    ) VALUES (?, ?, 'trialing', ?, ?, 0, ?, ?)
                    """,
                    (company_id, plan_id, starts_at, expires_at, now, now),
                )

            conn.execute(
                """
                INSERT INTO audit_logs (workspace_id, company_id, action, entity_type, created_at)
                VALUES (?, ?, 'company_created', 'company', ?)
                """,
                (config.DEFAULT_WORKSPACE_ID, company_id, now),
            )
            conn.commit()

        return self.get_company_detail(company_id=company_id)

    def set_company_status(
        self, *, company_id: int, status: str, actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        if status not in {"active", "suspended", "cancelled"}:
            raise ValueError(f"Invalid company status: {status}")

        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,)).fetchone()
            if not existing:
                raise KeyError("Company not found")

            conn.execute(
                "UPDATE companies SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, company_id),
            )
            conn.execute(
                """
                INSERT INTO audit_logs (workspace_id, company_id, user_id, action, entity_type, created_at)
                VALUES (?, ?, ?, ?, 'company', ?)
                """,
                (config.DEFAULT_WORKSPACE_ID, company_id, actor_user_id, f"company_status_set_{status}", now),
            )
            conn.commit()

        return self.get_company_detail(company_id=company_id)

    def list_plans(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM plans"
        params: list[Any] = []
        if active_only:
            query += " WHERE status = ?"
            params.append("active")
        query += " ORDER BY price_monthly ASC"
        with db.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def create_plan(
        self,
        *,
        name: str,
        code: str,
        price_monthly: float = 0,
        currency: str = "USD",
        max_users: int = 1,
        max_channel_accounts: int = 1,
        max_ai_messages: int = 500,
        max_knowledge_items: int = 100,
        voice_ai_enabled: bool = False,
        image_ai_enabled: bool = False,
        accounting_connector_enabled: bool = False,
        product_connector_enabled: bool = False,
    ) -> dict[str, Any]:
        with db.connect() as conn:
            existing = conn.execute("SELECT id FROM plans WHERE code = ?", (code,)).fetchone()
            if existing:
                raise ValueError(f"A plan with code '{code}' already exists")

            cursor = conn.execute(
                """
                INSERT INTO plans (
                    name, code, price_monthly, currency,
                    max_users, max_channel_accounts, max_ai_messages, max_knowledge_items,
                    voice_ai_enabled, image_ai_enabled, accounting_connector_enabled, product_connector_enabled,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    name, code, price_monthly, currency,
                    max_users, max_channel_accounts, max_ai_messages, max_knowledge_items,
                    int(voice_ai_enabled), int(image_ai_enabled),
                    int(accounting_connector_enabled), int(product_connector_enabled),
                ),
            )
            plan_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_plan(plan_id=plan_id)

    def get_plan(self, *, plan_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            raise KeyError("Plan not found")
        return dict(row)

    _PLAN_UPDATABLE_FIELDS = {
        "name", "price_monthly", "currency",
        "max_users", "max_channel_accounts", "max_ai_messages", "max_knowledge_items",
        "voice_ai_enabled", "image_ai_enabled",
        "accounting_connector_enabled", "product_connector_enabled",
        "status",
    }
    _PLAN_BOOL_FIELDS = {
        "voice_ai_enabled", "image_ai_enabled",
        "accounting_connector_enabled", "product_connector_enabled",
    }

    def update_plan(self, *, plan_id: int, values: dict[str, Any]) -> dict[str, Any]:
        updates = {k: v for k, v in values.items() if k in self._PLAN_UPDATABLE_FIELDS}
        if not updates:
            return self.get_plan(plan_id=plan_id)

        set_clauses = []
        params: list[Any] = []
        for field, value in updates.items():
            set_clauses.append(f"{field} = ?")
            params.append(int(value) if field in self._PLAN_BOOL_FIELDS else value)
        params.append(plan_id)

        with db.connect() as conn:
            existing = conn.execute("SELECT id FROM plans WHERE id = ?", (plan_id,)).fetchone()
            if not existing:
                raise KeyError("Plan not found")
            conn.execute(f"UPDATE plans SET {', '.join(set_clauses)} WHERE id = ?", params)
            conn.commit()

        return self.get_plan(plan_id=plan_id)

    def get_active_subscription_limits(self, *, company_id: int) -> dict[str, Any] | None:
        """Returns the plan limits/features for a company's current active
        (or trialing) subscription, or None if it has none. Used to
        enforce max_users / max_channel_accounts / feature flags
        elsewhere in the app."""
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT p.*
                FROM subscriptions s
                JOIN plans p ON p.id = s.plan_id
                WHERE s.company_id = ?
                  AND s.status IN ('active', 'trialing')
                ORDER BY s.created_at DESC
                LIMIT 1
                """,
                (company_id,),
            ).fetchone()
        return dict(row) if row else None

    # ---- Subscriptions -------------------------------------------------

    def change_plan(
        self, *, company_id: int, plan_id: int, duration_days: int = 30,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with db.connect() as conn:
            company = conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,)).fetchone()
            if not company:
                raise KeyError("Company not found")
            plan = conn.execute("SELECT id FROM plans WHERE id = ?", (plan_id,)).fetchone()
            if not plan:
                raise KeyError("Plan not found")

            conn.execute(
                "UPDATE subscriptions SET status = 'cancelled', cancelled_at = ?, updated_at = ? "
                "WHERE company_id = ? AND status IN ('active', 'trialing', 'past_due')",
                (now, now, company_id),
            )

            expires_at = (datetime.now(timezone.utc) + timedelta(days=duration_days)).isoformat()
            conn.execute(
                """
                INSERT INTO subscriptions (
                    company_id, plan_id, status, starts_at, expires_at, auto_renew, created_at, updated_at
                ) VALUES (?, ?, 'active', ?, ?, 0, ?, ?)
                """,
                (company_id, plan_id, now, expires_at, now, now),
            )
            conn.execute(
                """
                INSERT INTO audit_logs (workspace_id, company_id, action, entity_type, created_at)
                VALUES (?, ?, 'subscription_plan_changed', 'subscription', ?)
                """,
                (config.DEFAULT_WORKSPACE_ID, company_id, now),
            )
            conn.commit()

        return self.get_company_detail(company_id=company_id)

    # ---- Platform-wide usage summary -------------------------------------------------

    def platform_usage_summary(self) -> dict[str, Any]:
        with db.connect() as conn:
            totals = conn.execute(
                """
                SELECT usage_type, SUM(quantity) AS total_quantity, SUM(cost) AS total_cost
                FROM usage_records
                WHERE created_at >= date('now', 'start of month')
                GROUP BY usage_type
                """
            ).fetchall()
            company_counts = conn.execute(
                "SELECT status, COUNT(*) AS total FROM companies GROUP BY status"
            ).fetchall()
        return {
            "usage_this_month": [dict(row) for row in totals],
            "companies_by_status": {row["status"]: row["total"] for row in company_counts},
        }


platform_admin_service = PlatformAdminService()
