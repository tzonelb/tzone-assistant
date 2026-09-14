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

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.services.channel_account_service import (
    channel_account_service,
    telegram_bot_id,
)

# One throwaway RSA keypair for this file's Google Chat tests -- see
# `tests/test_google_chat_channel.py` for why it is generated once rather
# than per test.
_GOOGLE_CHAT_PRIVATE_KEY_PEM = rsa.generate_private_key(
    public_exponent=65537, key_size=2048
).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def _google_chat_service_account_json(client_email: str) -> str:
    return json.dumps(
        {
            "project_id": "test-project",
            "private_key": _GOOGLE_CHAT_PRIVATE_KEY_PEM,
            "client_email": client_email,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
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


def _discord_users_me_ok(bot_id: str):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"id": bot_id}

    return lambda *args, **kwargs: Response()


def test_discord_delivery_reaches_its_real_owner_not_a_page_id_shadow(
    wired, alpha, beta, monkeypatch
):
    """The same shadow again, for the third channel routing on the shared
    `external_account_id` column."""
    import backend.services.channel_account_service as service_module

    victim, attacker = alpha["id"], beta["id"]

    monkeypatch.setattr(service_module.httpx, "get", _discord_users_me_ok("D_VICTIM"))
    channel_account_service.create_account(
        company_id=victim, channel="discord", name="Victim Discord",
        values={"access_token": "test-fixture-token-victim"},
    )

    # The attacker's own bot, with the victim's bot id smuggled into the
    # unguarded page_id column.
    monkeypatch.setattr(service_module.httpx, "get", _discord_users_me_ok("D_ATTACKER"))
    channel_account_service.create_account(
        company_id=attacker, channel="discord", name="Attacker Discord",
        values={"access_token": "test-fixture-token-attacker", "page_id": "D_VICTIM"},
    )

    resolved = _resolve(wired, channel="discord", page_id="D_VICTIM")
    assert resolved == victim, "a Discord delivery was routed by an unguarded page_id"


def _viber_account_info_ok(account_id: str):
    class Response:
        @staticmethod
        def json():
            return {"status": 0, "status_message": "ok", "id": account_id}

    return lambda *args, **kwargs: Response()


def test_viber_delivery_reaches_its_real_owner_not_a_page_id_shadow(
    wired, alpha, beta, monkeypatch
):
    """The same shadow again, for the fourth channel routing on the shared
    `external_account_id` column."""
    import backend.services.channel_account_service as service_module

    victim, attacker = alpha["id"], beta["id"]

    monkeypatch.setattr(
        service_module.httpx, "post", _viber_account_info_ok("pa:VICTIM")
    )
    channel_account_service.create_account(
        company_id=victim, channel="viber", name="Victim Viber",
        values={"access_token": "test-fixture-token-victim"},
    )

    # The attacker's own bot, with the victim's account id smuggled into the
    # unguarded page_id column.
    monkeypatch.setattr(
        service_module.httpx, "post", _viber_account_info_ok("pa:ATTACKER")
    )
    channel_account_service.create_account(
        company_id=attacker, channel="viber", name="Attacker Viber",
        values={"access_token": "test-fixture-token-attacker", "page_id": "pa:VICTIM"},
    )

    resolved = _resolve(wired, channel="viber", page_id="pa:VICTIM")
    assert resolved == victim, "a Viber delivery was routed by an unguarded page_id"


def _line_bot_info_ok(user_id: str):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"userId": user_id, "basicId": "@x", "displayName": "Bot"}

    return lambda *args, **kwargs: Response()


def test_line_delivery_reaches_its_real_owner_not_a_page_id_shadow(
    wired, alpha, beta, monkeypatch
):
    """The same shadow again, for the fifth channel routing on the shared
    `external_account_id` column."""
    import backend.services.channel_account_service as service_module

    victim, attacker = alpha["id"], beta["id"]

    monkeypatch.setattr(service_module.httpx, "get", _line_bot_info_ok("UVICTIM"))
    channel_account_service.create_account(
        company_id=victim, channel="line", name="Victim LINE",
        values={"access_token": "test-fixture-token-victim", "verify_token": "victim-secret"},
    )

    # The attacker's own bot, with the victim's account id smuggled into the
    # unguarded page_id column.
    monkeypatch.setattr(service_module.httpx, "get", _line_bot_info_ok("UATTACKER"))
    channel_account_service.create_account(
        company_id=attacker, channel="line", name="Attacker LINE",
        values={
            "access_token": "test-fixture-token-attacker",
            "verify_token": "attacker-secret",
            "page_id": "UVICTIM",
        },
    )

    resolved = _resolve(wired, channel="line", page_id="UVICTIM")
    assert resolved == victim, "a LINE delivery was routed by an unguarded page_id"


def _twilio_phone_lookup_ok(phone_sid: str = "PNexample000000000000000000000000"):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"incoming_phone_numbers": [{"sid": phone_sid}]}

    return lambda *args, **kwargs: Response()


