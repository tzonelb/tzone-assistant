"""Tests for Telegram as a channel a company connects, rather than a script.

Telegram worked. `channels/telegram/bot.py` held one bot token from the
environment, polled, and answered customers — for exactly one company, because
one process holds one token. Then the platform became multi-tenant,
`message_gateway.handle_text` gained a required `company_id`, and nothing
updated the one caller living outside the request path. Every message raised
`TypeError` from then on, silently: no test covers a standalone script and
`main.py` never imports it.

The deeper problem was the shape, not the argument. The bot went through the
engine's Telegram branch, which pinned every conversation into
`telegram_iptv_start` with the department forced to `iptv` — T-ZONE's own IPTV
support script, applied to whichever company happened to run it. A channel is
not a business.

So Telegram is now a channel account like any other: the company connects its
own bot, the bot id routes inbound deliveries, and the reply comes from that
company's own departments and knowledge.
"""

from __future__ import annotations

import pytest


# A token in BotFather's real shape. Not a live one — the numeric prefix is what
# the routing derives, and nothing here reaches Telegram.
TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
BOT_ID = "123456789"


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401
    import channels.telegram.webhook  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    for required in (
        "backend.services.channel_account_service",
        "channels.telegram.webhook",
    ):
        assert required in rebound, f"{required} still holds the real manager"

    return test_manager


def _connect(company, *, token: str = TOKEN, secret: str | None = "s3cret"):
    from backend.services.channel_account_service import channel_account_service

    values = {"access_token": token}

    if secret:
        values["verify_token"] = secret

    return channel_account_service.create_account(
        company_id=company["id"],
        channel="telegram",
        name="Support bot",
        values=values,
    )


# ------------------------------------------------------------------ the token


def test_the_bot_id_is_derived_from_the_token(wired, alpha):
    """The operator pastes one line from BotFather. Asking them to also type
    the numeric id would add the one transcription error that matters — a wrong
    id either receives nothing, or claims an id another company routes on."""
    account = _connect(alpha)

    assert account["external_account_id"] == BOT_ID


def test_a_token_that_is_not_a_token_is_refused(wired, alpha):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="BotFather"):
        _connect(alpha, token="just-some-text")


def test_connecting_without_a_token_is_refused(wired, alpha):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with pytest.raises(ChannelAccountError):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="telegram",
            name="Support bot",
            values={},
        )


def test_two_companies_cannot_claim_the_same_bot(wired, alpha, beta):
    """The unique index is per channel and per routing id. Without it, the
    second company would silently receive the first company's customers."""
    from backend.services.channel_account_service import ChannelAccountError

    _connect(alpha)

    with pytest.raises(ChannelAccountError):
        _connect(beta)


def test_the_token_is_not_stored_in_the_clear(wired, platform, alpha):
    _connect(alpha)

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed FROM channel_accounts WHERE channel = 'telegram'"
        ).fetchone()

    assert row["access_token_sealed"]
    assert TOKEN not in str(row["access_token_sealed"])


# ---------------------------------------------------------------- the routing


def test_an_inbound_delivery_resolves_the_owning_company(wired, alpha):
    account = _connect(alpha)

    match = wired.resolve_account_for_channel(channel="telegram", page_id=BOT_ID)

    assert match["company_id"] == alpha["id"]
    assert match["account_id"] == account["id"]


def test_a_bot_nobody_connected_resolves_to_nothing(wired, alpha):
    _connect(alpha)

    assert wired.resolve_account_for_channel(channel="telegram", page_id="99") is None


def test_a_telegram_id_does_not_match_another_channel(wired, platform, alpha):
    """Routing is filtered by channel. A WhatsApp number and a Telegram bot id
    are different namespaces and could legitimately collide."""
    _connect(alpha)

    assert (
        wired.resolve_account_for_channel(channel="whatsapp", phone_number_id=BOT_ID)
        is None
    )


# ------------------------------------------------------------ the webhook auth


@pytest.fixture()
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from channels.telegram import webhook as telegram_webhook

    app = FastAPI()
    app.include_router(telegram_webhook.router)

    return TestClient(app)


def _update(text: str = "hello") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 11,
            "chat": {"id": 555},
            "from": {"first_name": "Rana", "last_name": "Khoury"},
            "text": text,
        },
    }


