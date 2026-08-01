"""Reply Flows — the real, per-company-configurable conversation flow
builder. Replaces the old decorative `reply_flow.steps` reorder-list (which
the engine never actually read) with a real node-graph a company designs
visually (drag-and-drop) and the engine can execute.

Each flow can be scoped to MULTIPLE channels and MULTIPLE departments at
once (multi-select, not a single dropdown) — e.g. one flow for
WhatsApp+Telegram Sales. Departments are never free-typed: they must be
real department names already registered via department_service (Company
Settings > Departments), so a flow can never reference a department that
doesn't exist. Nothing here is hardcoded to any one company's business.

v1 scope: full CRUD + graph storage (nodes/edges as JSON) + a company-scoped
list. Execution wiring into core/engine.py is a deliberately separate next
step once the schema/UI are proven — this is flagged, not silently skipped.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from backend.services.department_service import department_service
from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


NODE_TYPES = [
    "greeting", "company_intro", "ask_question", "ai_direct", "ai_knowledge_only",
    "ai_knowledge_plus", "canned_reply", "human_handoff", "appointment", "create_task",
    "product_suggest", "condition", "timeout_followup", "close_chat", "end",
]

CHANNEL_OPTIONS = ["whatsapp", "messenger", "instagram", "telegram"]


class ReplyFlowService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reply_flows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    channels_json TEXT NOT NULL DEFAULT '[]',
                    departments_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'draft',
                    nodes_json TEXT NOT NULL DEFAULT '[]',
                    edges_json TEXT NOT NULL DEFAULT '[]',
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_by_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
                )
                """
            )
            existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(reply_flows)")}
            if "channels_json" not in existing_columns:
                conn.execute("ALTER TABLE reply_flows ADD COLUMN channels_json TEXT NOT NULL DEFAULT '[]'")
            if "departments_json" not in existing_columns:
                conn.execute("ALTER TABLE reply_flows ADD COLUMN departments_json TEXT NOT NULL DEFAULT '[]'")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reply_flows_company ON reply_flows(company_id)"
            )
            conn.commit()

    def _clean_channels(self, channels: list[str] | None) -> list[str]:
        if not channels:
            return []
        cleaned = []
        for channel in channels:
            channel = (channel or "").strip().lower()
            if channel and channel not in CHANNEL_OPTIONS:
                raise ValueError(f'"{channel}" is not a valid channel. Choose from: {", ".join(CHANNEL_OPTIONS)}.')
            if channel and channel not in cleaned:
                cleaned.append(channel)
        return cleaned

    def _clean_departments(self, *, company_id: int, departments: list[str] | None) -> list[str]:
        if not departments:
            return []
        real_departments = set(department_service.list_for_company(company_id=company_id))
        cleaned = []
        for department in departments:
            department = (department or "").strip()
            if not department:
                continue
            if department not in real_departments:
                raise ValueError(
                    f'"{department}" is not a registered department. '
                    f"Add it first in Company Settings → Departments."
                )
            if department not in cleaned:
                cleaned.append(department)
        return cleaned

    def list_for_company(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, company_id, name, channels_json, departments_json, status, is_default, "
                "nodes_json, created_at, updated_at FROM reply_flows WHERE company_id = ? ORDER BY updated_at DESC",
                (company_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["channels"] = json.loads(item.pop("channels_json") or "[]")
            item["departments"] = json.loads(item.pop("departments_json") or "[]")
            item["node_count"] = len(json.loads(item.pop("nodes_json") or "[]"))
            items.append(item)
        return items

    def get(self, *, company_id: int, flow_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM reply_flows WHERE id = ? AND company_id = ?",
                (flow_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Reply flow not found")
        return self._row_to_dict(row)

    def _row_to_dict(self, row) -> dict[str, Any]:
        item = dict(row)
        item["nodes"] = json.loads(item.pop("nodes_json") or "[]")
        item["edges"] = json.loads(item.pop("edges_json") or "[]")
        item["channels"] = json.loads(item.pop("channels_json") or "[]")
        item["departments"] = json.loads(item.pop("departments_json") or "[]")
        return item

    def create(
        self, *, company_id: int, name: str, channels: list[str] | None = None,
        departments: list[str] | None = None, actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("A flow name is required.")
        cleaned_channels = self._clean_channels(channels)
        cleaned_departments = self._clean_departments(company_id=company_id, departments=departments)
        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reply_flows (
                    company_id, name, channels_json, departments_json, status, nodes_json, edges_json,
                    created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', '[]', '[]', ?, ?, ?)
                """,
                (company_id, name, json.dumps(cleaned_channels), json.dumps(cleaned_departments), actor_user_id, now, now),
            )
            flow_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, flow_id=flow_id)

    def update(
        self, *, company_id: int, flow_id: int,
        name: str | None = None, channels: list[str] | None = None, departments: list[str] | None = None,
        status: str | None = None, nodes: list[dict] | None = None, edges: list[dict] | None = None,
    ) -> dict[str, Any]:
        existing = self.get(company_id=company_id, flow_id=flow_id)
        cleaned: dict[str, Any] = {}
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("A flow name is required.")
            cleaned["name"] = name
        if channels is not None:
            cleaned["channels_json"] = json.dumps(self._clean_channels(channels))
        if departments is not None:
            cleaned["departments_json"] = json.dumps(self._clean_departments(company_id=company_id, departments=departments))
        if status is not None:
            if status not in ("draft", "active", "archived"):
                raise ValueError('Status must be "draft", "active", or "archived".')
            cleaned["status"] = status
        if nodes is not None:
            for node in nodes:
                node_type = node.get("data", {}).get("nodeType") or node.get("type")
                if node_type and node_type not in NODE_TYPES:
                    raise ValueError(f'"{node_type}" is not a valid node type.')
            cleaned["nodes_json"] = json.dumps(nodes)
        if edges is not None:
            cleaned["edges_json"] = json.dumps(edges)

        if not cleaned:
            return existing

        now = utc_now_iso()
        with db.connect() as conn:
            assignments = ", ".join(f"{key} = ?" for key in cleaned)
            conn.execute(
                f"UPDATE reply_flows SET {assignments}, updated_at = ? WHERE id = ? AND company_id = ?",
                [*cleaned.values(), now, flow_id, company_id],
            )
            conn.commit()
        return self.get(company_id=company_id, flow_id=flow_id)

    def delete(self, *, company_id: int, flow_id: int) -> None:
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM reply_flows WHERE id = ? AND company_id = ?", (flow_id, company_id),
            )
            conn.commit()
        if cursor.rowcount == 0:
            raise KeyError("Reply flow not found")

    def duplicate(self, *, company_id: int, flow_id: int, actor_user_id: int | None = None) -> dict[str, Any]:
        source = self.get(company_id=company_id, flow_id=flow_id)
        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reply_flows (
                    company_id, name, channels_json, departments_json, status, nodes_json, edges_json,
                    created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (
                    company_id, f"{source['name']} (copy)",
                    json.dumps(source["channels"]), json.dumps(source["departments"]),
                    json.dumps(source["nodes"]), json.dumps(source["edges"]),
                    actor_user_id, now, now,
                ),
            )
            flow_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, flow_id=flow_id)


reply_flow_service = ReplyFlowService()
