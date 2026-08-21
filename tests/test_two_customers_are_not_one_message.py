"""Provider message ids are unique per conversation, not per company.

Telegram numbers messages per chat, so two different customers both open at
message_id 1, 2, 3. The duplicate guard keyed on `provider_message_id` alone --
backed by a company-wide UNIQUE index -- discarded the second customer's message
as a duplicate of the first, losing it silently. Dedup and the index are now
scoped to the conversation, so a genuine provider retry (same id, same chat) is
still caught while two customers who happen to share an id are both kept.
"""

from __future__ import annotations

import sys

import pytest

# Imported at module scope on purpose: a module first imported *during* an
# active monkeypatch keeps that test's manager for the process lifetime, and
# the fixture below would then rebind a stale singleton. Loading it here means
# the rebind in `wired` both patches and restores it cleanly for every test.
import backend.services.message_service  # noqa: F401


@pytest.fixture()
def wired(platform, monkeypatch):
    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    assert (
        getattr(sys.modules["backend.services.message_service"], "database_manager")
        is test_manager
    )

    from backend.services.message_service import message_service

    return message_service, test_manager


def _save(ms, company_id, chat, mid, text):
    return ms.save_message(
        company_id=company_id, channel="telegram", external_user_id=chat,
        direction="in", text=text, sender_type="customer", provider_message_id=mid,
    )


def test_two_telegram_customers_sharing_a_message_id_are_both_kept(wired, alpha):
    ms, _ = wired
    a = _save(ms, alpha["id"], "chatA", "1", "Hello from A")
    b = _save(ms, alpha["id"], "chatB", "1", "Hello from B")

    assert not a.get("duplicate"), "the first customer's message was not stored"
    assert not b.get("duplicate"), "the second customer's message was lost as a duplicate"


def test_a_genuine_retry_in_the_same_chat_is_still_deduplicated(wired, alpha):
    ms, _ = wired
    first = _save(ms, alpha["id"], "chatA", "7", "Only once")
    retry = _save(ms, alpha["id"], "chatA", "7", "Only once")

    assert not first.get("duplicate")
    assert retry.get("duplicate"), "a real provider retry was stored twice"


def test_is_duplicate_is_scoped_to_the_conversation(wired, alpha):
    ms, _ = wired
    _save(ms, alpha["id"], "chatA", "9", "A")

    assert ms.is_duplicate(
        alpha["id"], "9", channel="telegram", external_user_id="chatA"
    )
    assert not ms.is_duplicate(
        alpha["id"], "9", channel="telegram", external_user_id="chatB"
    )


def test_the_index_migrates_on_an_old_database(wired, alpha):
    """An existing tenant carried the old single-column unique index; the
    upgrade must replace it so the cross-customer insert stops failing."""
    ms, manager = wired
    company_id = alpha["id"]

    with manager.tenant(company_id) as conn:
        conn.execute("DROP INDEX IF EXISTS idx_messages_provider")
        conn.execute(
            "CREATE UNIQUE INDEX idx_messages_provider ON messages(provider_message_id)"
            " WHERE provider_message_id IS NOT NULL"
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    with manager.control() as conn:
        conn.execute(
            "UPDATE company_databases SET schema_version = 2 WHERE company_id = ?",
            (company_id,),
        )
        conn.commit()

    manager.upgrade_outdated_tenants()

    a = _save(ms, company_id, "chatA", "1", "A")
    b = _save(ms, company_id, "chatB", "1", "B")
    assert not a.get("duplicate") and not b.get("duplicate")
