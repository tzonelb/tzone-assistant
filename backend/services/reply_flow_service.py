"""Reply Flows: a company's step-by-step scripted conversations.

An owner draws a flow on a canvas -- greet, ask a question, let the AI answer
from the knowledge base, branch on the answer, hand off to a human -- scopes it
to some channels and departments and a trigger, and marks it active. The
``reply_flow_engine`` then runs an active flow for a matching customer instead
of the default single-shot AI reply.

Everything here is per company: a flow lives in that company's own encrypted
database, and one company's flows never reach another's customers. This module
owns the stored shape, the validation, and the vocabulary (the node and trigger
types) that both the builder screen and the runtime engine agree on.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)

# A generous ceiling: a real flow is a handful of steps, not thousands. The cap
# stops a single company storing a graph so large that loading it on every
# matching message becomes the cost of answering.
MAX_FLOWS = 200
MAX_NODES = 200
MAX_NAME_LENGTH = 120

VALID_STATUSES = ("draft", "active", "archived")

# Kept in sync by hand with frontend ReplyFlowsListPage.jsx CHANNEL_OPTIONS.
CHANNEL_OPTIONS = ("whatsapp", "messenger", "instagram", "telegram")

# Kept in sync by hand with frontend ReplyFlowsListPage.jsx REPLY_MODE_OPTIONS.
REPLY_MODE_OPTIONS = (
    "ai_direct",
    "ai_knowledge_only",
    "ai_knowledge_plus",
    "canned_reply",
    "human_handoff",
)

# Every node type the builder can place -- must stay in sync with the frontend
# nodeTypesConfig.js NODE_TYPE_CONFIG. The engine dispatches on these keys, so a
# node type the engine does not know is treated as a pass-through (it advances
# to the next step) rather than an error.
NODE_TYPES = (
    "greeting",
    "company_intro",
    "ask_question",
    "ai_direct",
    "ai_knowledge_only",
    "ai_knowledge_plus",
    "canned_reply",
    "human_handoff",
    "appointment",
    "create_task",
    "product_suggest",
    "condition",
    "timeout_followup",
    "close_chat",
    "end",
)

# The triggers a flow can start on. Mirror of the frontend's
# FALLBACK_TRIGGER_TYPES; the builder fetches this list from
# GET /api/reply-flows/trigger-types so the two never drift. Only
# ``new_conversation`` and ``message_received`` are driven by the live message
# path today; the event-driven ones (appointments, calls, tasks, silence) are
# recognised and stored so a flow can be authored against them, and are fired by
# their originating services as those hooks are added.
TRIGGER_TYPES: tuple[dict[str, Any], ...] = (
    {
        "key": "new_conversation",
        "label": "New conversation",
        "category": "Conversation",
        "description": "Starts when a customer opens a new chat.",
        "config_fields": [],
    },
    {
        "key": "conversation_closed",
        "label": "Conversation closed",
        "category": "Conversation",
        "description": "Starts when a conversation is closed.",
        "config_fields": [],
    },
    {
        "key": "appointment_created",
        "label": "Appointment created",
        "category": "Appointments",
        "description": "Starts when a new appointment is booked.",
        "config_fields": [],
    },
    {
        "key": "appointment_completed",
        "label": "Appointment completed",
        "category": "Appointments",
        "description": "Starts when an appointment is marked completed.",
        "config_fields": [],
    },
    {
        "key": "appointment_reminder",
        "label": "Appointment reminder",
        "category": "Appointments",
        "description": "Starts ahead of a scheduled appointment.",
        "config_fields": [
            {
                "key": "minutes_before",
                "label": "Minutes before the appointment",
                "type": "number",
                "placeholder": "60",
            }
        ],
    },
    {
        "key": "call_logged",
        "label": "Call logged",
        "category": "Calls",
        "description": "Starts when a call is logged.",
        "config_fields": [],
    },
    {
        "key": "task_completed",
        "label": "Task completed",
        "category": "Tasks",
        "description": "Starts when a task linked to this customer is marked done.",
        "config_fields": [],
    },
    {
        "key": "customer_no_reply",
        "label": "Customer went silent",
        "category": "Conversation",
        "description": (
            "Starts when the customer has not replied for a set number of "
            "minutes after our last message."
        ),
        "config_fields": [
            {
                "key": "minutes_of_silence",
                "label": "Minutes of customer silence",
                "type": "number",
                "placeholder": "60",
            }
        ],
    },
    {
        "key": "team_no_reply",
        "label": "Team hasn't replied",
        "category": "Conversation",
        "description": (
            "Starts when a customer has been waiting on a human reply for a set "
            "number of minutes."
        ),
        "config_fields": [
            {
                "key": "minutes_waiting",
                "label": "Minutes the customer has been waiting",
                "type": "number",
                "placeholder": "30",
            }
        ],
    },
)

TRIGGER_KEYS = frozenset(t["key"] for t in TRIGGER_TYPES)
DEFAULT_TRIGGER = "new_conversation"


class ReplyFlowError(Exception):
    """A flow that cannot be stored, with a reason a person may read."""


def _string_list(value: Any, allowed: tuple[str, ...] | None = None) -> list[str]:
    """A clean list of strings, optionally filtered to an allowed vocabulary.

    Departments are free text (a company names its own), so ``allowed`` is only
    passed for the fixed vocabularies (channels, reply modes) where an unknown
    value is a bug to drop, not a company's own word to keep.
    """
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        if allowed is not None and text not in allowed:
            continue
        if text not in out:
            out.append(text)
    return out


def _clean_graph(graph: Any) -> dict[str, list]:
    """A graph reduced to what the builder and engine both rely on.

    Node ``data`` (nodeType, label, config) and position are kept verbatim so a
    round trip through the server never loses what the owner drew; anything else
    is dropped. Raises when the node count is over the ceiling.
    """
    if not isinstance(graph, dict):
        return {"nodes": [], "edges": []}

    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    edges = raw_edges if isinstance(raw_edges, list) else []

    if len(nodes) > MAX_NODES:
        raise ReplyFlowError(f"A flow can have at most {MAX_NODES} steps.")

    return {"nodes": nodes, "edges": edges}


def _row_summary(row: Any) -> dict[str, Any]:
    data = dict(row)
    graph = json.loads(data.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    return {
        "id": data["id"],
        "name": data["name"],
        "status": data["status"],
        "channels": json.loads(data.get("channels_json") or "[]"),
        "departments": json.loads(data.get("departments_json") or "[]"),
        "reply_modes": json.loads(data.get("reply_modes_json") or "[]"),
        "trigger_type": data.get("trigger_type") or DEFAULT_TRIGGER,
        "node_count": len(nodes) if isinstance(nodes, list) else 0,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _row_full(row: Any) -> dict[str, Any]:
    data = dict(row)
    graph = json.loads(data.get("graph_json") or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    edges = graph.get("edges") if isinstance(graph, dict) else []
    summary = _row_summary(row)
    summary.update(
        {
            "trigger_config": json.loads(data.get("trigger_config_json") or "{}"),
            "nodes": nodes if isinstance(nodes, list) else [],
            "edges": edges if isinstance(edges, list) else [],
        }
    )
    return summary


class ReplyFlowService:
    # ------------------------------------------------------------------ reads

    def list(self, company_id: int) -> list[dict[str, Any]]:
        with database_manager.tenant(int(company_id)) as conn:
            rows = conn.execute(
                "SELECT id, name, status, channels_json, departments_json, "
                "reply_modes_json, trigger_type, graph_json, created_at, updated_at "
                "FROM reply_flows WHERE company_id = ? "
                "ORDER BY updated_at DESC, id DESC",
                (int(company_id),),
            ).fetchall()
        return [_row_summary(r) for r in rows]

    def get(self, company_id: int, flow_id: int) -> dict[str, Any] | None:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT * FROM reply_flows WHERE id = ? AND company_id = ?",
                (int(flow_id), int(company_id)),
            ).fetchone()
        return _row_full(row) if row else None

    # ----------------------------------------------------------------- writes

    def _validate_name(self, name: str) -> str:
        name = str(name or "").strip()
        if not name:
            raise ReplyFlowError("A flow needs a name.")
        if len(name) > MAX_NAME_LENGTH:
            raise ReplyFlowError(
                f"A flow name must be under {MAX_NAME_LENGTH} characters."
            )
        return name

    def _validate_trigger(self, trigger_type: Any) -> str:
        trigger_type = str(trigger_type or DEFAULT_TRIGGER).strip()
        if trigger_type not in TRIGGER_KEYS:
            raise ReplyFlowError("That trigger is not one this platform offers.")
        return trigger_type

    def create(
        self,
        *,
        company_id: int,
        name: str,
        channels: list[str] | None = None,
        departments: list[str] | None = None,
        reply_modes: list[str] | None = None,
    ) -> dict[str, Any]:
        name = self._validate_name(name)
        now = utc_now_iso()

        with database_manager.tenant(int(company_id)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM reply_flows WHERE company_id = ?",
                (int(company_id),),
            ).fetchone()["n"]
            if int(count) >= MAX_FLOWS:
                raise ReplyFlowError(
                    f"A company can have at most {MAX_FLOWS} reply flows."
                )

            cursor = conn.execute(
                "INSERT INTO reply_flows "
                "(company_id, name, status, channels_json, departments_json, "
                " reply_modes_json, trigger_type, trigger_config_json, graph_json, "
                " created_at, updated_at) "
                "VALUES (?, ?, 'draft', ?, ?, ?, ?, '{}', ?, ?, ?)",
                (
                    int(company_id),
                    name,
                    json.dumps(_string_list(channels, CHANNEL_OPTIONS)),
                    json.dumps(_string_list(departments)),
                    json.dumps(_string_list(reply_modes, REPLY_MODE_OPTIONS)),
                    DEFAULT_TRIGGER,
                    json.dumps({"nodes": [], "edges": []}),
                    now,
                    now,
                ),
            )
            conn.commit()
            new_id = int(cursor.lastrowid)
            row = conn.execute(
                "SELECT * FROM reply_flows WHERE id = ? AND company_id = ?",
                (new_id, int(company_id)),
            ).fetchone()
        return _row_full(row)

    def update(
        self,
        *,
        company_id: int,
        flow_id: int,
        name: str,
        status: str,
        channels: list[str] | None,
        departments: list[str] | None,
        reply_modes: list[str] | None,
        trigger_type: str,
        trigger_config: dict | None,
        nodes: list | None,
        edges: list | None,
    ) -> dict[str, Any]:
        name = self._validate_name(name)
        trigger_type = self._validate_trigger(trigger_type)

        status = str(status or "draft").strip()
        if status not in VALID_STATUSES:
            raise ReplyFlowError("A flow's status must be draft, active or archived.")

        graph = _clean_graph({"nodes": nodes or [], "edges": edges or []})
        config = trigger_config if isinstance(trigger_config, dict) else {}

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                "UPDATE reply_flows SET name = ?, status = ?, channels_json = ?, "
                "departments_json = ?, reply_modes_json = ?, trigger_type = ?, "
                "trigger_config_json = ?, graph_json = ?, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                (
                    name,
                    status,
                    json.dumps(_string_list(channels, CHANNEL_OPTIONS)),
                    json.dumps(_string_list(departments)),
                    json.dumps(_string_list(reply_modes, REPLY_MODE_OPTIONS)),
                    trigger_type,
                    json.dumps(config),
                    json.dumps(graph),
                    utc_now_iso(),
                    int(flow_id),
                    int(company_id),
                ),
            )
            conn.commit()
            if cursor.rowcount != 1:
                raise ReplyFlowError("That flow does not exist.")
            row = conn.execute(
                "SELECT * FROM reply_flows WHERE id = ? AND company_id = ?",
                (int(flow_id), int(company_id)),
            ).fetchone()
        return _row_full(row)

    def set_graph(
        self, *, company_id: int, flow_id: int, nodes: list, edges: list,
        trigger_type: str | None = None, trigger_config: dict | None = None,
    ) -> dict[str, Any] | None:
        """Replace only the graph (and optionally the trigger) of a flow.

        Used by the AI generate-from-text endpoint, which rewrites the steps
        without touching the flow's name, scope or status.
        """
        graph = _clean_graph({"nodes": nodes or [], "edges": edges or []})
        with database_manager.tenant(int(company_id)) as conn:
            existing = conn.execute(
                "SELECT trigger_type, trigger_config_json FROM reply_flows "
                "WHERE id = ? AND company_id = ?",
                (int(flow_id), int(company_id)),
            ).fetchone()
            if not existing:
                return None

            new_trigger = existing["trigger_type"]
            new_config = existing["trigger_config_json"]
            if trigger_type and trigger_type in TRIGGER_KEYS:
                new_trigger = trigger_type
                new_config = json.dumps(
                    trigger_config if isinstance(trigger_config, dict) else {}
                )

            conn.execute(
                "UPDATE reply_flows SET graph_json = ?, trigger_type = ?, "
                "trigger_config_json = ?, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                (
                    json.dumps(graph),
                    new_trigger,
                    new_config,
                    utc_now_iso(),
                    int(flow_id),
                    int(company_id),
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM reply_flows WHERE id = ? AND company_id = ?",
                (int(flow_id), int(company_id)),
            ).fetchone()
        return _row_full(row)

    def delete(self, *, company_id: int, flow_id: int) -> bool:
        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                "DELETE FROM reply_flows WHERE id = ? AND company_id = ?",
                (int(flow_id), int(company_id)),
            )
            conn.commit()
            return cursor.rowcount > 0

    def duplicate(self, *, company_id: int, flow_id: int) -> dict[str, Any] | None:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                "SELECT * FROM reply_flows WHERE id = ? AND company_id = ?",
                (int(flow_id), int(company_id)),
            ).fetchone()
            if not row:
                return None

            count = conn.execute(
                "SELECT COUNT(*) AS n FROM reply_flows WHERE company_id = ?",
                (int(company_id),),
            ).fetchone()["n"]
            if int(count) >= MAX_FLOWS:
                raise ReplyFlowError(
                    f"A company can have at most {MAX_FLOWS} reply flows."
                )

            now = utc_now_iso()
            # A copy is always a draft: duplicating an active flow must never
            # quietly put a second live flow in front of customers.
            cursor = conn.execute(
                "INSERT INTO reply_flows "
                "(company_id, name, status, channels_json, departments_json, "
                " reply_modes_json, trigger_type, trigger_config_json, graph_json, "
                " created_at, updated_at) "
                "VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(company_id),
                    f"{row['name']} (copy)"[:MAX_NAME_LENGTH],
                    row["channels_json"],
                    row["departments_json"],
                    row["reply_modes_json"],
                    row["trigger_type"],
                    row["trigger_config_json"],
                    row["graph_json"],
                    now,
                    now,
                ),
            )
            conn.commit()
            new_id = int(cursor.lastrowid)
            new_row = conn.execute(
                "SELECT * FROM reply_flows WHERE id = ? AND company_id = ?",
                (new_id, int(company_id)),
            ).fetchone()
        return _row_full(new_row)

    # -------------------------------------------------------- the engine reads

    def active_flow_for(
        self,
        company_id: int,
        *,
        channel: str | None = None,
        department: str | None = None,
        trigger_type: str = DEFAULT_TRIGGER,
    ) -> dict[str, Any] | None:
        """The one active flow that should run for this message, or None.

        A flow matches when its trigger is the one asked for, its channel scope
        is empty or includes this channel, and its department scope is empty or
        includes this department. When several match, the most specific wins
        (a flow that named this channel and department beats a catch-all), and
        ties break on the most recently updated -- so an owner's newest intent
        is the one that runs.
        """
        try:
            rows = self.list(int(company_id))
        except Exception:  # noqa: BLE001
            logger.exception("Could not read reply flows for company %s", company_id)
            return None

        chan = str(channel or "").strip().lower()
        dept = str(department or "").strip().lower()

        best: dict[str, Any] | None = None
        best_score = -1
        for summary in rows:
            if summary["status"] != "active":
                continue
            if summary["trigger_type"] != trigger_type:
                continue

            channels = [c.lower() for c in summary["channels"]]
            departments = [d.lower() for d in summary["departments"]]

            if channels and chan not in channels:
                continue
            if departments and dept not in departments:
                continue

            score = (1 if channels else 0) + (1 if departments else 0)
            if score > best_score:
                best = summary
                best_score = score

        if not best:
            return None
        return self.get(int(company_id), int(best["id"]))


reply_flow_service = ReplyFlowService()
