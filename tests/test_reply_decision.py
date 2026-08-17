"""Tests for the five reply-policy switches that decided nothing.

Nine switches are offered to a company on the AI TEACHING screen, each with a
sentence explaining what it does to a customer. Four worked. `reply_mode`,
`grounded_ai_enabled`, `allow_ai_free_reply`, `minimum_match_confidence` and
`fallback_to_human` were resolved, merged, serialised into the model's payload
and never consulted by anything that decided anything — `grounded_ai_enabled`
did not appear in the codebase at all outside the field list that draws its
toggle.

So an owner could set "Off keeps it to what you taught it", watch it save, and
get an assistant that went on answering whatever it liked. A control that shows
a decision and does not make it is worse than no control, because the owner
believes the guardrail is on.

Each test below is one sentence from the screen, asserted.
"""

from __future__ import annotations

import pytest

from core.reply_decision import DEFAULTS, decide, fallback_to_human


def _match(confidence: float = 0.9, ids: list | None = None) -> dict:
    return {
        "confidence": confidence,
        "selected_ids": ["1"] if ids is None else ids,
        "department": "sales",
    }


def _no_match() -> dict:
    return {"confidence": 0.0, "selected_ids": [], "department": "unknown"}


# ------------------------------------------------------------------ reply_mode


def test_flow_only_never_calls_the_model():
    """"Flow only sticks to the buttons and scripted steps." Even a perfect
    knowledge match does not buy a model call — nor its charge."""
    decision = decide({"reply_mode": "flow_only"}, _match(confidence=1.0))

    assert decision.may_call_model is False
    assert decision.use_knowledge is False
    assert decision.reason == "reply_mode=flow_only"


def test_grounded_ai_answers_from_a_match():
    decision = decide({"reply_mode": "grounded_ai"}, _match())

    assert decision.may_call_model is True
    assert decision.use_knowledge is True


def test_grounded_ai_never_permits_a_free_reply_even_when_the_switch_allows_one():
    """The mode is the promise that answers come from the company's own words.
    A stale `allow_ai_free_reply` must not quietly break it."""
    decision = decide(
        {"reply_mode": "grounded_ai", "allow_ai_free_reply": True},
        _match(),
    )

    assert decision.allow_free_reply is False


def test_grounded_ai_says_nothing_when_nothing_matched():
    decision = decide({"reply_mode": "grounded_ai"}, _no_match())

    assert decision.may_call_model is False
    assert "grounded_ai" in decision.reason


def test_knowledge_then_ai_falls_through_when_the_company_allows_it():
    decision = decide(
        {"reply_mode": "knowledge_then_ai", "allow_ai_free_reply": True},
        _no_match(),
    )

    assert decision.may_call_model is True
    assert decision.allow_free_reply is True
    assert decision.use_knowledge is False


def test_an_unreadable_mode_falls_back_instead_of_taking_the_assistant_offline():
    """A hand-edited row must not silence a company's assistant."""
    decision = decide({"reply_mode": "not_a_mode"}, _match())

    assert decision.may_call_model is True


# --------------------------------------------------------- grounded_ai_enabled


def test_grounded_off_never_composes_from_knowledge():
    """"Off means the assistant never composes an answer from knowledge
    items." This flag appeared nowhere in the codebase before."""
    decision = decide(
        {"reply_mode": "knowledge_then_ai", "grounded_ai_enabled": False},
        _match(confidence=1.0),
    )

    assert decision.use_knowledge is False


def test_grounded_off_with_no_free_reply_leaves_nothing_to_say():
    decision = decide(
        {
            "reply_mode": "knowledge_then_ai",
            "grounded_ai_enabled": False,
            "allow_ai_free_reply": False,
        },
        _match(confidence=1.0),
    )

    assert decision.may_call_model is False
    assert decision.reason == "grounded_ai_enabled=false"


def test_grounded_off_still_allows_a_free_reply_when_the_company_wants_one():
    decision = decide(
        {
            "reply_mode": "knowledge_then_ai",
            "grounded_ai_enabled": False,
            "allow_ai_free_reply": True,
        },
        _match(confidence=1.0),
    )

    assert decision.may_call_model is True
    assert decision.use_knowledge is False
    assert decision.allow_free_reply is True


# --------------------------------------------------- minimum_match_confidence


def test_a_match_below_the_threshold_is_not_a_match():
    """The matcher's confidence was recorded and ignored. A company that
    tightened this got the same answers as one that had not touched it."""
    policy = {"reply_mode": "grounded_ai", "minimum_match_confidence": 0.8}

    assert decide(policy, _match(confidence=0.79)).matched is False
    assert decide(policy, _match(confidence=0.80)).matched is True


def test_the_threshold_decides_whether_the_model_is_called_at_all():
    policy = {"reply_mode": "grounded_ai", "minimum_match_confidence": 0.8}

    assert decide(policy, _match(confidence=0.5)).may_call_model is False
    assert decide(policy, _match(confidence=0.95)).may_call_model is True


