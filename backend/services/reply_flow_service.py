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
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.services.department_service import department_service
from config.settings import config
from database.database import db

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


NODE_TYPES = [
    "greeting", "company_intro", "ask_question", "ai_direct", "ai_knowledge_only",
    "ai_knowledge_plus", "canned_reply", "human_handoff", "appointment", "create_task",
    "product_suggest", "condition", "timeout_followup", "close_chat", "end",
]

CHANNEL_OPTIONS = ["whatsapp", "messenger", "instagram", "telegram"]

REPLY_MODE_OPTIONS = ["ai_direct", "ai_knowledge_only", "ai_knowledge_plus", "canned_reply", "human_handoff"]

# Kept in sync by hand with frontend/src/pages/reply-flows/nodeFieldsConfig.js —
# describes each node type + its real config fields for the AI text-to-flow prompt.
NODE_TYPE_REFERENCE = """
- greeting: config.text — the opening message. Variables: {{customer_name}}, {{company_name}}.
- company_intro: config.text — intro message about the company.
- ask_question: config.question — the question to ask; config.save_as — variable name to store the answer.
- ai_direct: config.instructions — free-form AI instructions; config.exit_when — when to move to the next step.
- ai_knowledge_only: same fields as ai_direct, AI must only use the Knowledge Base.
- ai_knowledge_plus: same fields as ai_direct, AI uses Knowledge Base + reasoning.
- canned_reply: config.text — verbatim reply text, sent exactly as written.
- human_handoff: config.note — note for the employee taking over.
- appointment: config.note — instructions/service context for booking.
- create_task: config.task_type (one of follow_up, complaint, service_request, sales_inquiry, internal, other); config.note.
- product_suggest: config.note — guidance on what to suggest.
- condition: config.variable, config.operator (one of equals, contains, greater_than, less_than, is_set), config.value.
- timeout_followup: config.wait_minutes (number); config.text — follow-up message.
- close_chat: config.ask_reschedule (boolean).
- end: no fields.
""".strip()


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
                    reply_modes_json TEXT NOT NULL DEFAULT '[]',
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
            if "reply_modes_json" not in existing_columns:
                conn.execute("ALTER TABLE reply_flows ADD COLUMN reply_modes_json TEXT NOT NULL DEFAULT '[]'")
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

    def _clean_reply_modes(self, reply_modes: list[str] | None) -> list[str]:
        if not reply_modes:
            return []
        cleaned = []
        for mode in reply_modes:
            mode = (mode or "").strip().lower()
            if mode and mode not in REPLY_MODE_OPTIONS:
                raise ValueError(f'"{mode}" is not a valid reply mode. Choose from: {", ".join(REPLY_MODE_OPTIONS)}.')
            if mode and mode not in cleaned:
                cleaned.append(mode)
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
                "SELECT id, company_id, name, channels_json, departments_json, reply_modes_json, status, is_default, "
                "nodes_json, created_at, updated_at FROM reply_flows WHERE company_id = ? ORDER BY updated_at DESC",
                (company_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["channels"] = json.loads(item.pop("channels_json") or "[]")
            item["departments"] = json.loads(item.pop("departments_json") or "[]")
            item["reply_modes"] = json.loads(item.pop("reply_modes_json") or "[]")
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
        item["reply_modes"] = json.loads(item.pop("reply_modes_json") or "[]")
        return item

    def create(
        self, *, company_id: int, name: str, channels: list[str] | None = None,
        departments: list[str] | None = None, reply_modes: list[str] | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("A flow name is required.")
        cleaned_channels = self._clean_channels(channels)
        cleaned_departments = self._clean_departments(company_id=company_id, departments=departments)
        cleaned_reply_modes = self._clean_reply_modes(reply_modes)
        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reply_flows (
                    company_id, name, channels_json, departments_json, reply_modes_json, status, nodes_json, edges_json,
                    created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', '[]', '[]', ?, ?, ?)
                """,
                (
                    company_id, name, json.dumps(cleaned_channels), json.dumps(cleaned_departments),
                    json.dumps(cleaned_reply_modes), actor_user_id, now, now,
                ),
            )
            flow_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, flow_id=flow_id)

    def update(
        self, *, company_id: int, flow_id: int,
        name: str | None = None, channels: list[str] | None = None, departments: list[str] | None = None,
        reply_modes: list[str] | None = None,
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
        if reply_modes is not None:
            cleaned["reply_modes_json"] = json.dumps(self._clean_reply_modes(reply_modes))
        if status is not None:
            if status not in ("draft", "active", "archived"):
                raise ValueError('Status must be "draft", "active", or "archived".')
            cleaned["status"] = status
        if nodes is not None:
            for node in nodes:
                node_type = node.get("data", {}).get("nodeType")
                if node_type not in NODE_TYPES:
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

    def generate_from_text(self, *, company_id: int, flow_id: int, text: str) -> dict[str, Any]:
        """Lets an admin write the flow out in plain language and has the AI
        turn it into the real node/edge graph — the 'write it' alternative
        to dragging nodes on the canvas. Uses the same OpenAI Responses API
        call shape as core/ai_router.py."""
        text = (text or "").strip()
        if not text:
            raise ValueError("Write out what you want the flow to do first.")
        if not config.OPENAI_API_KEY:
            raise ValueError("AI is not configured on this server (missing OPENAI_API_KEY).")

        system_prompt = f"""
You design conversation flows for a customer-support chat platform. Convert the
admin's plain-language description into a step-by-step flow graph.

Available step (node) types and their real config fields:
{NODE_TYPE_REFERENCE}

Rules:
- Break the description into a logical sequence of steps, choosing the closest node type for each.
- Each node needs a short "label" and a "config" object with the fields listed above for that type.
- Connect steps in the order they should happen via edges (source -> target).
- Return ONLY JSON: {{"nodes": [{{"id": "n1", "nodeType": "...", "label": "...", "config": {{...}}}}], "edges": [{{"source": "n1", "target": "n2"}}]}}
- Use short sequential ids like n1, n2, n3.
- Do not invent node types outside the list above.
""".strip()

        payload = {
            "model": config.OPENAI_MODEL,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "text": {"format": {"type": "json_object"}},
        }
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=40) as client:
                response = client.post(config.OPENAI_API_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.exception("Reply flow AI generation request failed")
            raise ValueError(f"Could not reach the AI service: {exc}") from None

        if response.status_code >= 400:
            raise ValueError(f"AI service error ({response.status_code}). Please try again.")

        output_text = self._extract_output_text(response.json())
        if not output_text:
            raise ValueError("The AI did not return a usable flow. Try describing it differently.")

        try:
            parsed = json.loads(output_text)
        except (TypeError, ValueError):
            raise ValueError("The AI returned an unreadable flow. Try describing it differently.") from None

        nodes, edges = self._build_graph_from_ai_result(parsed)
        return self.update(company_id=company_id, flow_id=flow_id, nodes=nodes, edges=edges)

    def _extract_output_text(self, data: dict[str, Any]) -> str | None:
        if data.get("output_text"):
            return data["output_text"]
        for output_item in data.get("output", []):
            for content in output_item.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    return content.get("text")
        choices = data.get("choices")
        if choices:
            return choices[0].get("message", {}).get("content")
        return None

    def _build_graph_from_ai_result(self, parsed: dict[str, Any]) -> tuple[list[dict], list[dict]]:
        raw_nodes = parsed.get("nodes") or []
        raw_edges = parsed.get("edges") or []
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise ValueError("The AI didn't produce any steps. Try describing it differently.")

        id_map: dict[str, str] = {}
        nodes = []
        for index, raw_node in enumerate(raw_nodes):
            node_type = raw_node.get("nodeType")
            if node_type not in NODE_TYPES:
                continue
            raw_id = str(raw_node.get("id") or f"n{index + 1}")
            node_id = f"node-ai-{index + 1}"
            id_map[raw_id] = node_id
            nodes.append({
                "id": node_id,
                "type": "step",
                "position": {"x": 250, "y": 80 + index * 130},
                "data": {
                    "nodeType": node_type,
                    "label": str(raw_node.get("label") or node_type),
                    "config": raw_node.get("config") if isinstance(raw_node.get("config"), dict) else {},
                },
            })

        if not nodes:
            raise ValueError("The AI didn't produce any valid steps. Try describing it differently.")

        edges = []
        for index, raw_edge in enumerate(raw_edges):
            source = id_map.get(str(raw_edge.get("source")))
            target = id_map.get(str(raw_edge.get("target")))
            if source and target:
                edges.append({"id": f"edge-ai-{index + 1}", "source": source, "target": target})

        return nodes, edges

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
                    company_id, name, channels_json, departments_json, reply_modes_json, status, nodes_json, edges_json,
                    created_by_user_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (
                    company_id, f"{source['name']} (copy)",
                    json.dumps(source["channels"]), json.dumps(source["departments"]), json.dumps(source["reply_modes"]),
                    json.dumps(source["nodes"]), json.dumps(source["edges"]),
                    actor_user_id, now, now,
                ),
            )
            flow_id = int(cursor.lastrowid)
            conn.commit()
        return self.get(company_id=company_id, flow_id=flow_id)


reply_flow_service = ReplyFlowService()
