"""Run a company's scripted Reply Flow for one customer message.

A flow is a graph of steps an owner drew: greet, ask a question and save the
answer, branch on it, send a fixed reply, hand off to a human, or let the AI
answer. This walks that graph one customer turn at a time, keeping its place
(which step, and the answers gathered) in the conversation's session -- exactly
where ``current_department`` and the scripted-flow state already live.

Safety first: this does nothing unless the company has a flow whose status is
``active`` and whose scope matches this message. With no active flow the entry
point returns ``None`` and the caller's default behaviour is untouched -- a
company that never builds a flow is never affected by this file.

The boundary of what runs here, stated plainly:
  * Scripted, deterministic steps run in full: greeting, company_intro,
    ask_question (with saved answers and optional buttons), canned_reply,
    condition (branch on a saved answer), close_chat, end.
  * ``create_task`` creates a real internal task.
  * ``human_handoff`` flags the conversation for a person, the same signal the
    AI's own escalation uses.
  * ``ai_direct`` / ``ai_knowledge_only`` / ``ai_knowledge_plus`` -- and the
    action steps that need a subsystem to converse (appointment, product
    suggestion) -- hand this turn to the normal AI reply path, carrying the
    step's own instructions. The AI already honours the company's knowledge and
    reply policy, so the flow positions the conversation and the AI answers.
  * ``timeout_followup`` needs a scheduler to fire later; today it passes
    through (the wait is not yet enforced). This is the one documented gap.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services import module_access
from backend.services.reply_flow_service import reply_flow_service
from core.response import Response


logger = logging.getLogger(__name__)

# A hard ceiling on how many steps one message may advance through, so a flow an
# owner accidentally drew as a loop cannot spin forever inside one turn.
MAX_STEPS_PER_TURN = 50

# Steps that end the flow when reached.
_TERMINAL = {"end", "close_chat", "human_handoff"}

# Steps that hand this turn to the normal AI reply path.
_AI_STEPS = {
    "ai_direct",
    "ai_knowledge_only",
    "ai_knowledge_plus",
    "appointment",
    "product_suggest",
}


def _index(nodes: list[dict]) -> dict[str, dict]:
    return {str(n.get("id")): n for n in nodes if n.get("id") is not None}


def _outgoing(edges: list[dict], node_id: str) -> list[dict]:
    return [e for e in edges if str(e.get("source")) == node_id]


def _start_node(nodes: list[dict], edges: list[dict]) -> dict | None:
    """The step with nothing pointing at it -- where the flow begins.

    Falls back to the first node when every node has an incoming edge (a flow
    drawn as a ring), so a malformed graph still starts somewhere rather than
    refusing to run.
    """
    if not nodes:
        return None
    targets = {str(e.get("target")) for e in edges}
    for node in nodes:
        if str(node.get("id")) not in targets:
            return node
    return nodes[0]


def _node_type(node: dict) -> str:
    return str((node.get("data") or {}).get("nodeType") or "")


def _config(node: dict) -> dict:
    config = (node.get("data") or {}).get("config")
    return config if isinstance(config, dict) else {}


def _fill(text: str, variables: dict[str, Any], names: dict[str, str]) -> str:
    """Substitute the handful of placeholders the builder documents.

    Unknown placeholders are left untouched rather than blanked -- an owner who
    typed ``{{order_id}}`` sees their own text, not an empty gap, and can tell
    the variable never got set.
    """
    out = str(text or "")
    replacements = {
        "customer_name": names.get("customer_name") or variables.get("customer_name") or "",
        "company_name": names.get("company_name") or "",
    }
    for key, value in {**variables, **replacements}.items():
        out = out.replace("{{" + str(key) + "}}", str(value))
    return out


def _condition_true(config: dict, variables: dict[str, Any]) -> bool:
    variable = str(config.get("variable") or "").strip()
    operator = str(config.get("operator") or "is_set").strip()
    target = config.get("value")
    actual = variables.get(variable)

    if operator == "is_set":
        return actual not in (None, "")
    if actual is None:
        return False

    actual_s = str(actual).strip().lower()
    target_s = str(target or "").strip().lower()

    if operator == "equals":
        return actual_s == target_s
    if operator == "contains":
        return target_s in actual_s
    if operator in ("greater_than", "less_than"):
        try:
            a = float(actual)
            b = float(target)
        except (TypeError, ValueError):
            return False
        return a > b if operator == "greater_than" else a < b
    return False


def _answer_buttons(config: dict) -> list[str]:
    """The labels a customer can tap for an ask_question, when it uses buttons."""
    mode = str(config.get("mode") or "text").strip()
    if mode not in ("buttons", "both"):
        return []
    labels: list[str] = []
    for option in config.get("options") or []:
        if isinstance(option, dict):
            label = str(option.get("label") or "").strip()
            if label:
                labels.append(label)
    return labels


class FlowDefer:
    """Marker: this turn should be answered by the normal AI path.

    Carries the step's own instructions so the AI answers with the flow's
    intent, not a blank prompt.
    """

    def __init__(self, instructions: str = ""):
        self.instructions = instructions


class ReplyFlowEngine:
    def handle(
        self,
        *,
        company_id: int,
        channel: str | None,
        department: str | None,
        message: str,
        user_session: dict,
        language: str,
        company_name: str = "",
        customer_name: str = "",
        request: Any = None,
    ) -> Response | FlowDefer | None:
        """Advance the flow for this message.

        Returns a ``Response`` to send scripted text, a ``FlowDefer`` to let the
        AI answer this turn, or ``None`` to leave the default path in charge
        (no active flow, or the module is off).
        """
        if not company_id:
            return None
        # The whole feature lives under the assistant-teaching module: an owner
        # who has that module off has not bought flow automation, and a flow
        # must never run for them.
        try:
            if not module_access.module_enabled(int(company_id), "ai_teaching"):
                return None
        except Exception:  # noqa: BLE001
            return None

        run = user_session.get("reply_flow")
        if not isinstance(run, dict):
            run = None

        if run is None:
            flow = reply_flow_service.active_flow_for(
                int(company_id),
                channel=channel,
                department=department,
                trigger_type="new_conversation",
            )
            if not flow:
                return None
            # Only start a fresh conversation's flow once. The marker outlives
            # the run itself, so a flow that has already ended does not restart
            # on the customer's next sentence.
            if user_session.get("reply_flow_started_id") == flow["id"]:
                return None
            run = {"flow_id": flow["id"], "node_id": None, "variables": {}}
            user_session["reply_flow"] = run
            user_session["reply_flow_started_id"] = flow["id"]
            return self._advance(
                int(company_id), flow, run, message, user_session, language,
                company_name, customer_name, first_turn=True,
            )

        flow = reply_flow_service.get(int(company_id), int(run["flow_id"]))
        if not flow or flow.get("status") != "active":
            # The owner archived or deleted the flow mid-conversation; stop
            # running it and hand back cleanly.
            user_session.pop("reply_flow", None)
            return None

        return self._advance(
            int(company_id), flow, run, message, user_session, language,
            company_name, customer_name, first_turn=False,
        )

    def _advance(
        self, company_id, flow, run, message, user_session, language,
        company_name, customer_name, *, first_turn: bool,
    ) -> Response | FlowDefer | None:
        nodes = flow.get("nodes") or []
        edges = flow.get("edges") or []
        index = _index(nodes)
        names = {"company_name": company_name, "customer_name": customer_name}
        variables = run.setdefault("variables", {})

        # Where are we? On the first turn, at the start node. Otherwise, resume
        # from the step we stopped on.
        if run.get("node_id") is None:
            current = _start_node(nodes, edges)
        else:
            current = index.get(str(run["node_id"]))
            if current is None:
                self._end(run, user_session)
                return None

            resume_type = _node_type(current)
            if resume_type == "ask_question":
                # The customer's message is the answer we were waiting for.
                save_as = str(_config(current).get("save_as") or "").strip()
                if save_as:
                    variables[save_as] = message
                current = self._next(current, edges, index, variables)
            elif resume_type in _AI_STEPS:
                # The AI already answered for this step last turn; move on.
                current = self._next(current, edges, index, variables)

        messages: list[str] = []
        buttons: list[str] = []
        steps = 0

        while current is not None and steps < MAX_STEPS_PER_TURN:
            steps += 1
            node_type = _node_type(current)
            config = _config(current)

            if node_type in ("greeting", "company_intro", "canned_reply"):
                text = _fill(config.get("text", ""), variables, names).strip()
                if text:
                    messages.append(text)
                current = self._next(current, edges, index, variables)
                continue

            if node_type == "ask_question":
                question = _fill(config.get("question", ""), variables, names).strip()
                if question:
                    messages.append(question)
                buttons = _answer_buttons(config)
                run["node_id"] = str(current.get("id"))
                return self._reply(messages, buttons)

            if node_type == "create_task":
                self._create_task(company_id, flow, config, variables, message, language)
                current = self._next(current, edges, index, variables)
                continue

            if node_type == "condition":
                current = self._branch(current, edges, index, variables)
                continue

            if node_type == "human_handoff":
                note = _fill(config.get("note", ""), variables, names).strip()
                self._flag_human(user_session)
                if note:
                    messages.append(note)
                elif not messages:
                    messages.append(self._handoff_line(language))
                self._end(run, user_session)
                return self._reply(messages, self._support_buttons(language))

            if node_type == "close_chat":
                self._end(run, user_session)
                if messages:
                    return self._reply(messages, [])
                return None

            if node_type == "end":
                self._end(run, user_session)
                if messages:
                    return self._reply(messages, [])
                return None

            if node_type == "timeout_followup":
                # No scheduler yet: the wait is not enforced, so this step is a
                # pass-through. Documented in the module docstring.
                current = self._next(current, edges, index, variables)
                continue

            if node_type in _AI_STEPS:
                run["node_id"] = str(current.get("id"))
                instructions = str(
                    config.get("instructions") or config.get("note") or ""
                ).strip()
                if messages:
                    # Flush what the flow scripted before handing to the AI; the
                    # AI step runs on the next message.
                    return self._reply(messages, buttons)
                return FlowDefer(instructions=instructions)

            # An unknown step type never blocks the flow: skip to the next.
            current = self._next(current, edges, index, variables)

        # Ran off the end of the graph (or hit the step ceiling): finish.
        self._end(run, user_session)
        if messages:
            return self._reply(messages, buttons)
        return None

    # --------------------------------------------------------------- helpers

    def _next(self, node, edges, index, variables) -> dict | None:
        outgoing = _outgoing(edges, str(node.get("id")))
        if not outgoing:
            return None
        return index.get(str(outgoing[0].get("target")))

    def _branch(self, node, edges, index, variables) -> dict | None:
        """Pick a condition's next step.

        The builder draws plain source->target edges with no labels, so the
        convention is positional: the first outgoing edge is the "true" path and
        the second is the "false" path. With a single edge, it is followed only
        when the condition holds; otherwise the flow ends.
        """
        outgoing = _outgoing(edges, str(node.get("id")))
        if not outgoing:
            return None
        is_true = _condition_true(_config(node), variables)
        if is_true:
            return index.get(str(outgoing[0].get("target")))
        if len(outgoing) >= 2:
            return index.get(str(outgoing[1].get("target")))
        return None

    def _reply(self, messages: list[str], buttons: list[str]) -> Response:
        return Response("\n\n".join(m for m in messages if m), list(buttons or []))

    def _end(self, run: dict, user_session: dict) -> None:
        user_session.pop("reply_flow", None)

    def _flag_human(self, user_session: dict) -> None:
        # The same session signal the AI path sets when it decides a human is
        # needed, so the inbox surfaces the conversation the usual way.
        user_session["last_ai_needs_human"] = True

    def _support_buttons(self, language: str) -> list[str]:
        return [self._support_label(language)]

    def _support_label(self, language: str) -> str:
        return "التواصل مع الدعم" if language == "ar" else "Contact support"

    def _handoff_line(self, language: str) -> str:
        if language == "ar":
            return "حوّلناك لأحد أعضاء الفريق، رح يتواصل معك قريباً."
        return "I've passed you to a member of our team — they'll be with you shortly."

    def _create_task(self, company_id, flow, config, variables, message, language) -> None:
        try:
            from backend.services.ticket_service import ticket_service

            note = str(config.get("note") or "").strip()
            task_type = str(config.get("task_type") or "other").strip()
            title = note.split("\n", 1)[0][:120] if note else f"{flow['name']} — task"
            ticket_service.create_task(
                company_id=int(company_id),
                data={
                    "title": title,
                    "task_type": task_type,
                    "problem": note or message,
                    "language": language,
                    "platform": "reply_flow",
                },
            )
        except Exception:  # noqa: BLE001
            # A flow must keep talking to the customer even if the task write
            # fails; the failure is logged, not surfaced mid-conversation.
            logger.exception("Reply flow could not create a task")


reply_flow_engine = ReplyFlowEngine()
