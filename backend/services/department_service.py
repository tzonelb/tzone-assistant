from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DepartmentService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS company_departments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(company_id, name)
                )
                """
            )
            conn.commit()

    def list_for_company(self, *, company_id: int) -> list[str]:
        """Always includes 'Unassigned' first (not stored — implicit
        for every company), followed by whatever departments this
        company has actually defined for itself. No hardcoded
        business-type list — a brand-new company starts with zero
        departments beyond Unassigned until they add their own."""
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM company_departments WHERE company_id = ? ORDER BY name",
                (company_id,),
            ).fetchall()
        return ["Unassigned"] + [row["name"] for row in rows]

    def create(self, *, company_id: int, name: str) -> list[str]:
        name = (name or "").strip()
        if not name:
            raise ValueError("Department name is required.")
        if name.lower() == "unassigned":
            raise ValueError('"Unassigned" is reserved and always available automatically.')

        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM company_departments WHERE company_id = ? AND lower(name) = lower(?)",
                (company_id, name),
            ).fetchone()
            if existing:
                raise ValueError(f'A department named "{name}" already exists.')

            conn.execute(
                "INSERT INTO company_departments (company_id, name, created_at) VALUES (?, ?, ?)",
                (company_id, name, utc_now_iso()),
            )
            conn.commit()
        return self.list_for_company(company_id=company_id)

    def delete(self, *, company_id: int, name: str) -> list[str]:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM company_departments WHERE company_id = ? AND name = ?",
                (company_id, name),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Department not found")
        return self.list_for_company(company_id=company_id)


department_service = DepartmentService()
