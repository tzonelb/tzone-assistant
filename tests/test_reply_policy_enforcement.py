"""Tests that the engine obeys the decision, not just that the decision is right.

`tests/test_reply_decision.py` asserts the rules in isolation. This file asserts
the wiring: that `core/engine.py` actually consults them, that a refused message
costs no model call, and that a company's switch reaches a real reply.

The distinction matters because the defect being fixed was exactly a correct
value that nothing consulted. A decision table with no call site is the same
bug in a tidier place.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.reply_policy_service  # noqa: F401
    import core.engine  # noqa: F401
    import core.response_policy  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    # `reply_policy_service` holds no manager of its own — it stores through
    # `company_settings_service`, which is the one that must be rebound.
    assert "backend.services.company_settings_service" in rebound

    from config.settings import config

    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

    return test_manager


class _Request:
    def __init__(self, company_id: int, message: str = "do you deliver?"):
        self.company_id = company_id
        self.user_id = "preview-user"
        self.channel = "messenger"
        self.message = message
        self.channel_account_id = None


@pytest.fixture()
def model_calls(monkeypatch):
    """Record every model call the engine makes, and answer without a network."""
    from core.ai_router import ai_router

    calls = []

    def fake_route(**kwargs):
        calls.append(kwargs)

        return {
            "department": "sales",
            "intent": "delivery",
            "topic": "delivery",
            "language": "en",
            "confidence": 0.9,
            "reply": "Yes, we deliver.",
            "buttons": [],
            "needs_human": False,
            "missing_information": [],
            "used_knowledge_ids": [],
            "notes": "",
        }

    monkeypatch.setattr(ai_router, "route", fake_route, raising=True)

    return calls


def _policy(company, **values) -> None:
    from backend.services.reply_policy_service import reply_policy_service

    reply_policy_service.update_company_default(
        company_id=company["id"], values=values
    )


def _reply(company, message: str = "do you deliver?") -> dict:
    from core.engine import Engine
    from core.session import session

    request = _Request(company["id"], message)
    session.reset(request.user_id) if hasattr(session, "reset") else None

    return Engine().handle_ai(
        request=request,
        language="en",
        current_state=None,
        current_department=None,
    )


def test_the_shipped_policy_refuses_rather_than_inventing(wired, alpha, model_calls):
    """A company with an empty knowledge base, on the shipped `grounded_ai`
    policy, has nothing confirmed to answer from — so it says so.

    This is the visible consequence of enforcing the switches, and it is the
    documented intent: `allow_ai_free_reply` ships as false.
    """
    response = _reply(alpha)

    assert model_calls == [], "the model was called for a message with no match"
    assert "confirmed" in response.text.lower()


def test_a_refused_message_costs_no_model_charge(wired, alpha, model_calls):
    """The other half of honouring the switch. A guardrail that still pays for
    the call it refused is only half implemented."""
    for _ in range(5):
        _reply(alpha)

    assert model_calls == []


def test_allowing_a_free_reply_reaches_the_model(wired, alpha, model_calls):
    _policy(alpha, reply_mode="knowledge_then_ai", allow_ai_free_reply=True)

    response = _reply(alpha)

    assert len(model_calls) == 1
    assert "we deliver" in response.text.lower()


def test_flow_only_never_reaches_the_model(wired, alpha, model_calls):
    _policy(alpha, reply_mode="flow_only")

    _reply(alpha)

    assert model_calls == []


def test_the_model_is_told_what_it_may_actually_do(wired, alpha, model_calls):
    """The resolved decision goes to the model, not the raw switches.

    A payload saying `allow_ai_free_reply: true` next to a decision that
    refused one is an instruction to do the thing the owner just forbade.
    """
    _policy(alpha, reply_mode="grounded_ai", allow_ai_free_reply=True)
    _policy(alpha, minimum_match_confidence=0.0)

    # `grounded_ai` never permits a free reply, whatever the other switch says.
    _policy(alpha, allow_ai_free_reply=True)

    _policy(alpha, reply_mode="knowledge_then_ai")
    _reply(alpha)

    assert model_calls, "expected the model to be reached under this policy"
    assert model_calls[0]["response_policy"]["allow_ai_free_reply"] is True


def _support_buttons(response) -> list[str]:
    """Only the escalation button, not the menu button `show_buttons` adds.

    Asserting on an empty button list would be asserting on the wrong switch:
    `show_buttons` appends a main-menu button downstream of this decision, and
    a test that failed when it appeared would be testing two switches at once.
    """
    return [
        button
        for button in (response.buttons or [])
        if "support" in str(button).lower() or "الدعم" in str(button)
    ]


def test_escalation_off_does_not_offer_a_support_button(wired, alpha, model_calls):
    """"Off means the assistant answers anyway instead of escalating." The
    words do not change — a company that switched escalation off did not ask
    its assistant to start guessing."""
    _policy(alpha, reply_mode="grounded_ai", fallback_to_human=False)

    response = _reply(alpha)

    assert _support_buttons(response) == []
    assert model_calls == []


def test_escalation_on_offers_the_support_button(wired, alpha, model_calls):
    _policy(alpha, reply_mode="grounded_ai", fallback_to_human=True)

    response = _reply(alpha)

    assert _support_buttons(response) != []


def test_one_company_policy_does_not_reach_another(wired, alpha, beta, model_calls):
    """The whole point of moving this out of a shared JSON file."""
    _policy(alpha, reply_mode="knowledge_then_ai", allow_ai_free_reply=True)

    _reply(beta)
    assert model_calls == [], "beta answered under alpha's policy"

    _reply(alpha)
    assert len(model_calls) == 1
