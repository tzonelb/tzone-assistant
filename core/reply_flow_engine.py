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
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.services.catalogue_service import catalogue_service
from backend.services.conversation_control_service import conversation_control_service
from backend.services.diagnostics_service import diagnostics_service
from backend.services.notification_service import notification_service
from backend.services.reply_flow_service import reply_flow_service
from backend.services.task_service import task_service
from channels.meta.sender import send_meta_buttons
from channels.telegram.sender import send_telegram_buttons
from channels.whatsapp.sender import send_whatsapp_text
from config.settings import config
from core.ai_router import ai_router
from core.conversation_store import save_conversation_message
from core.instruction_service import instruction_service
from core.knowledge_manager import knowledge_manager
from core.reply_flow_triggers import DEFAULT_TRIGGER_TYPE
from core.request import Request
from core.response import Response
from database.database import db

logger = logging.getLogger(__name__)

# Guards the get-session -> advance -> save-progress critical section per
# conversation. smart_reply.py's own _LOCK only serializes the message
# BUFFER (it's released before the slow AI call runs), so two overlapping
# calls to maybe_handle() for the same conversation (e.g. a customer
# message arriving while the previous AI turn is still in flight) could
# otherwise race on reply_flow_sessions — losing an ask_question answer or
# double-firing a create_task/appointment node, even with no loop in the
# flow at all. A lock per (company, channel, external_user_id) key fixes
# this without slowing down unrelated conversations.
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(company_id: int, channel: str, external_user_id: str) -> threading.Lock:
    key = f"{company_id}:{channel}:{external_user_id}"
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SESSION_LOCKS[key] = lock
        return lock

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
            # Claim markers for the two time-based no-reply triggers (same
            # claim-then-act discipline as appointments.flow_reminder_sent_at):
            # each stores the conversation-activity value it last fired for,
            # so one silence/waiting period fires at most once and fresh
            # activity re-arms the trigger naturally.
            existing = {
                row[1] for row in conn.execute("PRAGMA table_info(conversations)")
            }
            if "customer_no_reply_fired_for" not in existing:
                conn.execute(
                    "ALTER TABLE conversations "
                    "ADD COLUMN customer_no_reply_fired_for TEXT"
                )
            if "team_no_reply_fired_for" not in existing:
                conn.execute(
                    "ALTER TABLE conversations "
                    "ADD COLUMN team_no_reply_fired_for TEXT"
                )
            conn.commit()

    # -- flow selection -----------------------------------------------

    def _pick_flow(self, company_id: int, channel: str, department: str | None) -> dict[str, Any] | None:
        """Today's ONLY flow-selection path, unchanged: implicitly triggered by
        ANY new incoming customer message. Now just the `new_conversation`
        trigger type under the hood — every flow already in the database
        defaults to this trigger via the schema migration, so behavior for
        existing flows is byte-for-byte identical to before triggers existed."""
        return self._pick_flow_for_trigger(company_id, DEFAULT_TRIGGER_TYPE, channel, department)

    def _pick_flow_for_trigger(
        self, company_id: int, trigger_type: str, channel: str | None, department: str | None,
    ) -> dict[str, Any] | None:
        """Generic version of _pick_flow, keyed by trigger type instead of
        being hardcoded to the implicit new-message trigger. Same channel/
        department precedence: an exact department match wins, otherwise the
        first flow with no department restriction, otherwise nothing."""
        flows = [
            flow for flow in reply_flow_service.list_for_company(company_id=company_id)
            if flow["status"] == "active"
            and (flow.get("trigger_type") or DEFAULT_TRIGGER_TYPE) == trigger_type
            and (not flow["channels"] or not channel or channel in flow["channels"])
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

    # -- event-based triggers ---------------------------------------------
    # conversation_closed / appointment_created / appointment_completed /
    # appointment_reminder / call_logged all land here. Each is a real hook
    # in the service that owns that event (conversation_control_service,
    # appointment_service, call_log_service) or, for the time-based
    # appointment_reminder, a periodic scan alongside main.py's existing
    # reminder_worker() loop. Every one of them funnels into fire_event,
    # which reuses the exact same _start_session/_advance machinery
    # message-triggered flows use — no parallel execution path.

    def fire_event(
        self, *, company_id: int, trigger_type: str, channel: str, external_user_id: str,
        department: str | None = None,
    ) -> None:
        """Starts a brand-new flow session for the first active flow whose
        trigger_type/channel/department matches, then dispatches whatever it
        sends through the same real per-channel senders the message-triggered
        path uses (channels/meta/smart_reply.py's _finish_pending dispatches
        the same three functions). A closed-conversation/appointment/call flow
        is a fresh, separate mini-conversation — starting it reuses (and
        resets) the same one session slot a normal new_conversation flow would
        use for this channel+external_user_id, since only one flow session can
        be active per conversation at a time; that's fine here because the
        conversation the trigger fired on is, by definition, wrapping up.
        Never raises — a broken trigger flow must never block the real event
        (a status change, a booked appointment, a logged call) that fired it."""
        try:
            flow = self._pick_flow_for_trigger(company_id, trigger_type, channel, department)
            if not flow or not flow["nodes"]:
                return

            session_row = self._start_session(
                company_id=company_id, channel=channel, external_user_id=external_user_id, flow_id=flow["id"],
            )
            request = Request(channel=channel, user_id=external_user_id, message="", company_id=company_id)
            state = conversation_control_service.get_state(
                company_id=company_id, channel=channel, external_user_id=external_user_id,
            )
            response = self._advance(request, flow, session_row, state)
            if response is not None and (response.text or response.buttons):
                self._dispatch_event_response(
                    company_id=company_id, channel=channel, external_user_id=external_user_id, response=response,
                )
        except Exception:
            logger.exception("Reply flow trigger '%s' failed for %s/%s", trigger_type, channel, external_user_id)

    def fire_event_for_customer(self, *, company_id: int, customer_id: int | None, trigger_type: str) -> None:
        """Same as fire_event, but for triggers that only know a customer_id
        (appointments, call logs) rather than a channel+external_user_id
        directly — resolves every channel identity that customer has via
        customer_identities and fires the trigger on each. A customer with no
        linked channel identity (e.g. a walk-in appointment with just a phone
        number typed in) has nowhere to send a message, so it's a no-op."""
        if not customer_id:
            return
        try:
            with db.connect() as conn:
                identities = conn.execute(
                    "SELECT channel, external_user_id FROM customer_identities WHERE company_id = ? AND customer_id = ?",
                    (company_id, customer_id),
                ).fetchall()
        except Exception:
            logger.exception("Reply flow trigger '%s' could not resolve customer #%s", trigger_type, customer_id)
            return

        for identity in identities:
            channel = identity["channel"]
            external_user_id = identity["external_user_id"]
            state = conversation_control_service.get_state(
                company_id=company_id, channel=channel, external_user_id=external_user_id,
            )
            self.fire_event(
                company_id=company_id, trigger_type=trigger_type, channel=channel,
                external_user_id=external_user_id, department=state.get("department"),
            )

    def _dispatch_event_response(self, *, company_id: int, channel: str, external_user_id: str, response: Response) -> None:
        """The actual send, for a flow session that has no incoming customer
        message to piggy-back a reply onto (there's no smart_reply.py buffer
        to flush here — the event, not a customer message, is what's driving
        this). Calls the exact same per-channel sender functions
        channels/meta/smart_reply.py's _finish_pending calls for
        message-triggered flows, so a channel that gets real interactive
        buttons there gets them here too."""
        buttons = response.buttons or None
        if channel == "telegram":
            send_result = send_telegram_buttons(recipient_id=external_user_id, text=response.text, buttons=buttons, channel=channel)
        elif channel == "whatsapp":
            send_result = send_whatsapp_text(to=external_user_id, text=response.text, buttons=buttons, company_id=company_id)
        else:
            send_result = send_meta_buttons(recipient_id=external_user_id, text=response.text, buttons=buttons, channel=channel, company_id=company_id)

        save_conversation_message(
            channel=channel, user_id=external_user_id, direction="out", text=response.text,
            metadata={"buttons": buttons, "send_result": send_result, "sender_type": "ai", "source": "reply_flow_trigger"},
        )

    def check_appointment_reminders(self) -> None:
        """Called periodically by main.py's reminder_worker() loop, right
        alongside conversation_control_service.check_due_reminders() — same
        cadence, same fire-and-forget contract. For every active
        appointment_reminder flow, scans that company's scheduled
        appointments for ones now within trigger_config.minutes_before
        minutes of scheduled_at that haven't fired this reminder yet, claims
        each one (flow_reminder_sent_at, same claim-then-act discipline as
        check_due_reminders' reminder_notified_at, so a slow send can never
        cause a double-fire), then fires the flow for that appointment's
        linked customer."""
        try:
            with db.connect() as conn:
                flow_rows = conn.execute(
                    "SELECT company_id, trigger_config FROM reply_flows WHERE status = 'active' AND trigger_type = 'appointment_reminder'",
                ).fetchall()
        except Exception:
            logger.exception("appointment_reminder flow scan failed")
            return

        now = datetime.now(timezone.utc)
        for flow_row in flow_rows:
            company_id = flow_row["company_id"]
            try:
                trigger_config = json.loads(flow_row["trigger_config"] or "{}")
                minutes_before = int(trigger_config.get("minutes_before") or 60)
            except (TypeError, ValueError):
                minutes_before = 60
            minutes_before = max(1, minutes_before)

            window_end = (now + timedelta(minutes=minutes_before)).isoformat()
            # Safety bound: never resurrect a "reminder" for an appointment left
            # sitting in 'scheduled' status long after it should have happened
            # (e.g. the worker was down, or nobody ever marked it completed).
            window_start = (now - timedelta(hours=24)).isoformat()

            try:
                claimed_customer_ids: list[int | None] = []
                with db.connect() as conn:
                    due = conn.execute(
                        """
                        SELECT id, customer_id FROM appointments
                        WHERE company_id = ? AND status = 'scheduled' AND flow_reminder_sent_at IS NULL
                          AND scheduled_at <= ? AND scheduled_at >= ?
                        """,
                        (company_id, window_end, window_start),
                    ).fetchall()
                    for row in due:
                        cursor = conn.execute(
                            "UPDATE appointments SET flow_reminder_sent_at = ? WHERE id = ? AND flow_reminder_sent_at IS NULL",
                            (utc_now_iso(), row["id"]),
                        )
                        if cursor.rowcount:
                            claimed_customer_ids.append(row["customer_id"])
                    conn.commit()
            except Exception:
                logger.exception("appointment_reminder claim failed for company %s", company_id)
                continue

            for customer_id in claimed_customer_ids:
                self.fire_event_for_customer(company_id=company_id, customer_id=customer_id, trigger_type="appointment_reminder")

    def check_no_reply_triggers(self) -> None:
        """Called periodically by main.py's reminder_worker() loop, right
        alongside check_appointment_reminders() — same cadence, same
        fire-and-forget contract. Two time-based conversation triggers:

        - customer_no_reply: the ball is in the customer's court
          (unread_count = 0 — nothing pending from them) and the
          conversation has had no activity for trigger_config.
          minutes_of_silence minutes. Claim marker stores the updated_at
          value fired for, so one silence period fires once and any new
          activity (which changes updated_at) re-arms it.
        - team_no_reply: the customer is waiting on a human
          (workflow_state = 'waiting_agent') and their last message is
          trigger_config.minutes_waiting minutes old. Claim marker
          stores the last_message_at fired for.

        Claim-then-act (UPDATE ... WHERE marker IS NULL OR marker != ?)
        exactly like check_appointment_reminders' flow_reminder_sent_at,
        so a slow send can never double-fire."""
        self._scan_no_reply_trigger(
            trigger_type="customer_no_reply",
            config_key="minutes_of_silence",
            default_minutes=60,
            marker_column="customer_no_reply_fired_for",
            activity_column="updated_at",
            extra_where="COALESCE(unread_count, 0) = 0",
        )
        self._scan_no_reply_trigger(
            trigger_type="team_no_reply",
            config_key="minutes_waiting",
            default_minutes=30,
            marker_column="team_no_reply_fired_for",
            activity_column="last_message_at",
            extra_where="workflow_state = 'waiting_agent'",
        )

    def _scan_no_reply_trigger(
        self,
        *,
        trigger_type: str,
        config_key: str,
        default_minutes: int,
        marker_column: str,
        activity_column: str,
        extra_where: str,
    ) -> None:
        try:
            with db.connect() as conn:
                flow_rows = conn.execute(
                    "SELECT company_id, trigger_config FROM reply_flows "
                    "WHERE status = 'active' AND trigger_type = ?",
                    (trigger_type,),
                ).fetchall()
        except Exception:
            logger.exception("%s flow scan failed", trigger_type)
            return

        now = datetime.now(timezone.utc)
        for flow_row in flow_rows:
            company_id = flow_row["company_id"]
            try:
                trigger_config = json.loads(flow_row["trigger_config"] or "{}")
                minutes = int(trigger_config.get(config_key) or default_minutes)
            except (TypeError, ValueError):
                minutes = default_minutes
            minutes = max(1, minutes)

            cutoff = (now - timedelta(minutes=minutes)).isoformat()
            # Safety bound mirroring check_appointment_reminders': never
            # resurrect a "no reply" nudge for a conversation dead for
            # over a week (e.g. the worker was down for days).
            floor = (now - timedelta(days=7)).isoformat()

            try:
                claimed: list[dict] = []
                with db.connect() as conn:
                    candidates = conn.execute(
                        f"""
                        SELECT id, channel, external_user_id, department,
                               {activity_column} AS activity_marker
                        FROM conversations
                        WHERE company_id = ?
                          AND status NOT IN ('closed', 'archived')
                          AND {extra_where}
                          AND {activity_column} IS NOT NULL
                          AND {activity_column} <= ?
                          AND {activity_column} >= ?
                          AND (
                              {marker_column} IS NULL
                              OR {marker_column} != {activity_column}
                          )
                        LIMIT 200
                        """,
                        (company_id, cutoff, floor),
                    ).fetchall()
                    for row in candidates:
                        cursor = conn.execute(
                            f"""
                            UPDATE conversations SET {marker_column} = ?
                            WHERE id = ? AND (
                                {marker_column} IS NULL
                                OR {marker_column} != ?
                            )
                            """,
                            (
                                row["activity_marker"],
                                row["id"],
                                row["activity_marker"],
                            ),
                        )
                        if cursor.rowcount:
                            claimed.append(dict(row))
                    conn.commit()
            except Exception:
                logger.exception(
                    "%s claim failed for company %s", trigger_type, company_id
                )
                continue

            for row in claimed:
                self.fire_event(
                    company_id=company_id,
                    trigger_type=trigger_type,
                    channel=row["channel"],
                    external_user_id=row["external_user_id"],
                    department=row["department"],
                )

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

    def _run_ai_step(self, request: Any, node: dict[str, Any], department: str | None, variables: dict[str, Any]) -> tuple[str, bool]:
        """Returns (reply_text, should_exit). should_exit defaults to True
        (advance after one exchange) when the node has no exit_when
        configured — the original, backward-compatible behavior. When
        exit_when IS configured, a real classification call decides
        whether to advance or keep the customer on this same AI step for
        another exchange; any failure fails OPEN (advances) so a flaky
        classification call can never strand a conversation forever."""
        config = node["data"].get("config") or {}
        context_tags = [request.channel]
        if department:
            context_tags.append(department)

        instructions = instruction_service.list_texts_for_ai(request.company_id, context_tags=context_tags)
        if config.get("instructions"):
            instructions = [*instructions, config["instructions"]]

        knowledge = []
        if node["data"].get("nodeType") != "ai_direct":
            knowledge = knowledge_manager.list_for_ai(request.company_id, department=department, context_tags=context_tags)

        ai_result = ai_router.route(
            message=request.message,
            channel=request.channel,
            user_id=request.user_id,
            company_id=request.company_id,
            knowledge=knowledge,
            instructions=instructions,
        )
        reply = ai_result.get("reply") if ai_result else ""
        reply = reply or ""

        exit_when = (config.get("exit_when") or "").strip()
        should_exit = True
        if exit_when:
            should_exit = self._check_exit_condition(request, exit_when=exit_when, reply=reply, variables=variables)
        return reply, should_exit

    def _check_exit_condition(self, request: Any, *, exit_when: str, reply: str, variables: dict[str, Any]) -> bool:
        if not config.OPENAI_API_KEY:
            return True
        try:
            payload = {
                "model": config.OPENAI_MODEL,
                "input": [
                    {
                        "role": "system",
                        "content": (
                            "You judge whether a conversation step is finished. Given the exit condition below, "
                            "the customer's latest message, the AI's reply, and any information already collected, "
                            'reply with exactly one word: "YES" if the exit condition is satisfied and the '
                            'conversation should move to the next step, or "NO" if it should continue this step.\n\n'
                            f"Exit condition: {exit_when}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"customer_message": request.message, "ai_reply": reply, "collected_info": variables},
                            ensure_ascii=False,
                        ),
                    },
                ],
            }
            headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"}
            with httpx.Client(timeout=20) as client:
                response = client.post(config.OPENAI_API_URL, headers=headers, json=payload)
            if response.status_code >= 400:
                return True
            data = response.json()
            output_text = data.get("output_text") or ""
            if not output_text:
                for output_item in data.get("output", []):
                    for content in output_item.get("content", []):
                        if content.get("type") in ("output_text", "text"):
                            output_text = content.get("text") or ""
            return output_text.strip().upper().startswith("Y")
        except Exception:
            logger.exception("Reply flow exit_when check failed; defaulting to advance")
            return True

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
        if len(edges) == 1:
            return edges[0]["target"]

        # The builder's condition node now has two distinctly-labeled
        # handles ("Yes"/"No" — FlowStepNode.jsx), which tags each edge with
        # sourceHandle="true"/"false" so a flow author can draw the branches
        # in either order without silently swapping them. Older flows saved
        # before that existed have no sourceHandle on either edge — for
        # those, fall back to the original convention (first-drawn edge =
        # true branch, second = false) so nothing already built breaks.
        wanted_handle = "true" if matched else "false"
        for edge in edges:
            if edge.get("sourceHandle") == wanted_handle:
                return edge["target"]
        if any(edge.get("sourceHandle") in ("true", "false") for edge in edges):
            # At least one edge is labeled but not the one we want (e.g. only
            # the true branch was ever connected) — no edge for this outcome.
            return None
        return edges[0]["target"] if matched else edges[1]["target"]

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

    def _match_button_option(self, message: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Matches a customer's reply against an ask_question's button
        options — by 1-based position ("2"), or by the option's label/value
        text (case-insensitive). This covers every way a tap can come back as
        plain text: a Telegram reply-keyboard tap echoes the button's label
        verbatim; a Messenger/Instagram quick-reply tap's payload is set to
        that same (truncated) label text in channels/meta/sender.py's
        send_meta_buttons; and the WhatsApp numbered-list fallback explicitly
        asks the customer to reply with the number or the option name."""
        text = (message or "").strip()
        if not text:
            return None
        lowered = text.lower()
        for index, option in enumerate(options, start=1):
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or "").strip()
            value = str(option.get("value") or "").strip()
            if lowered == str(index):
                return option
            if label and lowered == label.lower():
                return option
            if value and lowered == value.lower():
                return option
        return None

    def _render_question_buttons(self, config: dict[str, Any]) -> list[str] | None:
        mode = config.get("mode") or "text"
        if mode not in ("buttons", "both"):
            return None
        options = config.get("options") or []
        labels = [str(option.get("label") or "").strip() for option in options if isinstance(option, dict) and option.get("label")]
        return labels or None

    def _close_chat_text(self, config: dict[str, Any]) -> str:
        text = "Thanks for chatting with us — is there anything else we can help with?"
        if config.get("ask_reschedule"):
            text += " Would you like to schedule a follow-up?"
        return text

    # -- entry point ------------------------------------------------------

    def maybe_handle(self, request: Any) -> Response | None:
        lock = _session_lock(request.company_id, request.channel, request.user_id)
        with lock:
            return self._maybe_handle_locked(request)

    def _maybe_handle_locked(self, request: Any) -> Response | None:
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
                node_config = current_node["data"].get("config") or {}
                save_as = node_config.get("save_as")
                mode = node_config.get("mode") or "text"
                options = node_config.get("options") or []
                matched = (
                    self._match_button_option(request.message, options)
                    if mode in ("buttons", "both") and options
                    else None
                )
                if mode == "buttons" and options and matched is None:
                    # Strict buttons mode: free text is never a valid answer —
                    # re-prompt with the same buttons instead of advancing.
                    # current_node_id is left unchanged so the loop below
                    # re-processes this same ask_question node.
                    variables["__invalid_choice__"] = True
                else:
                    if matched is not None:
                        if save_as:
                            variables[save_as] = matched.get("value") or matched.get("label")
                    elif save_as:
                        variables[save_as] = request.message
                    current_node_id = self._next_node_id(flow, current_node_id)
            elif node_type in AI_NODE_TYPES and variables.pop("__stay_at__", None) == current_node_id:
                # exit_when said "not yet" last time — re-run this same AI
                # step with the customer's new message instead of advancing.
                pass
            else:
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
                if variables.pop("__invalid_choice__", False):
                    reply_parts.append("Please choose one of the options below.")
                question = self._format_text(config.get("question", ""), request, variables)
                if question:
                    reply_parts.append(question)
                buttons = self._render_question_buttons(config)
                self._save_progress(session_row, current_node_id=current_node_id, variables=variables)
                return Response("\n\n".join(reply_parts), buttons=buttons) if reply_parts else None

            if node_type in AI_NODE_TYPES:
                ai_text, should_exit = self._run_ai_step(request, node, department, variables)
                if ai_text:
                    reply_parts.append(ai_text)
                if not should_exit:
                    variables["__stay_at__"] = current_node_id
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
