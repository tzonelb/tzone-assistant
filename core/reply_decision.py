"""Turning a company's reply-policy switches into one decision about one message.

Nine switches are offered to a company on the AI TEACHING screen, each with a
sentence explaining what it does to a customer. Four of them worked. The other
five — ``reply_mode``, ``grounded_ai_enabled``, ``allow_ai_free_reply``,
``minimum_match_confidence`` and ``fallback_to_human`` — were resolved, merged,
serialised into the model's payload, and never consulted by anything that
decided anything. ``grounded_ai_enabled`` did not appear anywhere in the
codebase outside the field list that renders its toggle.

So a company could set "Off keeps it to what you taught it", watch the switch
save, and get an assistant that went on answering whatever it liked. A control
that shows a decision and does not make it is worse than no control: the owner
believes the guardrail is on.

This module is the decision, kept apart from `core/engine.py` on purpose. It
touches no database, no session, no model and no request — it takes the
resolved policy and the matcher's verdict and returns what is allowed. That
makes every rule below assertable directly, which is the only way nine
interacting switches stay correct as more are added.

The rules are the sentences already shown to the owner, not new ones:

``reply_mode``
    ``flow_only`` never calls the model — buttons and scripted steps only.
    ``grounded_ai`` answers only from matched knowledge. ``knowledge_then_ai``
    answers from knowledge and may fall through to a free reply when the
    company allows one.

``grounded_ai_enabled``
    Off means the assistant never composes an answer from knowledge items,
    whatever the match said.

``allow_ai_free_reply``
    Off means nothing in the knowledge matched leaves the assistant with
    nothing to say, rather than free rein.

``minimum_match_confidence``
    A match below the threshold is not a match. Without this the matcher's
    own confidence number was recorded and ignored.

``fallback_to_human``
    Decides only what happens when the rules above leave no answer: hand to a
    human, or say plainly that the information is not confirmed. It never
    creates an answer that the other switches refused.

One combination has no reading that satisfies both switches:
``allow_ai_free_reply`` off with ``fallback_to_human`` off, on a message that
matched nothing. "Keep it to what you taught it" and "answer anyway instead of
escalating" cannot both hold. The content switch wins — the assistant does not
invent an answer — and ``fallback_to_human`` keeps its own meaning by not
routing the conversation to a human. The customer gets the plain "not
confirmed" sentence and the conversation stays where it is. Inverting that
precedence would let a switch about *who* answers silently turn off a switch
about *what may be said*, which is the guardrail, not the routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REPLY_MODES = ("grounded_ai", "knowledge_then_ai", "flow_only")

# Used when a policy omits a key or carries an unreadable value. They match
# `config/response_policy.json`, which is the platform's shipped starting point
# and what an owner sees as "inherited" before overriding anything.
DEFAULTS: dict[str, Any] = {
    "reply_mode": "grounded_ai",
    "grounded_ai_enabled": True,
    "allow_ai_free_reply": False,
    "minimum_match_confidence": 0.62,
    "fallback_to_human": True,
}


@dataclass(frozen=True)
class ReplyDecision:
    """What this company allows for this one message."""

    use_knowledge: bool
    """Whether the matched knowledge may be handed to the model."""

    may_call_model: bool
    """Whether the model may be called at all. False means the safe reply."""

    allow_free_reply: bool
    """Whether the model may answer beyond the matched knowledge."""

    escalate: bool
    """Whether a message left with no answer is routed to a human."""

    matched: bool
    """Whether the match cleared the company's confidence threshold."""

    reason: str
    """Which switch decided this, for the diagnostics record."""

    @property
    def blocked(self) -> bool:
        return not self.may_call_model


def _flag(policy: dict[str, Any], key: str) -> bool:
    value = policy.get(key, DEFAULTS[key])

    return DEFAULTS[key] if value is None else bool(value)


def fallback_to_human(policy: dict[str, Any] | None) -> bool:
    """Whether this company hands a message it cannot answer to a human.

    Public because the engine needs it on one path this module does not see:
    the model was called, was allowed to answer, and returned nothing. That is
    a failure rather than a policy refusal, but who picks it up is still the
    company's decision.
    """
    return _flag(policy or {}, "fallback_to_human")


