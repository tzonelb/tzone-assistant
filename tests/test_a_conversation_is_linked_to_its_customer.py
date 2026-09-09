"""A conversation must point at the customer record it belongs to, starting
from the very first message.

Reproduced live: `channels/inbound.py` calls `customer_service.upsert_from_channel`
first, and *that* already links `conversations.customer_id` for any
conversation row that already exists -- but only for one that already exists.
It runs before `conversation_control_service.record_customer_message` (which
creates the row on a customer's first-ever message), so on that first message
the conversation does not exist yet when the customer-linking `UPDATE` runs,
and it updates nothing. Reproduced against a first-time WhatsApp sender: the
Customers screen showed a lead with no name and no phone (a separate, also
real gap -- see `tests/test_webhook_security.py`'s `contact_names` tests), and
its "conversations" count read 0 even though the conversation the message
created was sitting right there.

`get_or_create`'s own INSERT now names `customer_id`, so the row is born
linked rather than depending on a second write to catch up.

Asserted here against the real inbound path, not the SQL directly: a fix
verified against a mock would prove the mock, not the platform.
"""

from __future__ import annotations

import sys

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


def _inbound(company_id, user_id="cust-link", text="hello"):
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


def test_a_new_conversations_customer_id_is_set_on_the_first_message(
    platform, alpha, monkeypatch
):
    manager = _wire(platform, monkeypatch)
    import channels.inbound as inbound
    from backend.services.customer_service import customer_service

    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    _inbound(alpha["id"], text="hi there")

    customers = customer_service.list_customers(company_id=alpha["id"], limit=10)
    customer_id = customers["items"][0]["id"]

    with manager.tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT customer_id FROM conversations WHERE company_id = ? "
            "AND channel = 'messenger' AND external_user_id = 'cust-link'",
            (alpha["id"],),
        ).fetchone()

    assert row["customer_id"] == customer_id, (
        "a brand-new conversation's customer_id was not set on its first "
        "message -- customer_service.upsert_from_channel's own linking "
        "UPDATE runs before this row exists, so it matches nothing the "
        "first time; the INSERT itself has to carry the id"
    )


def test_a_conversation_never_gets_repointed_to_a_different_customer(
    platform, alpha, monkeypatch
):
    """`get_or_create` fills a NULL customer_id on an existing row (the same
    guarantee it already gives `channel_account_id`); it must never overwrite
    an id that is already set."""
    manager = _wire(platform, monkeypatch)
    import channels.inbound as inbound
    from backend.services.conversation_control_service import (
        conversation_control_service,
    )

    monkeypatch.setattr(
        inbound, "schedule_smart_reply", lambda **kwargs: {"queued": True}
    )

    conversation_control_service.get_or_create(
        company_id=alpha["id"], channel="messenger", external_user_id="cust-link-3",
        customer_id=42,
    )

    conversation_control_service.record_customer_message(
        company_id=alpha["id"],
        channel="messenger",
        external_user_id="cust-link-3",
        customer_id=999,
    )

    with manager.tenant(alpha["id"]) as conn:
        row = conn.execute(
            "SELECT customer_id FROM conversations WHERE company_id = ? "
            "AND channel = 'messenger' AND external_user_id = 'cust-link-3'",
            (alpha["id"],),
        ).fetchone()

    assert row["customer_id"] == 42