def test_selected_ids_are_required_however_confident_the_model_claims_to_be():
    """A confidence of 1.0 with nothing selected is not something to answer
    from."""
    decision = decide(
        {"reply_mode": "grounded_ai"}, _match(confidence=1.0, ids=[])
    )

    assert decision.matched is False


@pytest.mark.parametrize("bad", [None, "high", float("nan"), {}])
def test_an_unreadable_confidence_is_not_treated_as_a_match(bad):
    decision = decide(
        {"reply_mode": "grounded_ai"},
        {"confidence": bad, "selected_ids": ["1"]},
    )

    assert decision.matched is False


@pytest.mark.parametrize("bad", [None, "", "later", -3, 9, float("nan")])
def test_an_unreadable_threshold_falls_back_to_the_shipped_one(bad):
    """Out of range is corrupt data, not an extreme choice.

    The field declares 0 to 1, so a stored 9 is not something an owner could
    have set through the screen. Clamping it to 1.0 means nothing ever clears
    the bar and the assistant goes silent; clamping a stored -3 to 0.0 turns
    the guardrail off entirely. Both hide bad data behind the most extreme
    setting available.
    """
    decision = decide(
        {"reply_mode": "grounded_ai", "minimum_match_confidence": bad},
        _match(confidence=0.99),
    )

    assert decision.matched is True, "a bad threshold silenced the assistant"


@pytest.mark.parametrize("bad", [-3, 9, float("nan")])
def test_an_out_of_range_threshold_does_not_switch_the_guardrail_off(bad):
    """The other half: a bad value must not let everything through either."""
    decision = decide(
        {"reply_mode": "grounded_ai", "minimum_match_confidence": bad},
        _match(confidence=0.05),
    )

    assert decision.matched is False, "a bad threshold disabled the guardrail"


# ------------------------------------------------------------ fallback_to_human


def test_escalation_is_on_when_there_is_no_answer():
    decision = decide(
        {"reply_mode": "grounded_ai", "fallback_to_human": True}, _no_match()
    )

    assert decision.escalate is True


def test_escalation_off_does_not_hand_the_conversation_over():
    """"Off means the assistant answers anyway instead of escalating."" """
    decision = decide(
        {"reply_mode": "grounded_ai", "fallback_to_human": False}, _no_match()
    )

    assert decision.escalate is False


def test_a_reply_that_goes_out_never_escalates():
    """Escalation is only ever a question when nothing is answered."""
    decision = decide(
        {"reply_mode": "grounded_ai", "fallback_to_human": True}, _match()
    )

    assert decision.escalate is False


def test_escalation_off_does_not_buy_a_free_reply_the_content_switch_refused():
    """The one combination with no reading that satisfies both switches.

    "Keep it to what you taught it" and "answer anyway instead of escalating"
    cannot both hold on an unmatched message. The content switch wins: the
    assistant does not invent an answer, and `fallback_to_human` keeps its own
    meaning by not routing the conversation to a human.

    The other precedence would let a switch about *who* answers silently turn
    off a switch about *what may be said* — the guardrail, not the routing.
    """
    decision = decide(
        {
            "reply_mode": "knowledge_then_ai",
            "allow_ai_free_reply": False,
            "fallback_to_human": False,
        },
        _no_match(),
    )

    assert decision.may_call_model is False, "the guardrail was overruled"
    assert decision.escalate is False


def test_fallback_to_human_is_readable_on_its_own():
    assert fallback_to_human({"fallback_to_human": False}) is False
    assert fallback_to_human({}) is DEFAULTS["fallback_to_human"]
    assert fallback_to_human(None) is DEFAULTS["fallback_to_human"]


# ----------------------------------------------------------------- robustness


def test_an_empty_policy_uses_the_shipped_defaults():
    """A company that has overridden nothing gets what the platform ships,
    which is what the screen shows it as inheriting."""
    decision = decide({}, _match())

    assert decision.may_call_model is True
    assert decision.use_knowledge is True
    assert decision.allow_free_reply is False


def test_no_policy_and_no_match_does_not_raise():
    decision = decide(None, None)

    assert decision.may_call_model is False
    assert decision.matched is False


@pytest.mark.parametrize(
    "key", ["grounded_ai_enabled", "allow_ai_free_reply", "fallback_to_human"]
)
def test_a_null_flag_reads_as_the_shipped_default_not_as_false(key):
    """A stored NULL is "not set", not "off". Reading it as off would silently
    tighten a company that never chose to."""
    decision = decide({key: None}, _match())
    shipped = decide({}, _match())

    assert decision == shipped


def test_the_reason_names_the_switch_that_produced_the_silence():
    """An owner who tightened the threshold too far needs to see why the
    assistant stopped answering, not just that it did."""
    decision = decide(
        {"reply_mode": "grounded_ai", "minimum_match_confidence": 0.9},
        _match(confidence=0.3),
    )

    assert "below_confidence" in decision.reason


def test_an_empty_knowledge_base_says_so_rather_than_blaming_confidence():
    decision = decide(
        {"reply_mode": "grounded_ai"}, _no_match(), has_knowledge=False
    )

    assert "no_knowledge" in decision.reason
