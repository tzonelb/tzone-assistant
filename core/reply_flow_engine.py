"""Runtime execution of a saved Reply Flow graph against a real
conversation. This is the piece that was missing: reply_flow_service.py
and the builder UI define WHAT a flow looks like; this module is what
actually makes a saved flow change the AI's real behavior on a real
conversation.

Deliberately scoped v1: supports the node types that can be executed
safely and unambiguously right now — greeting/company_intro/canned_reply
(send text, auto-advance), ask_question (send question, wait, store the
answer), the three ai_* modes (call the real AI pipeline with the node's
instructions layered on top of company instructions, wait one exchange,
advance), human_handoff (notify + hand off, end the flow), and end.
condition/appointment/create_task/product_suggest/timeout_followup/
close_chat are recognized but not yet executed — passed through with a
logged diagnostic event rather than silently skipped or crashing, exactly
like every other "not wired yet" gap flagged elsewhere in this codebase.

Entry point: maybe_handle(request) -> Response | None. Returns None when
no active flow applies to this conversation, so callers fall through to
the existing default AI pipeline unchanged.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.services.conversation_control_service import conversation_control_service
from backend.services.diagnostics_service import diagnostics_service
from backend.services.notification_service import notification_service
from backend.services.reply_flow_service import reply_flow_service
from core.ai_router import ai_router
from core.instruction_service import instruction_service
from core.knowledge_manager import knowledge_manager
from core.response import Response
from database.database import db

logger = logging.getLogger(__name__)

AI_NODE_TYPES = {"ai_direct", "ai_knowledge_only", "ai_knowledge_plus"}
PASSTHROUGH_TEXT_TYPES = {"greeting", "company_intro", "canned_reply"}
NOT_YET_EXECUTED_TYPES = {"condition", "appointment", "create_task", "product_suggest", "timeout_followup", "close_chat"}


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

    def _get_session(self, *, company_id: int, channel: str, external_user_id: str) -> dict[str, Any] | None:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM reply_flow_sessions WHERE company_id = ? AND channel = ? AND external_user_id = ? "
                "AND status = 'active'",
                (company_id, channel, external_user_id),
            ).fetchone()
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

    # -- entry point ------------------------------------------------------

    def maybe_handle(self, request: Any) -> Response | None:
        try:
            state = conversation_control_service.get_state(
                company_id=request.company_id, channel=request.channel, external_user_id=request.user_id,
            )
            department = state.get("department") if state else None

            session_row = self._get_session(
                company_id=request.company_id, channel=request.channel, external_user_id=request.user_id,
            )

            if session_row is None:
                flow = self._pick_flow(request.company_id, request.channel, department)
                if not flow or not flow["nodes"]:
                    return None
                session_row = self._start_session(
                    company_id=request.company_id, channel=request.channel, external_user_id=request.user_id,
                    flow_id=flow["id"],
                )
            else:
                flow = reply_flow_service.get(company_id=request.company_id, flow_id=session_row["flow_id"])

            return self._advance(request, flow, session_row, department)
        except Exception:
            logger.exception("Reply flow execution failed; falling back to the default AI pipeline")
            return None

    def _advance(self, request: Any, flow: dict[str, Any], session_row: dict[str, Any], department: str | None) -> Response | None:
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

            if node_type in NOT_YET_EXECUTED_TYPES:
                diagnostics_service.record(
                    event_type="reply_flow_node_not_executed",
                    company_id=request.company_id,
                    channel=request.channel,
                    external_user_id=request.user_id,
                    status="skipped",
                    data={"node_type": node_type, "flow_id": flow["id"]},
                )
                current_node_id = self._next_node_id(flow, current_node_id)
                continue

            current_node_id = self._next_node_id(flow, current_node_id)

        self._end_session(session_row)
        return Response("\n\n".join(reply_parts)) if reply_parts else None


reply_flow_engine = ReplyFlowEngine()
