"""Tests for Discord as a channel a company connects.

Discord is not shaped like every other channel here. Messenger, WhatsApp,
Telegram and Slack are all webhooks: a route exists, and the provider POSTs
to it. Discord has no such thing for ordinary messages -- a bot only
receives them by holding open a WebSocket to Discord's own Gateway, so this
file tests three different layers instead of one:

* the account (routing id derived from `GET /users/@me`, the same shape
  `slack_team_id` uses for Slack) -- `tests/test_slack_channel.py`'s shape,
* the wire protocol (`build_identify_payload`, `parse_dispatch`), which is
  pure and needs no socket at all,
* the connection manager, which decides which bots have a live connection
  and is what a route calls when an account is connected or disconnected.

Nothing here opens a real network connection. `websockets.connect` is
replaced with a fake that plays back canned frames, the same substitution
`tests/test_slack_channel.py` makes for `httpx.post`.
"""

from __future__ import annotations

import asyncio
import json

import pytest


BOT_TOKEN = "test-fixture-not-a-real-discord-bot-token"
BOT_ID = "111222333444555666"


@pytest.fixture()
def wired(platform, monkeypatch):
    import sys

    import database.manager as manager_module

    import backend.services.channel_account_service  # noqa: F401

    original = manager_module.database_manager
    test_manager = platform["manager"]

    monkeypatch.setattr(manager_module, "database_manager", test_manager)

    rebound = []
    for module in list(sys.modules.values()):
        if getattr(module, "database_manager", None) is original:
            monkeypatch.setattr(module, "database_manager", test_manager)
            rebound.append(module.__name__)

    assert "backend.services.channel_account_service" in rebound

    return test_manager


class _UsersMeResponse:
    def __init__(self, *, status_code: int = 200, bot_id: str = BOT_ID):
        self.status_code = status_code
        self._bot_id = bot_id

    def json(self):
        return {"id": self._bot_id} if self.status_code == 200 else {}


def _connect(
    company,
    monkeypatch,
    *,
    token: str = BOT_TOKEN,
    users_me: _UsersMeResponse | None = None,
):
    import backend.services.channel_account_service as service_module

    monkeypatch.setattr(
        service_module.httpx,
        "get",
        lambda *args, **kwargs: users_me or _UsersMeResponse(),
    )

    return service_module.channel_account_service.create_account(
        company_id=company["id"],
        channel="discord",
        name="Support bot",
        values={"access_token": token},
    )


# ------------------------------------------------------------------ the token


def test_the_bot_id_is_derived_from_the_token(wired, alpha, monkeypatch):
    """The operator pastes a bot token. Asking them to also find and type the
    bot's own id would add a transcription error that matters -- a wrong id
    either receives nothing, or claims a bot another company routes on."""
    account = _connect(alpha, monkeypatch)

    assert account["external_account_id"] == BOT_ID