def test_a_delivery_with_the_right_secret_is_accepted(client, wired, alpha, monkeypatch):
    import channels.telegram.webhook as module

    _connect(alpha)
    monkeypatch.setattr(module, "dispatch", lambda *args, **kwargs: None)

    response = client.post(
        f"/webhook/telegram/{BOT_ID}",
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        json=_update(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"


def test_a_delivery_with_no_secret_is_refused(client, wired, alpha):
    _connect(alpha)

    response = client.post(f"/webhook/telegram/{BOT_ID}", json=_update())

    assert response.status_code == 403


def test_a_delivery_with_the_wrong_secret_is_refused(client, wired, alpha):
    _connect(alpha)

    response = client.post(
        f"/webhook/telegram/{BOT_ID}",
        headers={"X-Telegram-Bot-Api-Secret-Token": "guessed"},
        json=_update(),
    )

    assert response.status_code == 403


def test_an_account_with_no_secret_registered_is_refused(client, wired, alpha):
    """Not waved through. The bot id in the URL is public — it is in the bot's
    own username lookup — so an unauthenticated endpoint would let anybody post
    into this company's inbox as any customer they chose."""
    _connect(alpha, secret=None)

    response = client.post(
        f"/webhook/telegram/{BOT_ID}",
        headers={"X-Telegram-Bot-Api-Secret-Token": "anything"},
        json=_update(),
    )

    assert response.status_code == 403


def test_a_delivery_for_an_unknown_bot_is_refused(client, wired, alpha):
    _connect(alpha)

    response = client.post(
        "/webhook/telegram/987654321",
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        json=_update(),
    )

    assert response.status_code == 403


def test_one_companys_secret_does_not_open_anothers_bot(
    client, wired, alpha, beta, monkeypatch
):
    import channels.telegram.webhook as module

    monkeypatch.setattr(module, "dispatch", lambda *args, **kwargs: None)

    _connect(alpha)
    _connect(beta, token="222222222:BBmzTcvCH1vGWJxfSeofSAs0K5PALDsaw", secret="other")

    refused = client.post(
        f"/webhook/telegram/{BOT_ID}",
        headers={"X-Telegram-Bot-Api-Secret-Token": "other"},
        json=_update(),
    )

    assert refused.status_code == 403


# ----------------------------------------------------------------- the parsing


def test_the_senders_name_is_read_out_of_the_delivery(wired):
    """Telegram sends the name with every update. Without reading it the inbox
    would show a numeric chat id for a customer whose name arrived in the same
    request — `resolve_meta_profile` answers only for Messenger."""
    from channels.telegram.webhook import parse_telegram_events

    events = parse_telegram_events(_update(), BOT_ID)

    assert events[0]["customer_name"] == "Rana Khoury"
    assert events[0]["user_id"] == "555"


def test_an_edited_message_is_not_answered_again(wired):
    """Replying to an edit would answer one thing the customer said twice."""
    from channels.telegram.webhook import parse_telegram_events

    payload = {"update_id": 2, "edited_message": _update()["message"]}

    assert parse_telegram_events(payload, BOT_ID) == []


def test_a_batch_of_updates_is_read_in_full(wired):
    """Telegram delivers a list after a webhook is re-armed following an
    outage. Reading only the first would discard the backlog."""
    from channels.telegram.webhook import parse_telegram_events

    batch = [_update("first"), _update("second")]

    assert [event["text"] for event in parse_telegram_events(batch, BOT_ID)] == [
        "first",
        "second",
    ]


def test_a_message_with_no_text_is_ignored(wired):
    from channels.telegram.webhook import parse_telegram_events

    payload = {"update_id": 3, "message": {"message_id": 1, "chat": {"id": 5}}}

    assert parse_telegram_events(payload, BOT_ID) == []


# ------------------------------------------------------------------ the sender


def test_telegram_is_reachable_through_the_shared_dispatcher():
    """It had no dispatcher entry at all, so an employee's manual reply, a
    scheduled message and the takeover handback all had nowhere to go on this
    channel."""
    from channels.sender import SUPPORTED_CHANNELS

    assert "telegram" in SUPPORTED_CHANNELS


def test_sending_without_a_connected_account_fails_rather_than_raising(wired, alpha):
    """The dispatcher's contract is a result dict. A sender that threw would
    take down the batch a customer is waiting in."""
    from channels.telegram.sender import send_telegram_text

    result = send_telegram_text(
        recipient_id="555", text="hello", company_id=alpha["id"]
    )

    assert result["ok"] is False
    assert result["error"]


def test_the_sender_uses_the_companys_own_token(wired, alpha, monkeypatch):
    """`TELEGRAM_BOT_TOKEN` in the environment is what made this
    single-company: a platform serving a thousand businesses cannot answer them
    all from one bot."""
    import channels.telegram.sender as sender_module

    _connect(alpha)
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {"message_id": 7}}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json")

        return Response()

    monkeypatch.setattr(sender_module.httpx, "post", fake_post)

    result = sender_module.send_telegram_text(
        recipient_id="555", text="hi", company_id=alpha["id"], buttons=["Sales"]
    )

    assert result["ok"] is True
    assert TOKEN in captured["url"]
    assert captured["payload"]["chat_id"] == "555"
    assert captured["payload"]["reply_markup"]["keyboard"] == [[{"text": "Sales"}]]


# ------------------------------------------------------------ the IPTV shape


def test_the_polling_bot_no_longer_calls_the_gateway_without_a_company():
    """The regression itself: `handle_text` gained a required `company_id` when
    the platform became multi-tenant, and this one caller — outside the request
    path, covered by no test, imported by no module — was never updated."""
    import ast
    import inspect

    import channels.telegram.bot as bot_module

    # Read the imports, not the text. This module's own docstring names the
    # function it stopped calling, so a substring search matches the
    # explanation rather than the code — the same trap
    # `test_channel_threading.py` hit.
    tree = ast.parse(inspect.getsource(bot_module))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    } | {
        (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any("message_gateway" in name for name in imported)
    assert "process_inbound_event" in imported


def test_the_polling_bot_resolves_its_company_before_it_starts():
    """A token nobody connected used to be answered by whichever company the
    engine happened to resolve — somebody else's business replying to a
    customer who did not write to them."""
    import inspect

    import channels.telegram.bot as bot_module

    source = inspect.getsource(bot_module.run_telegram)

    assert "_resolve_account" in source


def test_the_polling_bot_refuses_an_unconnected_token(wired):
    import channels.telegram.bot as bot_module

    with pytest.raises(RuntimeError, match="connected"):
        bot_module._resolve_account(TOKEN)
