"""Tests for which channel filters an inbox offers.

The rule: a company sees the channels it runs.

The inbox used to build its filters from
`SELECT DISTINCT channel FROM conversations` — from message history rather than
from what the company connected. That gives two wrong answers, and both were
reachable in normal use:

* a company that has just connected Instagram sees no Instagram filter until
  the first customer happens to write, so the thing they just paid for looks
  broken;
* a company that once received a single test message on Messenger keeps a
  Messenger filter forever, with no way to remove it.

A company on a three-channel bundle may connect three accounts of the same
type, or one each of three types. The filter list follows the accounts, not the
message log.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def service(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import backend.services.message_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.message_service" in rebound
    assert "backend.services.channel_account_service" in rebound

    from backend.services.message_service import message_service

    return message_service


def _connect(platform, company, channel: str, routing_id: str, name: str) -> None:
    """Register an active account without going through credential sealing."""
    from database.manager import utc_now_iso

    column = {
        "messenger": "page_id",
        "instagram": "instagram_business_id",
        "whatsapp": "phone_number_id",
    }[channel]

    now = utc_now_iso()

    with platform["manager"].control() as conn:
        conn.execute(
            f"""
            INSERT INTO channel_accounts (
                company_id, channel, name, {column}, external_account_id,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (company["id"], channel, name, routing_id, routing_id, now, now),
        )
        conn.commit()


def _conversation(platform, company, channel: str, external_user_id: str) -> None:
    from database.manager import utc_now_iso

    now = utc_now_iso()

    with platform["manager"].tenant(company["id"]) as conn:
        conn.execute(
            """
            INSERT INTO conversations (
                company_id, channel, external_user_id, last_message_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (company["id"], channel, external_user_id, now, now, now),
        )
        conn.commit()


def _channels(service, company) -> list[str]:
    return service.list_conversations(company_id=company["id"])["available_channels"]


def test_a_newly_connected_channel_appears_before_any_message(service, platform, alpha):
    """The filter follows the account, so a company sees what it just paid for
    rather than waiting for a customer to prove it exists."""
    _connect(platform, alpha, "instagram", "IG_1", "Shop IG")

    assert _channels(service, alpha) == ["instagram"]


def test_a_channel_that_was_never_connected_is_not_offered(service, platform, alpha):
    """A single old test message used to leave a filter nobody could remove."""
    _connect(platform, alpha, "whatsapp", "WA_1", "Shop WhatsApp")
    _conversation(platform, alpha, "messenger", "old-test-user")

    assert _channels(service, alpha) == ["whatsapp"]


def test_several_accounts_of_one_type_produce_one_filter(service, platform, alpha):
    """A three-channel bundle spent on three Instagram accounts is one channel
    in the inbox, not three. The bundle limits how many accounts, not which
    kinds."""
    _connect(platform, alpha, "instagram", "IG_1", "Main")
    _connect(platform, alpha, "instagram", "IG_2", "Second")
    _connect(platform, alpha, "instagram", "IG_3", "Third")

    assert _channels(service, alpha) == ["instagram"]


def test_one_of_each_type_produces_three_filters(service, platform, alpha):
    _connect(platform, alpha, "messenger", "FB_1", "Page")
    _connect(platform, alpha, "instagram", "IG_9", "Gram")
    _connect(platform, alpha, "whatsapp", "WA_9", "Number")

    assert _channels(service, alpha) == ["instagram", "messenger", "whatsapp"]


def test_a_disconnected_channel_loses_its_filter_but_keeps_its_history(
    service, platform, alpha
):
    """Disconnecting an account must not hide what customers said on it."""
    _connect(platform, alpha, "messenger", "FB_2", "Page")
    _conversation(platform, alpha, "messenger", "real-customer")

    with platform["manager"].control() as conn:
        conn.execute(
            "UPDATE channel_accounts SET status = 'disabled' WHERE company_id = ?",
            (alpha["id"],),
        )
        conn.commit()

    result = service.list_conversations(company_id=alpha["id"])

    assert result["available_channels"] == []
    assert any(
        item["external_user_id"] == "real-customer" for item in result["items"]
    ), "the conversation disappeared with the filter"


def test_one_company_never_sees_another_company_channels(
    service, platform, alpha, beta
):
    _connect(platform, alpha, "messenger", "FB_A", "Alpha Page")
    _connect(platform, beta, "whatsapp", "WA_B", "Beta Number")

    assert _channels(service, alpha) == ["messenger"]
    assert _channels(service, beta) == ["whatsapp"]


def test_a_company_with_nothing_connected_offers_nothing(service, alpha):
    assert _channels(service, alpha) == []
