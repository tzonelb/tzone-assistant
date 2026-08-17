"""The Super Admin control plane: managing companies without reading them.

A platform administrator runs the business of the platform — creating companies,
suspending them, assigning plans, deciding which modules a company sees. That is
a different job from working inside a company, and this service is written so it
stays a different job.

THE RULE THIS MODULE EXISTS TO KEEP
-----------------------------------
Every read and every write here goes to ``database_manager.control()``. The
control plane holds what the platform must know before it knows whose request it
is: companies, workspaces, users, plans, sealed key material, audit. It holds no
customer content at all.

There is exactly one exception, :meth:`PlatformService.company_statistics`, and
it is deliberately the narrowest one that still answers "how big is this
company": ``COUNT(*)`` aggregates and the size of the file on disk. It returns
numbers and nothing else. Read the comment on that method before changing it.

That is not a coding convention, it is the product decision the encryption is
built around. Each company's database is sealed with its own key, and an
employee proves possession of the workspace code at login. If the platform
console could list a company's conversations, the operator would be one screen
away from everything the customers wrote, and the per-company encryption would
only be protecting customers from a stolen disk.

Table creation belongs to ``database/schema_control.py`` alone. This service only
reads and writes.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.security import keyring
from database.manager import DatabaseError, database_manager


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlatformError(RuntimeError):
    """A control-plane operation that failed for a reason worth showing."""


class PlatformNotFound(PlatformError):
    """The company, plan or user named does not exist."""


class PlatformConflict(PlatformError):
    """The request contradicts the current state and was refused."""


# ----------------------------------------------------------------------
# What the Super Admin is allowed to switch on and off
# ----------------------------------------------------------------------

# The real modules of the customer workspace, taken from the navigation in
# frontend/src/components/layout/Sidebar.jsx. Unknown keys are refused rather
# than stored: a config holding "conversatoins": false disables nothing, reads
# back to the console looking like a decision that was made, and is only ever
# discovered by the company wondering why the module is still there.
PLATFORM_MODULES: tuple[str, ...] = (
    "dashboard",
    "notifications",
    "conversations",
    "comments",
    "customers",
    "appointments",
    "tasks",
    "catalogue",
    "scheduler",
    "team_chat",
    "knowledge",
    "ai_teaching",
    "analytics",
    "channels",
    "roles",
    "company_settings",
    "preferences",
)

# Brand name and theme tokens. Same reasoning as the modules: a typo here is a
# colour that silently never applies.
BRANDING_FIELDS: tuple[str, ...] = (
    "brand_name",
    "tagline",
    "logo_url",
    "primary_color",
    "accent_color",
    "sidebar_color",
    "surface_color",
    "text_color",
)

_COLOR_FIELDS = tuple(field for field in BRANDING_FIELDS if field.endswith("_color"))
_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# Layout flags the customer interface reads at sign-in.
LAYOUT_FLAGS: tuple[str, ...] = (
    "sidebar_collapsed",
    "sidebar_hover_expand",
    "dense_tables",
    "show_section_titles",
    "fullscreen_conversations",
    "fullscreen_comments",
    "show_brand_footer",
)

COMPANY_STATUSES: tuple[str, ...] = ("active", "suspended")

MAX_BRANDING_VALUE = 200


class PlatformService:
    MAX_AUDIT_LIMIT = 200
    RECENT_FAILED_LOGIN_LIMIT = 20

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    @staticmethod
    def slugify(value: str) -> str:
        """Fold a display name into a URL-safe slug, the same way the CLI does."""
        slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")

        if not slug:
            raise PlatformError(
                f"Cannot build a slug from {value!r}. Use letters or digits."
            )

        return slug

    @staticmethod
    def _loads(raw: Any, default: Any = None) -> Any:
        try:
            value = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            value = None

        return value if isinstance(value, dict) else (default if default is not None else {})

    def _database_bytes(self, company_id: int) -> int:
        """Bytes this company occupies on disk, sidecars included.

        The size of an encrypted file is not its content: it is a number the
        operator needs for capacity and billing, and it can be read without
        opening the database at all.
        """
        path = database_manager.tenant_path(int(company_id))
        total = 0

        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                total += candidate.stat().st_size

        return total

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def record_audit(
        self,
        *,
        action: str,
        actor_user_id: int | None = None,
        company_id: int | None = None,
        workspace_id: int | None = None,
        target_type: str | None = None,
        target_id: Any = None,
        data: dict[str, Any] | None = None,
        ip_address: str | None = None,
        conn: Any = None,
    ) -> None:
        """Write one row to ``audit_log``. Every mutation here calls this.

        ``conn`` lets a caller record inside a transaction it already holds, so
        the audit row and the change it describes commit together.

        Nothing customer-owned may be put in ``data``: this table is shared
        across companies, and a "helpful" copy of a customer's message here would
        walk straight around the tenant boundary the rest of this file keeps.
        """
        payload = (
            json.dumps(data, ensure_ascii=False, default=str) if data else None
        )

        def write(connection: Any) -> None:
            connection.execute(
                """
                INSERT INTO audit_log (
                    workspace_id, company_id, actor_user_id, action,
                    target_type, target_id, data_json, ip_address, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(workspace_id) if workspace_id is not None else None,
                    int(company_id) if company_id is not None else None,
                    int(actor_user_id) if actor_user_id is not None else None,
                    str(action),
                    target_type,
                    str(target_id) if target_id is not None else None,
                    payload,
                    ip_address,
                    utc_now_iso(),
                ),
            )

        if conn is not None:
            write(conn)
            return

        with database_manager.control() as connection:
            write(connection)
            connection.commit()

    def list_audit(
        self,
        *,
        company_id: int | None = None,
        action: str | None = None,
        actor_user_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), self.MAX_AUDIT_LIMIT))
        offset = max(0, int(offset))

        where: list[str] = []
        params: list[Any] = []

        if company_id is not None:
            where.append("audit_log.company_id = ?")
            params.append(int(company_id))

        if self._clean(action):
            where.append("audit_log.action = ?")
            params.append(self._clean(action))

        if actor_user_id is not None:
            where.append("audit_log.actor_user_id = ?")
            params.append(int(actor_user_id))

        clause = f"WHERE {' AND '.join(where)}" if where else ""

        with database_manager.control() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS total FROM audit_log {clause}", params
                ).fetchone()["total"]
            )
            rows = conn.execute(
                f"""
                SELECT
                    audit_log.*,
                    users.email AS actor_email,
                    companies.name AS company_name
                FROM audit_log
                LEFT JOIN users ON users.id = audit_log.actor_user_id
                LEFT JOIN companies ON companies.id = audit_log.company_id
                {clause}
                ORDER BY audit_log.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        items = []

        for row in rows:
            entry = dict(row)
            entry["data"] = self._loads(entry.pop("data_json", None))
            items.append(entry)

        return {"items": items, "total": total}

    # ------------------------------------------------------------------
    # Companies
    # ------------------------------------------------------------------

    def _owner_by_company(self, conn: Any) -> dict[int, dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT
                company_users.company_id AS company_id,
                users.id AS owner_user_id,
                users.email AS owner_email,
                users.full_name AS owner_name
            FROM company_users
            JOIN users ON users.id = company_users.user_id
            JOIN roles ON roles.id = company_users.role_id
            WHERE roles.code = 'owner'
              AND company_users.status = 'active'
            ORDER BY company_users.id ASC
            """
        ).fetchall()

        owners: dict[int, dict[str, Any]] = {}

        for row in rows:
            owners.setdefault(int(row["company_id"]), dict(row))

        return owners

    def _plan_by_company(self, conn: Any) -> dict[int, dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT
                subscriptions.company_id AS company_id,
                subscriptions.id AS subscription_id,
                subscriptions.status AS subscription_status,
                subscriptions.starts_at,
                subscriptions.expires_at,
                plans.code AS plan_code,
                plans.name AS plan_name,
                plans.price_monthly
            FROM subscriptions
            JOIN plans ON plans.id = subscriptions.plan_id
            WHERE subscriptions.status = 'active'
            ORDER BY subscriptions.id DESC
            """
        ).fetchall()

        plans: dict[int, dict[str, Any]] = {}

        for row in rows:
            plans.setdefault(int(row["company_id"]), dict(row))

        return plans

    def _company_rows(self, conn: Any, company_id: int | None = None) -> list[Any]:
        clause = "WHERE companies.id = ?" if company_id is not None else ""
        params = [int(company_id)] if company_id is not None else []

        return conn.execute(
            f"""
            SELECT
                companies.id,
                companies.name,
                companies.slug,
                companies.status,
                companies.country,
                companies.currency,
                companies.timezone,
                companies.default_language,
                companies.created_at,
                companies.updated_at,
                workspaces.id AS workspace_id,
                workspaces.name AS workspace_name,
                workspaces.slug AS workspace_slug,
                workspaces.status AS workspace_status,
                company_databases.database_filename,
                company_databases.schema_version,
                company_databases.code_rotated_at,
                company_databases.created_at AS provisioned_at
            FROM companies
            LEFT JOIN workspaces ON workspaces.id = companies.workspace_id
            LEFT JOIN company_databases
                ON company_databases.company_id = companies.id
            {clause}
            ORDER BY companies.id ASC
            """,
            params,
        ).fetchall()

    def _decorate_company(
        self,
        row: Any,
        owners: dict[int, dict[str, Any]],
        plans: dict[int, dict[str, Any]],
    ) -> dict[str, Any]:
        company = dict(row)
        company_id = int(company["id"])

        owner = owners.get(company_id, {})
        company["owner_user_id"] = owner.get("owner_user_id")
        company["owner_email"] = owner.get("owner_email")
        company["owner_name"] = owner.get("owner_name")

        plan = plans.get(company_id)
        company["plan_code"] = plan["plan_code"] if plan else None
        company["plan_name"] = plan["plan_name"] if plan else None
        company["plan_expires_at"] = plan["expires_at"] if plan else None

        path = database_manager.tenant_path(company_id)
        company["database_registered"] = bool(company.get("database_filename"))
        company["database_exists"] = path.exists()
        company["database_bytes"] = self._database_bytes(company_id)

        return company

    def list_companies(self) -> list[dict[str, Any]]:
        """Every company on the platform, from the control plane alone.

        Deliberately lists suspended companies too: a console that hides what it
        suspended is a console an operator cannot use to put it back.
        """
        with database_manager.control() as conn:
            rows = self._company_rows(conn)
            owners = self._owner_by_company(conn)
            plans = self._plan_by_company(conn)

        return [self._decorate_company(row, owners, plans) for row in rows]

    def company_detail(self, company_id: int) -> dict[str, Any]:
        """One company: its control-plane record, its config and its size."""
        company_id = int(company_id)

        with database_manager.control() as conn:
            rows = self._company_rows(conn, company_id)

            if not rows:
                raise PlatformNotFound(f"No company with id {company_id}.")

            owners = self._owner_by_company(conn)
            plans = self._plan_by_company(conn)

            employees = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM company_users
                    WHERE company_id = ? AND status = 'active'
                    """,
                    (company_id,),
                ).fetchone()["total"]
            )

        company = self._decorate_company(rows[0], owners, plans)
        company["employee_count"] = employees

        statistics: dict[str, int] | None = None
        statistics_error: str | None = None

        try:
            statistics = self.company_statistics(company_id)
        except (PlatformError, DatabaseError) as exc:
            # A company whose file is missing or unreadable must still be
            # listable and suspendable, or the console is useless in exactly the
            # situation it is needed.
            statistics_error = str(exc)

        return {
            "company": company,
            "statistics": statistics,
            "statistics_error": statistics_error,
            "platform_config": self.get_platform_config(company_id),
        }

    # ------------------------------------------------------------------
    # The one tenant-facing method
    # ------------------------------------------------------------------

    # Aggregated from fixed table names, never from anything a caller supplies.
    STATISTIC_TABLES: dict[str, str] = {
        "conversations": "conversations",
        "messages": "messages",
        "customers": "customers",
        "tickets": "tickets",
        "products": "products",
        "knowledge_items": "knowledge_items",
    }

    def company_statistics(self, company_id: int) -> dict[str, int]:
        """Count rows in one company's database and measure its file.

        THIS IS THE ONLY FUNCTION IN THIS SERVICE THAT OPENS A TENANT DATABASE.
        It runs COUNT(*) aggregates and reads the file size. It returns NUMBERS
        ONLY. It must never select a row body, a name, a phone number, a
        message, or any other customer content.

        Why it is written this narrowly: a platform administrator manages
        companies but cannot read their customer data. The per-company
        encryption is meant to protect the tenant even from the platform
        operator, and the operator is precisely the person with the master key
        that opens every file. So the boundary cannot be the key — it has to be
        the code, and this is the one place the code crosses. A count tells the
        operator a company is alive and how much it is storing, which is what
        capacity, billing and support actually need; a row would tell them what
        a customer said, which is not theirs to know.

        If a future screen here wants a name, a subject line, a "recent
        activity" list or "just the first message" — that is the boundary
        breaking. The company's own signed-in employees can see those things
        through the customer API with their workspace code. The console cannot.
        """
        company_id = int(company_id)

        counts: dict[str, int] = {}

        try:
            with database_manager.tenant(company_id) as conn:
                for label, table in self.STATISTIC_TABLES.items():
                    counts[label] = int(
                        conn.execute(
                            f"SELECT COUNT(*) AS total FROM {table}"
                        ).fetchone()["total"]
                    )
        except DatabaseError as exc:
            raise PlatformError(
                f"Company {company_id}: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - a broken file must not 500 the console
            raise PlatformError(
                f"Company {company_id} database could not be read: {exc}"
            ) from exc

        counts["database_bytes"] = self._database_bytes(company_id)

        # Numbers only, asserted rather than assumed: a later edit that returns a
        # row here fails loudly instead of quietly leaking it.
        for key, value in counts.items():
            if not isinstance(value, int) or isinstance(value, bool):
                raise PlatformError(
                    f"company_statistics tried to return a non-numeric value "
                    f"for {key!r}. This function returns counts only."
                )

        return counts

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    def _find_or_create_workspace(
        self, conn: Any, *, name: str, owner_email: str
    ) -> tuple[int, bool]:
        slug = self.slugify(name)

        row = conn.execute(
            "SELECT id FROM workspaces WHERE slug = ? LIMIT 1", (slug,)
        ).fetchone()

        if row:
            return int(row["id"]), False

        now = utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO workspaces (
                name, slug, status, owner_email, created_at, updated_at
            )
            VALUES (?, ?, 'active', ?, ?, ?)
            """,
            (name, slug, owner_email, now, now),
        )

        return int(cursor.lastrowid), True

    def _seed_company_roles(self, conn: Any, company_id: int) -> dict[str, int]:
        """Create this company's roles and wire their permissions.

        Mirrors the sequence in ``tools/manage_platform.py``'s create-company,
        which is the reference implementation: the same roles, the same
        permission wiring, and the same refusal to continue when a role names a
        permission the control database has never heard of.
        """
        from database.schema_control import DEFAULT_ROLES

        now = utc_now_iso()

        permission_ids = {
            row["code"]: int(row["id"])
            for row in conn.execute("SELECT id, code FROM permissions").fetchall()
        }

        role_ids: dict[str, int] = {}

        for name, code, description, permission_codes in DEFAULT_ROLES:
            conn.execute(
                """
                INSERT INTO roles (
                    company_id, name, code, description, is_system, created_at
                )
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(company_id, code) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description
                """,
                (company_id, name, code, description, now),
            )

            row = conn.execute(
                "SELECT id FROM roles WHERE company_id = ? AND code = ? LIMIT 1",
                (company_id, code),
            ).fetchone()

            role_id = int(row["id"])
            role_ids[code] = role_id

            missing = [
                permission_code
                for permission_code in permission_codes
                if permission_code not in permission_ids
            ]

            if missing:
                raise PlatformError(
                    f"Role {code!r} references permissions that are not "
                    f"registered: {', '.join(missing)}."
                )

            conn.executemany(
                """
                INSERT OR IGNORE INTO role_permissions (
                    role_id, permission_id, created_at
                )
                VALUES (?, ?, ?)
                """,
                [
                    (role_id, permission_ids[permission_code], now)
                    for permission_code in permission_codes
                ],
            )

        return role_ids

    def _create_or_reuse_user(
        self,
        conn: Any,
        *,
        email: str,
        full_name: str,
        password_hash: str,
    ) -> tuple[int, bool]:
        normalized = str(email or "").strip().lower()

        if "@" not in normalized:
            raise PlatformError(f"{email!r} is not a valid email address.")

        row = conn.execute(
            "SELECT id FROM users WHERE LOWER(email) = ? LIMIT 1", (normalized,)
        ).fetchone()

        if row:
            return int(row["id"]), False

        now = utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO users (
                email, password_hash, full_name, status,
                is_super_admin, created_at, updated_at
            )
            VALUES (?, ?, ?, 'active', 0, ?, ?)
            """,
            (normalized, password_hash, full_name, now, now),
        )

        return int(cursor.lastrowid), True

    def _rollback_company(self, company_id: int) -> None:
        """Undo a half-finished creation so no orphan can exist.

        A company row without a database serves nothing but blocks its slug, and
        a database file without a company row is unreachable ciphertext nobody
        will ever delete. Either half left behind is worse than a clean failure
        the operator can retry.
        """
        try:
            path = database_manager.tenant_path(company_id)

            with database_manager.control() as conn:
                conn.execute(
                    "DELETE FROM company_databases WHERE company_id = ?",
                    (company_id,),
                )
                conn.execute(
                    "DELETE FROM company_platform_config WHERE company_id = ?",
                    (company_id,),
                )
                conn.execute(
                    "DELETE FROM company_users WHERE company_id = ?", (company_id,)
                )
                conn.execute(
                    "DELETE FROM subscriptions WHERE company_id = ?", (company_id,)
                )
                conn.execute("DELETE FROM roles WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
                conn.commit()

            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)

            # Provisioning caches the key it just generated. Dropping it stops a
            # dead company id keeping a key alive in this process.
            cache = getattr(database_manager, "_key_cache", None)
            if isinstance(cache, dict):
                cache.pop(int(company_id), None)
        except Exception:  # noqa: BLE001 - cleanup must never mask the real error
            logger.exception(
                "Cleanup of half-created company %s did not fully succeed",
                company_id,
            )

    def create_company(
        self,
        *,
        name: str,
        slug: str,
        workspace: str,
        owner_email: str,
        owner_name: str,
        owner_password: str,
        country: str | None = None,
        currency: str = "USD",
        timezone_name: str = "Asia/Beirut",
        language: str = "ar",
        plan_code: str | None = None,
        actor_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Provision a company end to end and return its workspace code once.

        The sequence is the one ``tools/manage_platform.py create-company``
        performs, in the same order: workspace, company row, encrypted database,
        roles and their permissions, owner user, owner membership, audit.

        The returned ``workspace_code`` is the only time it exists in readable
        form. It is not stored — only a copy of the company key sealed behind it
        is — so it cannot be shown again from the database, a backup or support.
        """
        from backend.services.auth_service import auth_service

        company_slug = self.slugify(slug)
        normalized_email = str(owner_email or "").strip().lower()

        display_name = self._clean(name)
        if not display_name:
            raise PlatformError("Give this company a name.")

        workspace_name = self._clean(workspace)
        if not workspace_name:
            raise PlatformError("Give this company a workspace name.")

        try:
            password_hash = auth_service.hash_password(owner_password)
        except ValueError as exc:
            raise PlatformError(f"Owner password rejected: {exc}") from exc

        workspace_code = keyring.generate_workspace_code()

        # Step 1: the control-plane rows that must exist before the tenant file.
        with database_manager.control() as conn:
            workspace_id, workspace_created = self._find_or_create_workspace(
                conn, name=workspace_name, owner_email=normalized_email
            )

            existing = conn.execute(
                "SELECT id FROM companies WHERE workspace_id = ? AND slug = ? LIMIT 1",
                (workspace_id, company_slug),
            ).fetchone()

            if existing:
                raise PlatformConflict(
                    f"Workspace {workspace_name!r} already has a company with "
                    f"slug {company_slug!r} (id {int(existing['id'])}). "
                    "Pick another slug."
                )

            now = utc_now_iso()
            cursor = conn.execute(
                """
                INSERT INTO companies (
                    workspace_id, name, slug, country, currency, timezone,
                    default_language, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    workspace_id,
                    display_name,
                    company_slug,
                    self._clean(country),
                    self._clean(currency) or "USD",
                    self._clean(timezone_name) or "Asia/Beirut",
                    self._clean(language) or "ar",
                    now,
                    now,
                ),
            )
            company_id = int(cursor.lastrowid)
            conn.commit()

        # Step 2: everything else. Any failure below removes the company row and
        # deletes the database file, so a half-created company cannot exist and
        # the operator can simply retry the same slug.
        try:
            database_path = database_manager.provision_company(
                company_id=company_id, workspace_code=workspace_code
            )

            with database_manager.control() as conn:
                role_ids = self._seed_company_roles(conn, company_id)

                owner_role_id = role_ids.get("owner")
                if owner_role_id is None:
                    raise PlatformError(
                        "DEFAULT_ROLES does not define an 'owner' role, so the "
                        "first user cannot be given ownership."
                    )

                user_id, user_created = self._create_or_reuse_user(
                    conn,
                    email=normalized_email,
                    full_name=self._clean(owner_name) or normalized_email,
                    password_hash=password_hash,
                )

                conn.execute(
                    """
                    INSERT INTO company_users (
                        company_id, user_id, role_id, status, created_at
                    )
                    VALUES (?, ?, ?, 'active', ?)
                    ON CONFLICT(company_id, user_id) DO UPDATE SET
                        role_id = excluded.role_id,
                        status = 'active'
                    """,
                    (company_id, user_id, owner_role_id, utc_now_iso()),
                )

                self._ensure_platform_config(conn, company_id, actor_user_id)

                self.record_audit(
                    conn=conn,
                    action="company.created",
                    actor_user_id=actor_user_id,
                    company_id=company_id,
                    workspace_id=workspace_id,
                    target_type="company",
                    target_id=company_id,
                    data={
                        "slug": company_slug,
                        "owner_email": normalized_email,
                        "owner_user_id": user_id,
                    },
                    ip_address=ip_address,
                )

                conn.commit()

            if plan_code:
                self.assign_plan(
                    company_id=company_id,
                    plan_code=plan_code,
                    actor_user_id=actor_user_id,
                    ip_address=ip_address,
                )
        except Exception:
            self._rollback_company(company_id)
            raise

        logger.info(
            "Platform admin %s created company id=%s slug=%s",
            actor_user_id,
            company_id,
            company_slug,
        )

        return {
            "company_id": company_id,
            "workspace_id": workspace_id,
            "workspace_created": workspace_created,
            "name": display_name,
            "slug": company_slug,
            "database_path": str(database_path),
            "roles": sorted(role_ids),
            "owner_user_id": user_id,
            "owner_email": normalized_email,
            "owner_user_created": user_created,
            "workspace_code": workspace_code,
            "workspace_code_notice": (
                "This code is shown once. It seals a second copy of the "
                "company's database key and is never stored in readable form, "
                "so it cannot be recovered later — issue a new one instead."
            ),
        }

    # ------------------------------------------------------------------
    # Status, codes and plans
    # ------------------------------------------------------------------

    def set_company_status(
        self,
        company_id: int,
        status: str,
        *,
        actor_user_id: int | None = None,
        ip_address: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Suspend or reactivate a company.

        Suspension has to bite in two places. ``auth_service.authenticate``
        joins on ``companies.status = 'active'``, so a suspended company's
        employees cannot sign in. That alone would leave anyone already holding
        a token working until it expired, so the company's live sessions are
        revoked here too.
        """
        company_id = int(company_id)
        status = str(status or "").strip().lower()

        if status not in COMPANY_STATUSES:
            raise PlatformError(
                f"Status must be one of: {', '.join(COMPANY_STATUSES)}."
            )

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id, name, status FROM companies WHERE id = ? LIMIT 1",
                (company_id,),
            ).fetchone()

            if not row:
                raise PlatformNotFound(f"No company with id {company_id}.")

            previous = str(row["status"])

            conn.execute(
                "UPDATE companies SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now_iso(), company_id),
            )

            revoked = 0

            if status != "active":
                cursor = conn.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = ?
                    WHERE company_id = ? AND revoked_at IS NULL
                    """,
                    (utc_now_iso(), company_id),
                )
                revoked = int(cursor.rowcount or 0)

            self.record_audit(
                conn=conn,
                action=f"company.{'suspended' if status != 'active' else 'reactivated'}",
                actor_user_id=actor_user_id,
                company_id=company_id,
                target_type="company",
                target_id=company_id,
                data={
                    "from": previous,
                    "to": status,
                    "reason": self._clean(reason),
                    "sessions_revoked": revoked,
                },
                ip_address=ip_address,
            )

            conn.commit()

        logger.info(
            "Company %s status %s -> %s by platform admin %s",
            company_id,
            previous,
            status,
            actor_user_id,
        )

        return {
            "company_id": company_id,
            "status": status,
            "previous_status": previous,
            "sessions_revoked": revoked,
        }

    def rotate_workspace_code(
        self,
        company_id: int,
        *,
        actor_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Issue a new workspace code and return it once.

        The company's data is not re-encrypted: only the copy of its key that is
        sealed behind the code is replaced. The old code stops working the
        moment this returns.
        """
        company_id = int(company_id)

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id, name FROM companies WHERE id = ? LIMIT 1", (company_id,)
            ).fetchone()

            if not row:
                raise PlatformNotFound(f"No company with id {company_id}.")

            company_name = str(row["name"])

        new_code = keyring.generate_workspace_code()

        try:
            database_manager.rotate_workspace_code(company_id, new_code)
        except DatabaseError as exc:
            raise PlatformError(str(exc)) from exc

        self.record_audit(
            action="company.workspace_code_rotated",
            actor_user_id=actor_user_id,
            company_id=company_id,
            target_type="company",
            target_id=company_id,
            ip_address=ip_address,
        )

        logger.info(
            "Workspace code rotated for company %s by platform admin %s",
            company_id,
            actor_user_id,
        )

        return {
            "company_id": company_id,
            "company_name": company_name,
            "workspace_code": new_code,
            "workspace_code_notice": (
                "The previous code stopped working immediately. This one is "
                "shown once and cannot be recovered later."
            ),
        }

    def list_plans(self) -> list[dict[str, Any]]:
        with database_manager.control() as conn:
            rows = conn.execute(
                "SELECT * FROM plans ORDER BY price_monthly ASC, id ASC"
            ).fetchall()

        return [dict(row) for row in rows]

    def assign_plan(
        self,
        *,
        company_id: int,
        plan_code: str,
        expires_at: str | None = None,
        actor_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Put a company on a plan, replacing whatever it was on.

        The previous subscription is closed rather than deleted: billing has to
        be able to say what a company was on last month.
        """
        company_id = int(company_id)
        plan_code = str(plan_code or "").strip().lower()

        now = utc_now_iso()

        with database_manager.control() as conn:
            company = conn.execute(
                "SELECT id FROM companies WHERE id = ? LIMIT 1", (company_id,)
            ).fetchone()

            if not company:
                raise PlatformNotFound(f"No company with id {company_id}.")

            plan = conn.execute(
                "SELECT * FROM plans WHERE LOWER(code) = ? LIMIT 1", (plan_code,)
            ).fetchone()

            if not plan:
                raise PlatformNotFound(
                    f"No plan with code {plan_code!r}. Read the available codes "
                    "from list_plans()."
                )

            conn.execute(
                """
                UPDATE subscriptions
                SET status = 'replaced', updated_at = ?
                WHERE company_id = ? AND status = 'active'
                """,
                (now, company_id),
            )

            cursor = conn.execute(
                """
                INSERT INTO subscriptions (
                    company_id, plan_id, status, starts_at, expires_at,
                    auto_renew, created_at, updated_at
                )
                VALUES (?, ?, 'active', ?, ?, 0, ?, ?)
                """,
                (
                    company_id,
                    int(plan["id"]),
                    now,
                    self._clean(expires_at),
                    now,
                    now,
                ),
            )
            subscription_id = int(cursor.lastrowid)

            self.record_audit(
                conn=conn,
                action="company.plan_assigned",
                actor_user_id=actor_user_id,
                company_id=company_id,
                target_type="subscription",
                target_id=subscription_id,
                data={"plan_code": plan["code"], "expires_at": self._clean(expires_at)},
                ip_address=ip_address,
            )

            conn.commit()

        return {
            "company_id": company_id,
            "subscription_id": subscription_id,
            "plan_code": plan["code"],
            "plan_name": plan["name"],
            "starts_at": now,
            "expires_at": self._clean(expires_at),
        }

    # ------------------------------------------------------------------
    # Platform configuration: modules, branding, layout
    # ------------------------------------------------------------------

    def _ensure_platform_config(
        self, conn: Any, company_id: int, actor_user_id: int | None = None
    ) -> None:
        now = utc_now_iso()
        conn.execute(
            """
            INSERT OR IGNORE INTO company_platform_config (
                company_id, modules_json, branding_json, layout_json,
                updated_by_user_id, created_at, updated_at
            )
            VALUES (?, '{}', '{}', '{}', ?, ?, ?)
            """,
            (
                int(company_id),
                int(actor_user_id) if actor_user_id is not None else None,
                now,
                now,
            ),
        )

    def get_platform_config(self, company_id: int) -> dict[str, Any]:
        """What this company is allowed to see, with every module resolved.

        A module absent from the stored config is on. Defaulting to off would
        mean a release that adds a module silently turns it off for every
        existing company until somebody edits each one.
        """
        company_id = int(company_id)

        with database_manager.control() as conn:
            company = conn.execute(
                "SELECT id FROM companies WHERE id = ? LIMIT 1", (company_id,)
            ).fetchone()

            if not company:
                raise PlatformNotFound(f"No company with id {company_id}.")

            row = conn.execute(
                "SELECT * FROM company_platform_config WHERE company_id = ? LIMIT 1",
                (company_id,),
            ).fetchone()

        stored_modules = self._loads(row["modules_json"]) if row else {}
        branding = self._loads(row["branding_json"]) if row else {}
        layout = self._loads(row["layout_json"]) if row else {}

        modules = {
            key: bool(stored_modules.get(key, True)) for key in PLATFORM_MODULES
        }

        return {
            "company_id": company_id,
            "modules": modules,
            "branding": branding,
            "layout": layout,
            "available_modules": list(PLATFORM_MODULES),
            "available_branding_fields": list(BRANDING_FIELDS),
            "available_layout_flags": list(LAYOUT_FLAGS),
            "updated_at": row["updated_at"] if row else None,
            "updated_by_user_id": row["updated_by_user_id"] if row else None,
        }

    def _validate_modules(self, modules: dict[str, Any]) -> dict[str, bool]:
        if not isinstance(modules, dict):
            raise PlatformError("Modules must be a mapping of module key to on/off.")

        unknown = sorted(set(modules) - set(PLATFORM_MODULES))

        if unknown:
            raise PlatformError(
                f"Unknown module key(s): {', '.join(unknown)}. "
                f"Valid keys are: {', '.join(PLATFORM_MODULES)}."
            )

        return {key: bool(value) for key, value in modules.items()}

    def _validate_branding(self, branding: dict[str, Any]) -> dict[str, str]:
        if not isinstance(branding, dict):
            raise PlatformError("Branding must be a mapping of field to value.")

        unknown = sorted(set(branding) - set(BRANDING_FIELDS))

        if unknown:
            raise PlatformError(
                f"Unknown branding field(s): {', '.join(unknown)}. "
                f"Valid fields are: {', '.join(BRANDING_FIELDS)}."
            )

        cleaned: dict[str, str] = {}

        for key, value in branding.items():
            text = self._clean(value)

            if text is None:
                continue

            if len(text) > MAX_BRANDING_VALUE:
                raise PlatformError(
                    f"{key} cannot be longer than {MAX_BRANDING_VALUE} characters."
                )

            if key in _COLOR_FIELDS and not _COLOR_PATTERN.match(text):
                raise PlatformError(
                    f"{key} must be a hex colour such as #1B2A4A, not {text!r}."
                )

            cleaned[key] = text

        return cleaned

    def _validate_layout(self, layout: dict[str, Any]) -> dict[str, bool]:
        if not isinstance(layout, dict):
            raise PlatformError("Layout must be a mapping of flag to on/off.")

        unknown = sorted(set(layout) - set(LAYOUT_FLAGS))

        if unknown:
            raise PlatformError(
                f"Unknown layout flag(s): {', '.join(unknown)}. "
                f"Valid flags are: {', '.join(LAYOUT_FLAGS)}."
            )

        return {key: bool(value) for key, value in layout.items()}

    def update_platform_config(
        self,
        company_id: int,
        *,
        modules: dict[str, Any] | None = None,
        branding: dict[str, Any] | None = None,
        layout: dict[str, Any] | None = None,
        actor_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Merge a partial config change after validating every key.

        Unknown keys are refused rather than stored. A stored typo looks like a
        setting that was applied and disables nothing at all, which is the worst
        of both outcomes: the operator believes a module is off, and the company
        can still use it.
        """
        company_id = int(company_id)

        with database_manager.control() as conn:
            company = conn.execute(
                "SELECT id FROM companies WHERE id = ? LIMIT 1", (company_id,)
            ).fetchone()

            if not company:
                raise PlatformNotFound(f"No company with id {company_id}.")

        clean_modules = self._validate_modules(modules) if modules is not None else None
        clean_branding = (
            self._validate_branding(branding) if branding is not None else None
        )
        clean_layout = self._validate_layout(layout) if layout is not None else None

        with database_manager.control() as conn:
            self._ensure_platform_config(conn, company_id, actor_user_id)

            row = conn.execute(
                "SELECT * FROM company_platform_config WHERE company_id = ? LIMIT 1",
                (company_id,),
            ).fetchone()

            merged_modules = self._loads(row["modules_json"])
            merged_branding = self._loads(row["branding_json"])
            merged_layout = self._loads(row["layout_json"])

            if clean_modules is not None:
                merged_modules.update(clean_modules)

            if clean_branding is not None:
                merged_branding.update(clean_branding)

            if clean_layout is not None:
                merged_layout.update(clean_layout)

            conn.execute(
                """
                UPDATE company_platform_config
                SET modules_json = ?,
                    branding_json = ?,
                    layout_json = ?,
                    updated_by_user_id = ?,
                    updated_at = ?
                WHERE company_id = ?
                """,
                (
                    json.dumps(merged_modules, ensure_ascii=False),
                    json.dumps(merged_branding, ensure_ascii=False),
                    json.dumps(merged_layout, ensure_ascii=False),
                    int(actor_user_id) if actor_user_id is not None else None,
                    utc_now_iso(),
                    company_id,
                ),
            )

            self.record_audit(
                conn=conn,
                action="company.platform_config_updated",
                actor_user_id=actor_user_id,
                company_id=company_id,
                target_type="company",
                target_id=company_id,
                data={
                    "modules": clean_modules,
                    "branding": sorted(clean_branding) if clean_branding else None,
                    "layout": clean_layout,
                },
                ip_address=ip_address,
            )

            conn.commit()

        # The switch is read on every inbound message through a cache, so it has
        # to be dropped the moment it changes. Without this an operator turning
        # a module off would watch the screen disappear while the assistant kept
        # using it for another half minute — and would reasonably conclude the
        # switch does not work.
        #
        # Imported here rather than at module scope: `module_gate` imports this
        # module for `PLATFORM_MODULES`, and a top-level import either way round
        # is a cycle.
        from backend.services.module_gate import module_gate

        module_gate.invalidate(company_id)

        return self.get_platform_config(company_id)

    # ------------------------------------------------------------------
    # Platform administrators
    # ------------------------------------------------------------------

    def list_platform_admins(self) -> list[dict[str, Any]]:
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT id, email, full_name, status, last_login_at, created_at
                FROM users
                WHERE is_super_admin = 1
                ORDER BY id ASC
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def search_users(
        self, *, search: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Find an account by email or name, so a grant needs no guessed id.

        Control-plane only: `users` holds sign-in identities, not anything a
        company's customers wrote. No password hash, no session token and no
        workspace code is selected.

        A blank search returns the most recent accounts rather than all of
        them, because this exists to find one person, not to export a directory.
        """
        term = self._clean(search)
        limit = max(1, min(int(limit), 50))

        with database_manager.control() as conn:
            if term:
                pattern = f"%{term}%"
                rows = conn.execute(
                    """
                    SELECT id, email, full_name, status, is_super_admin, created_at
                    FROM users
                    WHERE email LIKE ? COLLATE NOCASE
                       OR full_name LIKE ? COLLATE NOCASE
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (pattern, pattern, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, email, full_name, status, is_super_admin, created_at
                    FROM users
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

        return [
            {**dict(row), "is_super_admin": bool(row["is_super_admin"])}
            for row in rows
        ]

    def _active_admin_ids(self, conn: Any) -> set[int]:
        rows = conn.execute(
            "SELECT id FROM users WHERE is_super_admin = 1 AND status = 'active'"
        ).fetchall()

        return {int(row["id"]) for row in rows}

    def grant_platform_admin(
        self,
        user_id: int,
        *,
        actor_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        user_id = int(user_id)

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id, email, status, is_super_admin FROM users WHERE id = ? LIMIT 1",
                (user_id,),
            ).fetchone()

            if not row:
                raise PlatformNotFound(f"No user with id {user_id}.")

            if str(row["status"]) != "active":
                raise PlatformConflict(
                    "This account is not active, so it cannot administer the "
                    "platform."
                )

            conn.execute(
                "UPDATE users SET is_super_admin = 1, updated_at = ? WHERE id = ?",
                (utc_now_iso(), user_id),
            )

            self.record_audit(
                conn=conn,
                action="platform_admin.granted",
                actor_user_id=actor_user_id,
                target_type="user",
                target_id=user_id,
                data={"email": row["email"]},
                ip_address=ip_address,
            )

            conn.commit()

        logger.warning(
            "User %s granted platform administrator rights by %s",
            user_id,
            actor_user_id,
        )

        return {"user_id": user_id, "is_super_admin": True}

    def revoke_platform_admin(
        self,
        user_id: int,
        *,
        actor_user_id: int | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Take platform rights away, unless that would lock everyone out.

        Refusing the last one is not politeness. Platform rights can only be
        granted from this console, so revoking the final administrator leaves
        nobody who can grant them back: recovery would mean shell access to the
        server and the CLI. The check counts what would remain, so it catches
        the common version — an administrator revoking their own last remaining
        access — as well as revoking somebody else's.
        """
        user_id = int(user_id)

        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT id, email, is_super_admin FROM users WHERE id = ? LIMIT 1",
                (user_id,),
            ).fetchone()

            if not row:
                raise PlatformNotFound(f"No user with id {user_id}.")

            if not int(row["is_super_admin"] or 0):
                raise PlatformConflict(
                    "This account is not a platform administrator."
                )

            remaining = self._active_admin_ids(conn) - {user_id}

            if not remaining:
                raise PlatformConflict(
                    "This is the last platform administrator. Grant the rights "
                    "to someone else first — revoking this one would leave "
                    "nobody able to grant them back."
                )

            conn.execute(
                "UPDATE users SET is_super_admin = 0, updated_at = ? WHERE id = ?",
                (utc_now_iso(), user_id),
            )

            # Their platform sessions die now rather than at expiry.
            # `get_platform_admin` re-checks the flag on every request, so this
            # is belt and braces rather than the only barrier.
            conn.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = ?
                WHERE user_id = ? AND scope = 'platform' AND revoked_at IS NULL
                """,
                (utc_now_iso(), user_id),
            )

            self.record_audit(
                conn=conn,
                action="platform_admin.revoked",
                actor_user_id=actor_user_id,
                target_type="user",
                target_id=user_id,
                data={"email": row["email"], "remaining_admins": len(remaining)},
                ip_address=ip_address,
            )

            conn.commit()

        logger.warning(
            "User %s had platform administrator rights revoked by %s",
            user_id,
            actor_user_id,
        )

        return {
            "user_id": user_id,
            "is_super_admin": False,
            "remaining_admins": len(remaining),
        }

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def platform_health(self) -> dict[str, Any]:
        """Is the platform actually able to serve every company it lists?

        The database check runs through :meth:`company_statistics` on purpose:
        that is the one method allowed to open a tenant file, and routing the
        health check through it means there is no second place in this service
        that touches customer databases.
        """
        with database_manager.control() as conn:
            totals = conn.execute(
                """
                SELECT
                    COUNT(*) AS companies,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN status <> 'active' THEN 1 ELSE 0 END) AS suspended
                FROM companies
                """
            ).fetchone()

            provisioned = int(
                conn.execute(
                    "SELECT COUNT(*) AS total FROM company_databases"
                ).fetchone()["total"]
            )

            company_rows = conn.execute(
                """
                SELECT companies.id, companies.name
                FROM companies
                JOIN company_databases
                    ON company_databases.company_id = companies.id
                ORDER BY companies.id
                """
            ).fetchall()

            admins = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS total FROM users
                    WHERE is_super_admin = 1 AND status = 'active'
                    """
                ).fetchone()["total"]
            )

            failed_logins = conn.execute(
                """
                SELECT email, ip_address, failure_reason, created_at
                FROM login_attempts
                WHERE succeeded = 0
                ORDER BY id DESC
                LIMIT ?
                """,
                (self.RECENT_FAILED_LOGIN_LIMIT,),
            ).fetchall()

            failed_login_total = int(
                conn.execute(
                    "SELECT COUNT(*) AS total FROM login_attempts WHERE succeeded = 0"
                ).fetchone()["total"]
            )

        unreadable: list[dict[str, Any]] = []
        total_bytes = 0

        for row in company_rows:
            company_id = int(row["id"])

            try:
                statistics = self.company_statistics(company_id)
            except PlatformError as exc:
                unreadable.append(
                    {
                        "company_id": company_id,
                        "name": row["name"],
                        "error": str(exc),
                    }
                )
                continue

            total_bytes += int(statistics["database_bytes"])

        return {
            "companies": int(totals["companies"] or 0),
            "active_companies": int(totals["active"] or 0),
            "suspended_companies": int(totals["suspended"] or 0),
            "provisioned_databases": provisioned,
            "readable_databases": len(company_rows) - len(unreadable),
            "unreadable_databases": unreadable,
            "total_database_bytes": total_bytes,
            "platform_admins": admins,
            "failed_logins_total": failed_login_total,
            "recent_failed_logins": [dict(row) for row in failed_logins],
            "healthy": not unreadable and admins > 0,
            "checked_at": utc_now_iso(),
        }


platform_service = PlatformService()
