"""Discord's Gateway: a persistent outbound WebSocket, one per connected bot.

Every other channel in this platform is reachable the moment a webhook route
exists -- Discord is not. A bot only receives messages while it holds open a
WebSocket connection to Discord's own Gateway and periodically proves it is
still alive with a heartbeat; there is no URL a company can point at this
platform and have Discord start POSTing to it. So unlike every webhook
channel here, Discord needs something that outlives any single request: a
connection this platform opens and keeps open for as long as the bot is
connected, reconnecting on its own when Discord drops it -- which Discord
does routinely, as part of normal operation, not only on failure.

Split deliberately into pure functions (`build_identify_payload`,
`parse_dispatch`) and the connection itself, the same split
`channels/slack/webhook.py` makes between `parse_slack_events` and the route
around it: the pure functions are what a test can exercise without a real
socket. What the connection class adds on top -- the heartbeat loop, the
reconnect backoff -- is protocol plumbing, not business logic, and is kept
out of the parts that decide what a message means.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

import websockets

from channels.inbound import process_inbound_event


logger = logging.getLogger(__name__)


GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

# Opcodes this platform acts on. The rest of Discord's Gateway protocol
# (voice, presence, etc.) is never sent to a bot that never asks for those
# intents in the first place.
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT.
# MESSAGE_CONTENT is a privileged intent -- Discord refuses to connect a bot
# that requests it until the operator turns it on for their application in
# the Developer Portal. That is a manual step on Discord's side, the same
# shape as Telegram's manual `setWebhook` call, and is documented on the
# connect form rather than assumed to already be done.
INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)

RECONNECT_BACKOFF_SECONDS = (1, 2, 5, 15, 30, 60)


def build_identify_payload(token: str) -> dict[str, Any]:
    return {
        "op": OP_IDENTIFY,
        "d": {
            "token": token,
            "intents": INTENTS,
            "properties": {"os": "linux", "browser": "tzone", "device": "tzone"},
        },
    }


def parse_dispatch(event_type: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """One normalised inbound event from a MESSAGE_CREATE dispatch, or
    ``None`` for anything not worth answering.

    Scoped to direct messages only -- the same choice
    `channels/slack/webhook.py` makes by routing on a DM channel id: a
    guild's public channels carry conversation among people who did not
    write to this business, and answering into one would put the
    assistant's replies in a room full of onlookers rather than a private
    conversation with a customer.
    """
    if event_type != "MESSAGE_CREATE":
        return None

    author = data.get("author") or {}

    # A message from a bot -- including this platform's own reply -- would
    # otherwise loop back in as a new customer message.
    if author.get("bot"):
        return None

    if data.get("guild_id"):
        return None

    channel_id = str(data.get("channel_id") or "").strip()
    author_id = str(author.get("id") or "").strip()
    text = str(data.get("content") or "").strip()

    if not channel_id or not author_id or not text:
        return None

    display_name = str(
        author.get("global_name") or author.get("username") or ""
    ).strip()

    return {
        "ignored": False,
        "channel": "discord",
        # The DM channel id, not the person's user id: it is what sending a
        # reply needs (`POST /channels/{id}/messages`), so it plays the role
        # `chat.id` plays for Telegram and a DM channel id plays for Slack.
        "user_id": channel_id,
        "recipient_id": channel_id,
        "text": text,
        "message_id": str(data.get("id") or "") or None,
        "timestamp": data.get("timestamp"),
        "customer_name": display_name or None,
    }


class _GiveUpAndReconnect(Exception):
    """Discord asked for a fresh session (RECONNECT / INVALID_SESSION).

    Resuming a session with its sequence number would avoid replaying
    history the customer already saw answered, but needs state cached across
    reconnects that this first version does not keep -- so every reconnect
    re-identifies from scratch instead. Worth stating plainly: it means a
    dropped connection can miss a message sent in the gap, the same window
    every webhook channel's own outage already has.
    """


class DiscordGatewayConnection:
    """One bot's Gateway connection, held open for as long as the account is
    connected and active. Reconnects on its own with backoff; a dropped
    connection is not the same thing as the account being disconnected --
    that is decided by `channels/discord/manager.py`, which is what actually
    starts and stops one of these."""

    def __init__(self, *, account_id: int, company_id: int, token: str) -> None:
        self.account_id = account_id
        self.company_id = company_id
        self.token = token
        self.task: asyncio.Task | None = None
        self._stopping = False
        self._heartbeat_task: asyncio.Task | None = None

    async def stop(self) -> None:
        self._stopping = True

        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        if self.task:
            self.task.cancel()

            with suppress(asyncio.CancelledError):
                await self.task

    async def run(self) -> None:
        attempt = 0

        while not self._stopping:
            try:
                await self._connect_once()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Discord Gateway connection for company %s (account %s) "
                    "dropped; reconnecting",
                    self.company_id,
                    self.account_id,
                    exc_info=True,
                )

            if self._stopping:
                return

            delay = RECONNECT_BACKOFF_SECONDS[
                min(attempt, len(RECONNECT_BACKOFF_SECONDS) - 1)
            ]
            attempt += 1
            await asyncio.sleep(delay)

    async def _connect_once(self) -> None:
        async with websockets.connect(GATEWAY_URL, max_size=2**20) as ws:
            hello = json.loads(await ws.recv())

            if hello.get("op") != OP_HELLO:
                raise _GiveUpAndReconnect("Expected HELLO as the first frame.")

            interval_ms = int(hello["d"]["heartbeat_interval"])
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(ws, interval_ms)
            )

            await ws.send(json.dumps(build_identify_payload(self.token)))

            try:
                async for raw in ws:
                    if self._stopping:
                        return

                    await self._handle(ws, json.loads(raw))
            finally:
                self._heartbeat_task.cancel()

    async def _heartbeat_loop(self, ws, interval_ms: int) -> None:
        try:
            while True:
                await asyncio.sleep(interval_ms / 1000)
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": None}))
        except asyncio.CancelledError:
            return

    async def _handle(self, ws, payload: dict[str, Any]) -> None:
        op = payload.get("op")

        if op == OP_HEARTBEAT:
            # Discord asking for an out-of-cycle heartbeat. Answered
            # immediately rather than waiting for the regular loop -- that is
            # the point of Discord sending this at all.
            await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": None}))
            return

        if op in (OP_RECONNECT, OP_INVALID_SESSION):
            raise _GiveUpAndReconnect(f"Discord sent opcode {op}.")

        if op == OP_HEARTBEAT_ACK:
            return

        if op != OP_DISPATCH:
            return

        event = parse_dispatch(payload.get("t") or "", payload.get("d") or {})

        if not event:
            return

        try:
            await asyncio.to_thread(
                process_inbound_event,
                event=event,
                company_id=self.company_id,
                channel_account_id=self.account_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to process a Discord event for company %s", self.company_id
            )