def test_a_token_discord_rejects_is_refused(wired, alpha, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    with pytest.raises(ChannelAccountError, match="Discord"):
        _connect(alpha, monkeypatch, users_me=_UsersMeResponse(status_code=401))


def test_connecting_without_a_token_is_refused(wired, alpha):
    from backend.services.channel_account_service import (
        ChannelAccountError,
        channel_account_service,
    )

    with pytest.raises(ChannelAccountError):
        channel_account_service.create_account(
            company_id=alpha["id"],
            channel="discord",
            name="Support bot",
            values={},
        )


def test_two_companies_cannot_claim_the_same_bot(wired, alpha, beta, monkeypatch):
    from backend.services.channel_account_service import ChannelAccountError

    _connect(alpha, monkeypatch)

    with pytest.raises(ChannelAccountError):
        _connect(beta, monkeypatch)


def test_the_token_is_not_stored_in_the_clear(wired, platform, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    with platform["manager"].control() as conn:
        row = conn.execute(
            "SELECT access_token_sealed FROM channel_accounts WHERE channel = 'discord'"
        ).fetchone()

    assert row["access_token_sealed"]
    assert BOT_TOKEN not in str(row["access_token_sealed"])


# ---------------------------------------------------------------- the routing


def test_an_inbound_delivery_resolves_the_owning_company(wired, alpha, monkeypatch):
    account = _connect(alpha, monkeypatch)

    match = wired.resolve_account_for_channel(channel="discord", page_id=BOT_ID)

    assert match["company_id"] == alpha["id"]
    assert match["account_id"] == account["id"]


def test_a_bot_nobody_connected_resolves_to_nothing(wired, alpha, monkeypatch):
    _connect(alpha, monkeypatch)

    assert (
        wired.resolve_account_for_channel(channel="discord", page_id="999999999999999999")
        is None
    )


# ----------------------------------------------------------- the wire protocol


def test_the_identify_payload_carries_the_token_and_intents():
    from channels.discord.gateway import INTENTS, OP_IDENTIFY, build_identify_payload

    payload = build_identify_payload("a-token")

    assert payload["op"] == OP_IDENTIFY
    assert payload["d"]["token"] == "a-token"
    assert payload["d"]["intents"] == INTENTS


def _message_create(
    *, text: str = "hello", author_id: str = "U1", channel_id: str = "C1",
    bot: bool = False, guild_id: str | None = None,
) -> dict:
    return {
        "id": "M1",
        "channel_id": channel_id,
        "content": text,
        "timestamp": "2026-01-01T00:00:00.000000+00:00",
        "author": {"id": author_id, "username": "rana", "bot": bot},
        **({"guild_id": guild_id} if guild_id else {}),
    }


def test_a_direct_message_is_parsed_into_a_normalised_event():
    from channels.discord.gateway import parse_dispatch

    event = parse_dispatch("MESSAGE_CREATE", _message_create())

    assert event["channel"] == "discord"
    assert event["text"] == "hello"
    assert event["user_id"] == "C1"
    assert event["recipient_id"] == "C1"
    assert event["customer_name"] == "rana"


def test_the_bots_own_message_is_not_answered_again(wired):
    """Without this, every reply this platform sends would loop back in as a
    new customer message."""
    from channels.discord.gateway import parse_dispatch

    assert parse_dispatch("MESSAGE_CREATE", _message_create(bot=True)) is None


def test_a_guild_channel_message_is_not_answered(wired):
    """Scoped to direct messages only: a public server channel carries
    conversation among people who did not write to this business."""
    from channels.discord.gateway import parse_dispatch

    assert (
        parse_dispatch("MESSAGE_CREATE", _message_create(guild_id="G1")) is None
    )


def test_a_message_with_no_content_is_ignored():
    from channels.discord.gateway import parse_dispatch

    assert parse_dispatch("MESSAGE_CREATE", _message_create(text="")) is None


def test_a_non_message_dispatch_is_ignored():
    from channels.discord.gateway import parse_dispatch

    assert parse_dispatch("TYPING_START", {"channel_id": "C1"}) is None


# -------------------------------------------------------- the connection itself


class FakeWebSocket:
    """Plays back canned frames: `recv()` for the first (HELLO), then
    iteration for everything after -- the same split `_connect_once` makes."""

    def __init__(self, frames: list[str]):
        self._frames = list(frames)
        self.sent: list[str] = []

    async def recv(self):
        return self._frames.pop(0)

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


class FakeConnectContext:
    def __init__(self, ws: FakeWebSocket):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc_info):
        return False


def test_hello_then_message_create_reaches_the_shared_pipeline(monkeypatch):
    import channels.discord.gateway as gateway_module

    hello = json.dumps(
        {"op": gateway_module.OP_HELLO, "d": {"heartbeat_interval": 45000}}
    )
    dispatch = json.dumps(
        {
            "op": gateway_module.OP_DISPATCH,
            "t": "MESSAGE_CREATE",
            "s": 1,
            "d": _message_create(text="I need help", channel_id="C42"),
        }
    )

    ws = FakeWebSocket([hello, dispatch])
    monkeypatch.setattr(
        gateway_module.websockets, "connect", lambda *a, **k: FakeConnectContext(ws)
    )

    captured = {}

    def fake_process_inbound_event(**kwargs):
        captured.update(kwargs)
        return {"status": "received_ai_queued"}

    monkeypatch.setattr(
        gateway_module, "process_inbound_event", fake_process_inbound_event
    )

    connection = gateway_module.DiscordGatewayConnection(
        account_id=7, company_id=3, token=BOT_TOKEN
    )

    asyncio.run(connection._connect_once())

    assert captured["event"]["text"] == "I need help"
    assert captured["event"]["recipient_id"] == "C42"
    assert captured["company_id"] == 3
    assert captured["channel_account_id"] == 7

    identify_sent = [json.loads(frame) for frame in ws.sent]
    assert any(
        frame["op"] == gateway_module.OP_IDENTIFY and frame["d"]["token"] == BOT_TOKEN
        for frame in identify_sent
    )


def test_a_heartbeat_request_is_answered_immediately(monkeypatch):
    import channels.discord.gateway as gateway_module

    hello = json.dumps(
        {"op": gateway_module.OP_HELLO, "d": {"heartbeat_interval": 45000}}
    )
    heartbeat_request = json.dumps({"op": gateway_module.OP_HEARTBEAT})

    ws = FakeWebSocket([hello, heartbeat_request])
    monkeypatch.setattr(
        gateway_module.websockets, "connect", lambda *a, **k: FakeConnectContext(ws)
    )

    connection = gateway_module.DiscordGatewayConnection(
        account_id=1, company_id=1, token=BOT_TOKEN
    )
    asyncio.run(connection._connect_once())

    frames = [json.loads(frame) for frame in ws.sent]
    assert any(frame["op"] == gateway_module.OP_HEARTBEAT for frame in frames)


def test_an_invalid_session_ends_the_connection_attempt(monkeypatch):
    """Discord asking to start over is not swallowed silently -- it has to
    surface so `run()`'s reconnect loop actually reconnects rather than
    sitting on a session Discord has already discarded."""
    import channels.discord.gateway as gateway_module

    hello = json.dumps(
        {"op": gateway_module.OP_HELLO, "d": {"heartbeat_interval": 45000}}
    )
    invalid_session = json.dumps(
        {"op": gateway_module.OP_INVALID_SESSION, "d": False}
    )

    ws = FakeWebSocket([hello, invalid_session])
    monkeypatch.setattr(
        gateway_module.websockets, "connect", lambda *a, **k: FakeConnectContext(ws)
    )

    connection = gateway_module.DiscordGatewayConnection(
        account_id=1, company_id=1, token=BOT_TOKEN
    )

    with pytest.raises(gateway_module._GiveUpAndReconnect):
        asyncio.run(connection._connect_once())


# ----------------------------------------------------------------- the manager


def test_start_connection_is_a_noop_before_the_app_has_a_loop(monkeypatch):
    """A channel account can legitimately be created before the gateway
    module has captured a running loop (most often in a test). Nothing
    should raise -- the connection simply starts the next time `start_all`
    runs."""
    import channels.discord.manager as manager_module

    monkeypatch.setattr(manager_module, "_loop", None)
    monkeypatch.setattr(manager_module, "_connections", {})

    manager_module.start_connection(account_id=1, company_id=2, token="t")

    assert manager_module.is_connected(1) is False


def test_start_all_connects_every_active_account_and_stop_all_closes_them(
    monkeypatch,
):
    import channels.discord.manager as manager_module

    monkeypatch.setattr(manager_module, "_connections", {})
    monkeypatch.setattr(manager_module, "_loop", None)

    monkeypatch.setattr(
        manager_module.channel_account_service,
        "active_accounts_for_channel",
        lambda channel: [{"account_id": 9, "company_id": 3, "access_token": "t"}],
    )

    async def fake_run(self):
        # A real connection would run until cancelled; this stands in for
        # that without opening a socket.
        await asyncio.Event().wait()

    monkeypatch.setattr(manager_module.DiscordGatewayConnection, "run", fake_run)

    async def scenario():
        await manager_module.start_all()
        assert manager_module.is_connected(9) is True

        await manager_module.stop_all()
        assert manager_module.is_connected(9) is False

    asyncio.run(scenario())


def test_start_connection_and_stop_connection_from_a_sync_caller(monkeypatch):
    """The shape a request handler actually uses: schedule work onto the
    loop rather than run it inline, since a sync route cannot `await`."""
    import channels.discord.manager as manager_module

    monkeypatch.setattr(manager_module, "_connections", {})

    async def fake_run(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(manager_module.DiscordGatewayConnection, "run", fake_run)

    async def scenario():
        manager_module._loop = asyncio.get_running_loop()

        manager_module.start_connection(account_id=5, company_id=1, token="t")
        await asyncio.sleep(0.05)
        assert manager_module.is_connected(5) is True

        manager_module.stop_connection(5)
        await asyncio.sleep(0.05)
        assert manager_module.is_connected(5) is False

        manager_module._loop = None

    asyncio.run(scenario())


# ------------------------------------------------------------------ the sender


def test_discord_is_reachable_through_the_shared_dispatcher():
    from channels.sender import SUPPORTED_CHANNELS

    assert "discord" in SUPPORTED_CHANNELS


def test_send_text_actually_calls_the_discord_sender(wired, alpha, monkeypatch):
    """The same gap pinned in `tests/test_slack_channel.py`: being listed in
    `SUPPORTED_CHANNELS` does not by itself prove `send_text` calls
    anything."""
    import channels.sender as sender_module

    captured = {}

    def fake_send_discord_text(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "skipped": False}

    monkeypatch.setattr(sender_module, "send_discord_text", fake_send_discord_text)

    result = sender_module.send_text(
        channel="discord", recipient_id="C999", company_id=alpha["id"], text="hi"
    )

    assert result["ok"] is True
    assert captured["recipient_id"] == "C999"


def test_sending_without_a_connected_account_fails_rather_than_raising(wired, alpha):
    from channels.discord.sender import send_discord_text

    result = send_discord_text(recipient_id="C1", text="hello", company_id=alpha["id"])

    assert result["ok"] is False
    assert result["error"]


def test_the_sender_uses_the_companys_own_token(wired, alpha, monkeypatch):
    import channels.discord.sender as sender_module

    _connect(alpha, monkeypatch)
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "M99"}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["payload"] = kwargs.get("json")
        return Response()

    monkeypatch.setattr(sender_module.httpx, "post", fake_post)

    result = sender_module.send_discord_text(
        recipient_id="C1", text="hi", company_id=alpha["id"]
    )

    assert result["ok"] is True
    assert captured["headers"]["Authorization"] == f"Bot {BOT_TOKEN}"
    assert captured["payload"]["content"] == "hi"
    assert "C1" in captured["url"]
