"""Inbound routing must only ever match a channel's *guarded* identifier.

A channel account is kept unique per channel on one column — the routing field:
`page_id` for Messenger, `instagram_business_id` for Instagram, `phone_number_id`
for WhatsApp, `external_account_id` for Telegram. `_assert_routing_id_is_free`
refuses to let two companies claim the same value on that column.

The other identifier columns are free-form and tenant-writable. The resolver
used to probe `page_id` first for *every* channel, so a company could connect an
Instagram account under its own (unique) `instagram_business_id` while writing a
victim company's Instagram id into the unguarded `page_id` column — and every
inbound Instagram DM for the victim resolved to the attacker's inbox. The same
shape produced a cross-tenant denial of service on Telegram.

These tests pin the fix: routing authorises only against the guarded column, so
the shadow rows below change nothing, and each channel still reaches its real
owner.
"""

from __future__ import annotations

import pytest

from backend.services.channel_account_service import (
    channel_account_service,
    telegram_bot_id,
)


@pytest.fixture()
def wired(platform, monkeypatch):
    """Point the service and the resolver at the test databases."""
    import sys

    import database.manager as manager_module

    original = manager_module.database_manager
    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)

    return test_manager


def _resolve(manager, **kwargs):
    match = manager.resolve_account_for_channel(**kwargs)
    return match["company_id"] if match else None


def test_instagram_dm_reaches_its_real_owner_not_a_page_id_shadow(
    wired, alpha, beta
):
    victim, attacker = alpha["id"], beta["id"]

    channel_account_service.create_account(
        company_id=victim, channel="instagram", name="Victim IG",
        values={"instagram_business_id": "IG_VICTIM"},
    )
    # The attacker holds their own (unique) Instagram id, but writes the
    # victim's id into the unguarded page_id column.
    channel_account_service.create_account(
        company_id=attacker, channel="instagram", name="Attacker IG",
        values={"instagram_business_id": "IG_ATTACKER", "page_id": "IG_VICTIM"},
    )

    # The Meta webhook carries the Instagram id in both slots for an IG event.
    resolved = _resolve(
        wired, channel="instagram",
        page_id="IG_VICTIM", instagram_business_id="IG_VICTIM",
    )
    assert resolved == victim, "an Instagram DM was routed by an unguarded page_id"


def test_telegram_delivery_reaches_its_real_owner_not_a_page_id_shadow(
    wired, alpha, beta
):
    victim, attacker = alpha["id"], beta["id"]

    victim_token = "123456789:AAExampleBotTokenaaaaaaaaaaaaaaaaaa"
    bot_id = telegram_bot_id(victim_token)
    channel_account_service.create_account(
        company_id=victim, channel="telegram", name="Victim TG",
        values={"access_token": victim_token},
    )
    # The attacker's own bot, with the victim's bot id smuggled into page_id.
    attacker_token = "987654321:BBExampleBotTokenbbbbbbbbbbbbbbbbbb"
    channel_account_service.create_account(
        company_id=attacker, channel="telegram", name="Attacker TG",
        values={"access_token": attacker_token, "page_id": bot_id},
    )

    resolved = _resolve(wired, channel="telegram", page_id=bot_id)
    assert resolved == victim, "a Telegram delivery was routed by an unguarded page_id"


def _slack_auth_test_ok(team_id: str):
    class Response:
        @staticmethod
        def json():
            return {"ok": True, "team_id": team_id}

    return lambda *args, **kwargs: Response()


def test_slack_delivery_reaches_its_real_owner_not_a_page_id_shadow(
    wired, alpha, beta, monkeypatch
):
    """The same shadow the Telegram test above pins, for the other channel
    that also routes on the shared `external_account_id` column."""
    import backend.services.channel_account_service as service_module

    victim, attacker = alpha["id"], beta["id"]

    monkeypatch.setattr(
        service_module.httpx, "post", _slack_auth_test_ok("T_VICTIM")
    )
    channel_account_service.create_account(
        company_id=victim, channel="slack", name="Victim Slack",
        values={"access_token": "test-fixture-token-victim"},
    )

    # The attacker's own workspace, with the victim's team id smuggled into
    # the unguarded page_id column.
    monkeypatch.setattr(
        service_module.httpx, "post", _slack_auth_test_ok("T_ATTACKER")
    )
    channel_account_service.create_account(
        company_id=attacker, channel="slack", name="Attacker Slack",
        values={"access_token": "test-fixture-token-attacker", "page_id": "T_VICTIM"},
    )

    resolved = _resolve(wired, channel="slack", page_id="T_VICTIM")
    assert resolved == victim, "a Slack delivery was routed by an unguarded page_id"


def test_every_channel_still_reaches_its_legitimate_owner(wired, alpha, monkeypatch):
    """The negative tests above are only meaningful if routing still works."""
    import backend.services.channel_account_service as service_module

    company = alpha["id"]

    channel_account_service.create_account(
        company_id=company, channel="messenger", name="FB",
        values={"page_id": "PAGE_1"},
    )
    channel_account_service.create_account(
        company_id=company, channel="instagram", name="IG",
        values={"instagram_business_id": "IG_1"},
    )
    channel_account_service.create_account(
        company_id=company, channel="whatsapp", name="WA",
        values={"phone_number_id": "WA_1"},
    )
    token = "111222333:AACleanBotTokencccccccccccccccccccc"
    channel_account_service.create_account(
        company_id=company, channel="telegram", name="TG",
        values={"access_token": token},
    )
    monkeypatch.setattr(
        service_module.httpx, "post", _slack_auth_test_ok("T_CLEAN")
    )
    channel_account_service.create_account(
        company_id=company, channel="slack", name="Slack",
        values={"access_token": "test-fixture-token-clean"},
    )

    assert _resolve(wired, channel="messenger", page_id="PAGE_1") == company
    assert _resolve(
        wired, channel="instagram",
        page_id="IG_1", instagram_business_id="IG_1",
    ) == company
    assert _resolve(wired, channel="whatsapp", phone_number_id="WA_1") == company
    assert _resolve(
        wired, channel="telegram", page_id=telegram_bot_id(token)
    ) == company
    assert _resolve(wired, channel="slack", page_id="T_CLEAN") == company