def _mode(policy: dict[str, Any]) -> str:
    value = str(policy.get("reply_mode") or DEFAULTS["reply_mode"]).strip().lower()

    # An unrecognised mode falls back rather than raising. A typo stored in a
    # company's database must not take that company's assistant offline, and
    # the write path validates against the same tuple, so this is reachable
    # only through a hand-edited row.
    return value if value in REPLY_MODES else DEFAULTS["reply_mode"]


def _threshold(policy: dict[str, Any]) -> float:
    """The company's confidence threshold, or the shipped one.

    Out of range is treated as unreadable rather than clamped, which is the
    opposite of the obvious implementation and the reason this has its own
    function. The field declares a minimum of 0 and a maximum of 1, so a stored
    ``9`` is not a choice an owner could have made through the screen — it is a
    corrupt row. Clamping it to ``1.0`` means nothing ever clears the bar and
    the assistant goes silent; clamping a stored ``-3`` to ``0.0`` means
    everything clears it and the guardrail is off. Both turn bad data into the
    most extreme setting available, silently.
    """
    raw = policy.get("minimum_match_confidence", DEFAULTS["minimum_match_confidence"])
    shipped = float(DEFAULTS["minimum_match_confidence"])

    try:
        value = float(raw)
    except (TypeError, ValueError):
        return shipped

    # Also catches NaN, which compares false against both bounds and would
    # otherwise make every comparison below it false — silence again.
    if not 0.0 <= value <= 1.0:
        return shipped

    return value


def decide(
    policy: dict[str, Any] | None,
    match_result: dict[str, Any] | None,
    *,
    has_knowledge: bool = True,
) -> ReplyDecision:
    """Resolve one message against one company's switches.

    ``has_knowledge`` is whether this company has any knowledge at all — a
    company with an empty base and a mode of ``grounded_ai`` has nothing to
    answer from, and saying so in the reason makes an otherwise silent
    assistant explainable from the diagnostics record.
    """
    policy = policy or {}
    match_result = match_result or {}

    mode = _mode(policy)
    grounded = _flag(policy, "grounded_ai_enabled")
    free = _flag(policy, "allow_ai_free_reply")
    escalate = _flag(policy, "fallback_to_human")
    threshold = _threshold(policy)

    try:
        confidence = float(match_result.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    selected = match_result.get("selected_ids") or []
    matched = bool(selected) and confidence >= threshold

    def built(
        *,
        use_knowledge: bool,
        may_call_model: bool,
        allow_free_reply: bool,
        reason: str,
    ) -> ReplyDecision:
        return ReplyDecision(
            use_knowledge=use_knowledge,
            may_call_model=may_call_model,
            allow_free_reply=allow_free_reply,
            # Escalation is only ever a question when nothing is going to be
            # answered. A reply that goes out does not need a human.
            escalate=escalate and not may_call_model,
            matched=matched,
            reason=reason,
        )

    if mode == "flow_only":
        return built(
            use_knowledge=False,
            may_call_model=False,
            allow_free_reply=False,
            reason="reply_mode=flow_only",
        )

    if not grounded:
        # The assistant may not compose from knowledge. Under `grounded_ai`
        # that is the only source it had, so there is nothing left; under
        # `knowledge_then_ai` a free reply is still possible if allowed.
        if mode == "grounded_ai" or not free:
            return built(
                use_knowledge=False,
                may_call_model=False,
                allow_free_reply=False,
                reason="grounded_ai_enabled=false",
            )

        return built(
            use_knowledge=False,
            may_call_model=True,
            allow_free_reply=True,
            reason="grounded_ai_enabled=false,allow_ai_free_reply=true",
        )

    if matched:
        return built(
            use_knowledge=True,
            may_call_model=True,
            # Even with a match, a free reply is only permitted where the
            # company permits one. Under `grounded_ai` it never is: the mode
            # is the promise that answers come from the company's own words.
            allow_free_reply=free and mode != "grounded_ai",
            reason="knowledge_match",
        )

    if mode == "grounded_ai":
        return built(
            use_knowledge=False,
            may_call_model=False,
            allow_free_reply=False,
            reason=(
                "reply_mode=grounded_ai,no_knowledge"
                if not has_knowledge
                else f"reply_mode=grounded_ai,below_confidence({threshold:g})"
            ),
        )

    if free:
        return built(
            use_knowledge=False,
            may_call_model=True,
            allow_free_reply=True,
            reason="allow_ai_free_reply=true",
        )

    return built(
        use_knowledge=False,
        may_call_model=False,
        allow_free_reply=False,
        reason="allow_ai_free_reply=false,no_match",
    )
