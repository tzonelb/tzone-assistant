"""Pre-issued license keys a customer redeems at signup instead of picking a
plan themselves — e.g. a key sold offline/by a reseller that already carries
a specific plan entitlement. Distinct from `companies.license_code`
(platform_admin_service), which is just a per-company serial generated AFTER
a company already exists.
"""
import secrets
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_key() -> str:
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous chars (0/O, 1/I)
    groups = ["".join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return f"TZK-{'-'.join(groups)}"


class LicenseKeyService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS license_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    plan_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unused',
                    note TEXT,
                    issued_by_user_id INTEGER,
                    redeemed_by_company_id INTEGER,
                    redeemed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plans(id)
                )
                """
            )
            conn.commit()

    def issue(self, *, plan_id: int, note: str | None = None, issued_by_user_id: int | None = None) -> dict[str, Any]:
        with db.connect() as conn:
            plan = conn.execute("SELECT id FROM plans WHERE id = ?", (plan_id,)).fetchone()
            if not plan:
                raise KeyError("Plan not found")
            code = _generate_key()
            conn.execute(
                """
                INSERT INTO license_keys (code, plan_id, status, note, issued_by_user_id, created_at)
                VALUES (?, ?, 'unused', ?, ?, ?)
                """,
                (code, plan_id, note, issued_by_user_id, utc_now_iso()),
            )
            conn.commit()
        return self.get(code)

    def get(self, code: str) -> dict[str, Any] | None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM license_keys WHERE code = ?", ((code or "").strip().upper(),)
            ).fetchone()
        return dict(row) if row else None

    def peek_plan_id(self, code: str) -> int:
        """Validate an unredeemed key and return the plan_id it grants,
        without redeeming it — used to preview during signup before the
        company actually exists yet."""
        key = self.get(code)
        if not key:
            raise ValueError("This license key was not found.")
        if key["status"] != "unused":
            raise ValueError("This license key has already been used.")
        return key["plan_id"]

    def redeem(self, *, code: str, company_id: int) -> None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM license_keys WHERE code = ?", ((code or "").strip().upper(),)
            ).fetchone()
            if not row or row["status"] != "unused":
                raise ValueError("This license key is not valid or already used.")
            # Atomic claim: the WHERE status='unused' guard (not just the
            # pre-check above) is what actually prevents two concurrent
            # redemptions of the same key from both succeeding — a bare
            # SELECT-then-UPDATE has a gap where both callers can pass the
            # pre-check before either commits, double-granting the plan
            # entitlement from a single key.
            cursor = conn.execute(
                "UPDATE license_keys SET status = 'redeemed', redeemed_by_company_id = ?, redeemed_at = ? "
                "WHERE id = ? AND status = 'unused'",
                (company_id, utc_now_iso(), row["id"]),
            )
            if cursor.rowcount == 0:
                raise ValueError("This license key is not valid or already used.")
            conn.commit()

    def list_all(self) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute("SELECT * FROM license_keys ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


license_key_service = LicenseKeyService()
