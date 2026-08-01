"""Reply Flows — the real, per-company-configurable conversation flow
builder. Replaces the old decorative `reply_flow.steps` reorder-list (which
the engine never actually read) with a real node-graph a company designs
visually (drag-and-drop) and the engine can execute.

Each flow is scoped to a channel + optional department, so a company can
have a distinct flow per channel/department combination (e.g. WhatsApp
Sales vs Telegram Support), matching the plug-and-play "every company
designs it their own way" requirement — nothing here is hardcoded to any
one company's business.

v1 scope: full CRUD + graph storage (nodes/edges as JSON) + a company-scoped
list. Execution wiring into core/engine.py is a deliberately separate next
step once the schema/UI are proven — this is flagged, not silently skipped.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


NODE_TYPES = [
    "greeting", "company_intro", "ask_question", "ai_direct", "ai_knowledge_only",
    "ai_knowledge_plus", "canned_reply", "human_handoff", "appointment", "create_task",
    "product_suggest", "condition", "timeout_followup", "close_chat", "end",
]


class ReplyFlowService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reply_flows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'all',
                    department TEXT NOT NULL DEFAULT '',
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reply_flows_company ON reply_flows(company_id)"
            )
            conn.commit()

    def list_for_company(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, company_id, name, channel, department, status, is_default, "
                "created_at, updated_at FROM reply_flows WHERE company_id = ? ORDER BY updated_at DESC",
                (company_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            with db.connect() as conn:
                counts = conn.execute(
                    "SELECT nodes_json FROM reply_flows WHERE id = ?", (item["id"],),
                ).fetchone()
            item["node_count"] = len(json.loads(counts["nodes_json"] or "[]")) if counts else 0
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
        return item

    def create(
        self, *, company_id: int, name: str, channel: str = "all", department: str = "",
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("A flow name is required.")
        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reply_flows (
                    company_id, name, channel, department, status, nodes_json, edges_json,
                    created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', '[]', '[]', ?, ?, ?)
                """,
                (company_id, name, channel or "all", department or "", actor_user_id, now, now),
            )
            flow_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, flow_id=flow_id)

    def update(
        self, *, company_id: int, flow_id: int,
        name: str | None = None, channel: str | None = None, department: str | None = None,
        status: str | None = None, nodes: list[dict] | None = None, edges: list[dict] | None = None,
    ) -> dict[str, Any]:
        existing = self.get(company_id=company_id, flow_id=flow_id)
        cleaned: dict[str, Any] = {}
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("A flow name is required.")
            cleaned["name"] = name
        if channel is not None:
            cleaned["channel"] = channel or "all"
        if department is not None:
            cleaned["department"] = department or ""
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
                    company_id, name, channel, department, status, nodes_json, edges_json,
                    created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (
                    company_id, f"{source['name']} (copy)", source["channel"], source["department"],
                    json.dumps(source["nodes"]), json.dumps(source["edges"]),
                    actor_user_id, now, now,
                ),
            )
            flow_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, flow_id=flow_id)


reply_flow_service = ReplyFlowService()