def test_sms_delivery_reaches_its_real_owner_not_a_page_id_shadow(
    wired, alpha, beta, monkeypatch
):
    """The same shadow again, for the sixth channel routing on the shared
    `external_account_id` column -- here a phone number, typed in and
    confirmed against Twilio rather than derived from a token."""
    import backend.services.channel_account_service as service_module

    victim, attacker = alpha["id"], beta["id"]

    monkeypatch.setattr(service_module.httpx, "get", _twilio_phone_lookup_ok())
    channel_account_service.create_account(
        company_id=victim, channel="sms", name="Victim SMS",
        values={
            "external_account_id": "+15550001111",
            "access_token": "test-fixture-token-victim",
            "account_sid": "ACvictim00000000000000000000000000",
        },
    )

    # The attacker's own number, with the victim's number smuggled into the
    # unguarded page_id column.
    channel_account_service.create_account(
        company_id=attacker, channel="sms", name="Attacker SMS",
        values={
            "external_account_id": "+15559998888",
            "access_token": "test-fixture-token-attacker",
            "account_sid": "ACattacker0000000000000000000000000",
            "page_id": "+15550001111",
        },
    )

    resolved = _resolve(wired, channel="sms", page_id="+15550001111")
    assert resolved == victim, "an SMS delivery was routed by an unguarded page_id"


def _google_chat_token_mint_ok():
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "fake-access-token"}

    return lambda *args, **kwargs: Response()


def test_google_chat_delivery_reaches_its_real_owner_not_a_page_id_shadow(
    wired, alpha, beta, monkeypatch
):
    """The same shadow again, for the seventh channel routing on the shared
    `external_account_id` column -- here a service account's `client_email`,
    read out of the pasted key rather than asked of the provider."""
    import backend.services.channel_account_service as service_module

    victim, attacker = alpha["id"], beta["id"]

    monkeypatch.setattr(service_module.httpx, "post", _google_chat_token_mint_ok())
    channel_account_service.create_account(
        company_id=victim, channel="google_chat", name="Victim Chat app",
        values={"access_token": _google_chat_service_account_json("victim@test-project.iam.gserviceaccount.com")},
    )

    # The attacker's own bot, with the victim's client_email smuggled into
    # the unguarded page_id column.
    channel_account_service.create_account(
        company_id=attacker, channel="google_chat", name="Attacker Chat app",
        values={
            "access_token": _google_chat_service_account_json("attacker@test-project.iam.gserviceaccount.com"),
            "page_id": "victim@test-project.iam.gserviceaccount.com",
        },
    )

    resolved = _resolve(
        wired, channel="google_chat", page_id="victim@test-project.iam.gserviceaccount.com"
    )
    assert resolved == victim, "a Google Chat delivery was routed by an unguarded page_id"


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
    monkeypatch.setattr(
        service_module.httpx, "get", _discord_users_me_ok("D_CLEAN")
    )
    channel_account_service.create_account(
        company_id=company, channel="discord", name="Discord",
        values={"access_token": "test-fixture-token-clean-discord"},
    )
    monkeypatch.setattr(
        service_module.httpx, "post", _viber_account_info_ok("pa:CLEAN")
    )
    channel_account_service.create_account(
        company_id=company, channel="viber", name="Viber",
        values={"access_token": "test-fixture-token-clean-viber"},
    )
    monkeypatch.setattr(service_module.httpx, "get", _line_bot_info_ok("ULINECLEAN"))
    channel_account_service.create_account(
        company_id=company, channel="line", name="LINE",
        values={
            "access_token": "test-fixture-token-clean-line",
            "verify_token": "clean-secret",
        },
    )
    monkeypatch.setattr(service_module.httpx, "get", _twilio_phone_lookup_ok())
    channel_account_service.create_account(
        company_id=company, channel="sms", name="SMS",
        values={
            "external_account_id": "+15551230000",
            "access_token": "test-fixture-token-clean-sms",
            "account_sid": "ACclean000000000000000000000000000",
        },
    )
    monkeypatch.setattr(service_module.httpx, "post", _google_chat_token_mint_ok())
    channel_account_service.create_account(
        company_id=company, channel="google_chat", name="Google Chat",
        values={
            "access_token": _google_chat_service_account_json(
                "clean@test-project.iam.gserviceaccount.com"
            ),
        },
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
    assert _resolve(wired, channel="discord", page_id="D_CLEAN") == company
    assert _resolve(wired, channel="viber", page_id="pa:CLEAN") == company
    assert _resolve(wired, channel="line", page_id="ULINECLEAN") == company
    assert _resolve(wired, channel="sms", page_id="+15551230000") == company
    assert _resolve(
        wired, channel="google_chat", page_id="clean@test-project.iam.gserviceaccount.com"
    ) == company
