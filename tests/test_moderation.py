"""Moderation: a spam mark that actually silences the assistant, and a block
that actually stops a customer's message from reaching the platform.

The failure this guards is the hollow version of both: a "Mark as spam" that
only changes a badge while the assistant keeps replying, and a "Block customer"
that only hides a button while the message is still stored, notified on, and
answered. Both are asserted against the real inbound and reply-gating paths, the
same way test_a_suspended_company_stops_acting.py asserts suspension.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


def _wire(platform, monkeypatch):
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)
    return test_manager


def _inbound(company_id, user_id="cust-mod", text="hello"):
    from channels.inbound import process_inbound_event

    return process_inbound_event(
        company_id=company_id,
        event={
            "channel": "messenger",
            "user_id": user_id,
            "text": text,
            "message_id": f"mid-{user_id}-{text}",
        },
    )


# --------------------------------------------------------------- blocking


def test_a_blocked_customers_message_is_dropped(platform, alpha, monkeypatch):
    _wire(platform, monkeypatch)
    import channels.inbound as inbound
    from backend.services.customer_service import customer_service

    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    # First message creates the customer record.
    result = _inbound(alpha["id"], text="hi there")
    assert result["status"] != "ignored"

    customers = customer_service.list_customers(company_id=alpha["id"], limit=10)
    customer_id = customers["items"][0]["id"]

    customer_service.set_blocked(
        company_id=alpha["id"], customer_id=customer_id, blocked=True, actor_user_id=1
    )

    result = _inbound(alpha["id"], text="are you there")
    assert result["status"] == "ignored"
    assert result["reason"] == "customer_blocked"


def test_a_blocked_customers_message_is_not_stored(platform, alpha, monkeypatch):
    _wire(platform, monkeypatch)
    import channels.inbound as inbound
    from backend.services.customer_service import customer_service
    from backend.services.message_service import message_service

    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    _inbound(alpha["id"], text="hi")
    customers = customer_service.list_customers(company_id=alpha["id"], limit=10)
    customer_id = customers["items"][0]["id"]

    customer_service.set_blocked(
        company_id=alpha["id"], customer_id=customer_id, blocked=True, actor_user_id=1
    )

    _inbound(alpha["id"], text="a secret complaint")

    stored = message_service.list_messages(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-mod"
    )
    texts = [m.get("text") for m in stored]
    assert "a secret complaint" not in texts


def test_unblocking_lets_messages_through_again(platform, alpha, monkeypatch):
    _wire(platform, monkeypatch)
    import channels.inbound as inbound
    from backend.services.customer_service import customer_service

    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    _inbound(alpha["id"], text="hi")
    customers = customer_service.list_customers(company_id=alpha["id"], limit=10)
    customer_id = customers["items"][0]["id"]

    customer_service.set_blocked(
        company_id=alpha["id"], customer_id=customer_id, blocked=True, actor_user_id=1
    )
    assert _inbound(alpha["id"], text="blocked now")["status"] == "ignored"

    customer_service.set_blocked(
        company_id=alpha["id"], customer_id=customer_id, blocked=False, actor_user_id=1
    )
    result = _inbound(alpha["id"], text="unblocked now")
    assert result["status"] != "ignored"


def test_one_companys_block_does_not_reach_another(platform, alpha, beta, monkeypatch):
    """The isolation that matters most on a multi-tenant platform."""
    _wire(platform, monkeypatch)
    import channels.inbound as inbound
    from backend.services.customer_service import customer_service

    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    _inbound(alpha["id"], text="hi from alpha")
    alpha_customers = customer_service.list_customers(company_id=alpha["id"], limit=10)
    customer_service.set_blocked(
        company_id=alpha["id"],
        customer_id=alpha_customers["items"][0]["id"],
        blocked=True,
        actor_user_id=1,
    )

    # The same external user id, messaging beta -- a different company.
    result = _inbound(beta["id"], text="hi from beta, same user id")
    assert result["status"] != "ignored"


def test_blocking_an_unknown_customer_raises(platform, alpha, monkeypatch):
    _wire(platform, monkeypatch)
    from backend.services.customer_service import customer_service

    with pytest.raises(KeyError):
        customer_service.set_blocked(
            company_id=alpha["id"], customer_id=999999, blocked=True, actor_user_id=1
        )


# --------------------------------------------------------------- spam


@pytest.fixture()
def bound_control(platform, monkeypatch):
    import backend.services.conversation_control_service as ccs_module

    test_manager = _wire(platform, monkeypatch)
    monkeypatch.setattr(ccs_module, "database_manager", test_manager)
    return test_manager


def test_marking_spam_stops_ai_handling(bound_control, alpha):
    from backend.services.conversation_control_service import conversation_control_service

    state = conversation_control_service.get_or_create(
        company_id=alpha["id"], channel="messenger", external_user_id="spam-cust"
    )
    assert conversation_control_service.is_ai_handling(
        company_id=alpha["id"], channel="messenger", external_user_id="spam-cust"
    )

    conversation_control_service.update_workspace_state(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="spam-cust",
        actor_user_id=1,
        is_spam=True,
    )

    assert not conversation_control_service.is_ai_handling(
        company_id=alpha["id"], channel="messenger", external_user_id="spam-cust"
    )
    updated = conversation_control_service.get_state(
        company_id=alpha["id"], channel="messenger", external_user_id="spam-cust"
    )
    assert bool(updated["is_spam"]) is True


def test_unmarking_spam_does_not_resume_ai_on_its_own(bound_control, alpha):
    """A person decided to look at it; the same 'return to AI' action every
    human takeover already uses is how it resumes -- not automatically."""
    from backend.services.conversation_control_service import conversation_control_service

    conversation_control_service.get_or_create(
        company_id=alpha["id"], channel="messenger", external_user_id="spam-cust-2"
    )
    conversation_control_service.update_workspace_state(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="spam-cust-2",
        actor_user_id=1,
        is_spam=True,
    )
    conversation_control_service.update_workspace_state(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="spam-cust-2",
        actor_user_id=1,
        is_spam=False,
    )

    updated = conversation_control_service.get_state(
        company_id=alpha["id"], channel="messenger", external_user_id="spam-cust-2"
    )
    assert bool(updated["is_spam"]) is False
    assert not conversation_control_service.is_ai_handling(
        company_id=alpha["id"], channel="messenger", external_user_id="spam-cust-2"
    )


def test_one_companys_spam_mark_does_not_reach_another(bound_control, alpha, beta):
    from backend.services.conversation_control_service import conversation_control_service

    conversation_control_service.get_or_create(
        company_id=alpha["id"], channel="messenger", external_user_id="shared-id"
    )
    conversation_control_service.get_or_create(
        company_id=beta["id"], channel="messenger", external_user_id="shared-id"
    )
    conversation_control_service.update_workspace_state(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="shared-id",
        actor_user_id=1,
        is_spam=True,
    )

    beta_state = conversation_control_service.get_state(
        company_id=beta["id"], channel="messenger", external_user_id="shared-id"
    )
    assert bool(beta_state["is_spam"]) is False
    assert conversation_control_service.is_ai_handling(
        company_id=beta["id"], channel="messenger", external_user_id="shared-id"
    )
