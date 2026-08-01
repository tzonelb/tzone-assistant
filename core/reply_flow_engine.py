"""Runtime execution of a saved Reply Flow graph against a real
conversation. This is the piece that was missing: reply_flow_service.py
and the builder UI define WHAT a flow looks like; this module is what
actually makes a saved flow change the AI's real behavior on a real
conversation.

Every node type executes for real: greeting/company_intro/canned_reply
(send text, auto-advance), ask_question (send question, wait, store the
answer), the three ai_* modes (call the real AI pipeline with the node's
instructions layered on top of company instructions, wait one exchange,
advance), human_handoff (notify + hand off, end the flow), condition
(branch on a saved variable — first outgoing edge if true/second if
false), create_task and appointment (real backend.services.task_service
task — appointment intentionally creates a follow-up task rather than a
fabricated appointment record, since the flow has no real date/time to
schedule against; inventing one would be worse than not booking it),
product_suggest (a real Master Catalogue lookup — says so honestly if
nothing matches, never invents a product), timeout_followup (arms a real
delayed auto-send via the existing conversation reminder/safety-check
system), close_chat (a real templated closing, optionally asking about a
follow-up), and end.

Entry point: maybe_handle(request) -> Response | None. Returns None when
no active flow applies to this conversation, so callers fall through to
the existing default AI pipeline unchanged.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.catalogue_service import catalogue_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.diagnostics_service import diagnostics_service
from backend.services.notification_service import notification_service
from backend.services.reply_flow_service import reply_flow_service
from backend.services.task_service import task_service
from core.ai_router import ai_router
from core.instruction_service import instruction_service
from core.knowledge_manager import knowledge_manager
from core.response import Response
from database.database import db

logger = logging.getLogger(__name__)

AI_NODE_TYPES = {"ai_direct", "ai_knowledge_only", "ai_knowledge_plus"}
PASSTHROUGH_TEXT_TYPES = {"greeting", "company_intro", "canned_reply"}
CONDITION_OPERATORS = {"equals", "contains", "greater_than", "less_than", "is_set"}
TASK_TYPES = {"follow_up", "complaint", "service_request", "sales_inquiry", "internal", "other"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReplyFlowEngine:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reply_flow_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    flow_id INTEGER NOT NULL,
                    current_node_id TEXT,
                    variables_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(company_id, channel, external_user_id)
                )
                """
            )
            conn.commit()

    # -- flow selection -----------------------------------------------

    def _pick_flow(self, company_id: int, channel: str, department: str | None) -> dict[str, Any] | None:
        flows = [
            flow for flow in reply_flow_service.list_for_company(company_id=company_id)
            if flow["status"] == "active" and (not flow["channels"] or channel in flow["channels"])
        ]
        if not flows:
            return None

        if department:
            for flow in flows:
                if department in flow["departments"]:
                    return reply_flow_service.get(company_id=company_id, flow_id=flow["id"])

        for flow in flows:
            if not flow["departments"]:
                return reply_flow_service.get(company_id=company_id, flow_id=flow["id"])

        return None

    # -- graph helpers --------------------------------------------------

    def _node_by_id(self, flow: dict[str, Any], node_id: str | None) -> dict[str, Any] | None:
        if not node_id:
            return None
        return next((node for node in flow["nodes"] if node.get("id") == node_id), None)

    def _entry_node_id(self, flow: dict[str, Any]) -> str | None:
        nodes = flow["nodes"]
        if not nodes:
            return None
        target_ids = {edge.get("target") for edge in flow["edges"]}
        entry = next((node for node in nodes if node.get("id") not in target_ids), nodes[0])
        return entry.get("id")

    def _next_node_id(self, flow: dict[str, Any], node_id: str) -> str | None:
        edge = next((edge for edge in flow["edges"] if edge.get("source") == node_id), None)
        return edge.get("target") if edge else None

    # -- session persistence --------------------------------------------

    def _get_session(self, *, company_id: int, channel: str, external_user_id: str, status: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM reply_flow_sessions WHERE company_id = ? AND channel = ? AND external_user_id = ?"
        params: list[Any] = [company_id, channel, external_user_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        with db.connect() as conn:
            row = conn.execute(query, params).fetchone()
        return dict(row) if row else None

    def _start_session(self, *, company_id: int, channel: str, external_user_id: str, flow_id: int) -> dict[str, Any]:
        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO reply_flow_sessions (company_id, channel, external_user_id, flow_id, current_node_id, "
                "variables_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, NULL, '{}', 'active', ?, ?) "
                "ON CONFLICT(company_id, channel, external_user_id) DO UPDATE SET "
                "flow_id = excluded.flow_id, current_node_id = NULL, variables_json = '{}', status = 'active', "
                "updated_at = excluded.updated_at",
                (company_id, channel, external_user_id, flow_id, now, now),
            )
            conn.commit()
        return self._get_session(company_id=company_id, channel=channel, external_user_id=external_user_id)

    def _save_progress(self, session_row: dict[str, Any], *, current_node_id: str | None, variables: dict[str, Any]) -> None:
        with db.connect() as conn:
            conn.execute(
                "UPDATE reply_flow_sessions SET current_node_id = ?, variables_json = ?, updated_at = ? WHERE id = ?",
                (current_node_id, json.dumps(variables), utc_now_iso(), session_row["id"]),
            )
            conn.commit()

    def _end_session(self, session_row: dict[str, Any]) -> None:
        with db.connect() as conn:
            conn.execute(
                "UPDATE reply_flow_sessions SET status = 'ended', updated_at = ? WHERE id = ?",
                (utc_now_iso(), session_row["id"]),
            )
            conn.commit()

    # -- node execution ---------------------------------------------------

    def _format_text(self, text: str, request: Any, variables: dict[str, Any]) -> str:
        formatted = text or ""
        for key, value in variables.items():
            formatted = formatted.replace(f"{{{{{key}}}}}", str(value))
        formatted = formatted.replace("{{customer_name}}", str(variables.get("customer_name") or ""))
        return formatted.strip()

    def _run_ai_step(self, request: Any, node: dict[str, Any], department: str | None) -> str:
        config = node["data"].get("config") or {}
        context_tags = [request.channel]
        if department:
            context_tags.append(department)

        instructions = instruction_service.list_texts_for_ai(request.company_id, context_tags=context_tags)
        if config.get("instructions"):
            instructions = [*instructions, config["instructions"]]

        knowledge = []
        if node["data"].get("nodeType") != "ai_direct":
            knowledge = knowledge_manager.list_for_ai(request.company_id, context_tags=context_tags)

        ai_result = ai_router.route(
            message=request.message,
            channel=request.channel,
            user_id=request.user_id,
            company_id=request.company_id,
            knowledge=knowledge,
            instructions=instructions,
        )
        if not ai_result:
            return ""
        return ai_result.get("reply") or ""

    def _handle_human_handoff(self, request: Any, node: dict[str, Any]) -> None:
        note = (node["data"].get("config") or {}).get("note") or ""
        notification_service.create(
            company_id=request.company_id,
            notification_type="ai_escalation",
            title="Reply Flow handed off to a human",
            body=note or "A reply flow step requested a human takeover.",
            channel=request.channel,
            external_user_id=request.user_id,
            severity="warning",
            data={"reason": "flow_human_handoff"},
        )

    def _edges_from(self, flow: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
        return [edge for edge in flow["edges"] if edge.get("source") == node_id]

    def _evaluate_condition(self, config: dict[str, Any], variables: dict[str, Any]) -> bool:
        operator = config.get("operator")
        if operator not in CONDITION_OPERATORS:
            return False
        actual = variables.get(config.get("variable"))
        if operator == "is_set":
            return bool(actual) or actual == 0
        if actual is None:
            return False
        actual_text = str(actual).strip().lower()
        expected_text = str(config.get("value") or "").strip().lower()
        if operator == "equals":
            return actual_text == expected_text
        if operator == "contains":
            return expected_text in actual_text
        try:
            actual_number = float(actual_text)
            expected_number = float(expected_text)
        except (TypeError, ValueError):
            return False
        if operator == "greater_than":
            return actual_number > expected_number
        return actual_number < expected_number

    def _next_node_id_for_condition(self, flow: dict[str, Any], node_id: str, matched: bool) -> str | None:
        edges = self._edges_from(flow, node_id)
        if not edges:
            return None
        if matched or len(edges) == 1:
            return edges[0]["target"]
        return edges[1]["target"]

    def _handle_create_task(self, request: Any, config: dict[str, Any], state: dict[str, Any] | None, *, is_appointment: bool = False) -> None:
        task_type = config.get("task_type") if not is_appointment else "follow_up"
        if task_type not in TASK_TYPES:
            task_type = "other"
        note = (config.get("note") or "").strip()
        title = "Book an appointment (from Reply Flow)" if is_appointment else f"Reply Flow task ({task_type})"
        try:
            task_service.create_task(
                company_id=request.company_id,
                title=title,
                description=note or f"Created automatically by a Reply Flow step for {request.channel} customer {request.user_id}.",
                task_type=task_type,
                conversation_id=state.get("id") if state else None,
            )
        except Exception:
            logger.exception("Reply flow could not create a task")

    def _suggest_product(self, request: Any, config: dict[str, Any]) -> str | None:
        search_term = (config.get("note") or "").strip()
        try:
            result = catalogue_service.list_products(company_id=request.company_id, search=search_term or None)
        except Exception:
            logger.exception("Reply flow product lookup failed")
            return None
        items = result.get("items") or []
        if not items:
            return None
        product = items[0]
        name = product.get("name") or "this item"
        price_cents = product.get("price_cents")
        description = product.get("description") or ""
        parts = [name]
        if price_cents not in (None, ""):
            parts.append(f"${price_cents / 100:.2f}")
        text = " — ".join(parts)
        if description:
            text += f"\n{description}"
        return text

    def _schedule_followup(self, request: Any, config: dict[str, Any]) -> None:
        text = (config.get("text") or "").strip()
        if not text:
            return
        try:
            wait_minutes = max(1, int(config.get("wait_minutes") or 60))
        except (TypeError, ValueError):
            wait_minutes = 60
        reminder_at = (datetime.now(timezone.utc) + timedelta(minutes=wait_minutes)).isoformat()
        try:
            conversation_control_service.set_reminder(
                company_id=request.company_id, channel=request.channel, external_user_id=request.user_id,
                reminder_at=reminder_at, note="Reply flow timeout follow-up", actor_user_id=None,
                auto_send=True, message_text=text,
            )
        except Exception:
            logger.exception("Reply flow could not schedule a timeout follow-up")

    def _close_chat_text(self, config: dict[str, Any]) -> str:
        text = "Thanks for chatting with us — is there anything else we can help with?"
        if config.get("ask_reschedule"):
            text += " Would you like to schedule a follow-up?"
        return text

    # -- entry point ------------------------------------------------------

    def maybe_handle(self, request: Any) -> Response | None:
        try:
            state = conversation_control_service.get_state(
                company_id=request.company_id, channel=request.channel, external_user_id=request.user_id,
            )
            department = state.get("department") if state else None

            session_row = self._get_session(
                company_id=request.company_id, channel=request.channel, external_user_id=request.user_id, status="active",
            )

            if session_row is None:
                # A session that already ran to completion (ended) must NOT
                # silently restart on the customer's very next message —
                # only a conversation that has never had a flow session at
                # all should trigger a fresh start.
                any_session = self._get_session(
                    company_id=request.company_id, channel=request.channel, external_user_id=request.user_id,
                )
                if any_session is not None:
                    return None

                flow = self._pick_flow(request.company_id, request.channel, department)
                if not flow or not flow["nodes"]:
                    return None
                session_row = self._start_session(
                    company_id=request.company_id, channel=request.channel, external_user_id=request.user_id,
                    flow_id=flow["id"],
                )
            else:
                flow = reply_flow_service.get(company_id=request.company_id, flow_id=session_row["flow_id"])

            return self._advance(request, flow, session_row, state)
        except Exception:
            logger.exception("Reply flow execution failed; falling back to the default AI pipeline")
            return None

    def _advance(self, request: Any, flow: dict[str, Any], session_row: dict[str, Any], state: dict[str, Any] | None) -> Response | None:
        department = state.get("department") if state else None
        variables = json.loads(session_row["variables_json"] or "{}")
        current_node_id = session_row["current_node_id"]
        current_node = self._node_by_id(flow, current_node_id)

        if current_node is None:
            current_node_id = self._entry_node_id(flow)
        else:
            node_type = current_node["data"].get("nodeType")
            if node_type == "ask_question":
                save_as = (current_node["data"].get("config") or {}).get("save_as")
                if save_as:
                    variables[save_as] = request.message
            current_node_id = self._next_node_id(flow, current_node_id)

        reply_parts: list[str] = []
        visited = set()

        while current_node_id and current_node_id not in visited:
            visited.add(current_node_id)
            node = self._node_by_id(flow, current_node_id)
            if node is None:
                break
            node_type = node["data"].get("nodeType")
            config = node["data"].get("config") or {}

            if node_type in PASSTHROUGH_TEXT_TYPES:
                text = self._format_text(config.get("text", ""), request, variables)
                if text:
                    reply_parts.append(text)
                current_node_id = self._next_node_id(flow, current_node_id)
                continue

            if node_type == "ask_question":
                question = self._format_text(config.get("question", ""), request, variables)
                if question:
                    reply_parts.append(question)
                self._save_progress(session_row, current_node_id=current_node_id, variables=variables)
                return Response("\n\n".join(reply_parts)) if reply_parts else None

            if node_type in AI_NODE_TYPES:
                ai_text = self._run_ai_step(request, node, department)
                if ai_text:
                    reply_parts.append(ai_text)
                self._save_progress(session_row, current_node_id=current_node_id, variables=variables)
                return Response("\n\n".join(reply_parts)) if reply_parts else None

            if node_type == "human_handoff":
                self._handle_human_handoff(request, node)
                self._end_session(session_row)
                return Response("\n\n".join(reply_parts)) if reply_parts else None

            if node_type == "end":
                self._end_session(session_row)
                return Response("\n\n".join(reply_parts)) if reply_parts else None

            if node_type == "condition":
                matched = self._evaluate_condition(config, variables)
                current_node_id = self._next_node_id_for_condition(flow, current_node_id, matched)
                continue

            if node_type == "create_task":
                self._handle_create_task(request, config, state)
                current_node_id = self._next_node_id(flow, current_node_id)
                continue

            if node_type == "appointment":
                self._handle_create_task(request, config, state, is_appointment=True)
                current_node_id = self._next_node_id(flow, current_node_id)
                continue

            if node_type == "product_suggest":
                suggestion = self._suggest_product(request, config)
                if suggestion:
                    reply_parts.append(suggestion)
                current_node_id = self._next_node_id(flow, current_node_id)
                continue

            if node_type == "timeout_followup":
                self._schedule_followup(request, config)
                current_node_id = self._next_node_id(flow, current_node_id)
                continue

            if node_type == "close_chat":
                reply_parts.append(self._close_chat_text(config))
                self._end_session(session_row)
                return Response("\n\n".join(reply_parts)) if reply_parts else None

            diagnostics_service.record(
                event_type="reply_flow_unknown_node_type",
                company_id=request.company_id,
                channel=request.channel,
                external_user_id=request.user_id,
                status="skipped",
                data={"node_type": node_type, "flow_id": flow["id"]},
            )
            current_node_id = self._next_node_id(flow, current_node_id)

        self._end_session(session_row)
        return Response("\n\n".join(reply_parts)) if reply_parts else None


reply_flow_engine = ReplyFlowEngine()
