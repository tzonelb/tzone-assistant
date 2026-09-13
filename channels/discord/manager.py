"""Which Discord bots are connected right now, and who is holding them open.

Every webhook channel is reachable the moment its route exists; Discord is
reachable only while something on this side keeps a Gateway connection open
per bot (see `channels/discord/gateway.py`). This module is that something:
it knows, at any moment, which company's bot has a live connection, and is
the one place that starts or stops one.

Two callers, deliberately different:

* `start_all` / `stop_all` run once, at the app's own startup and shutdown --
  every already-connected Discord account gets a connection when the process
  comes up, and every connection is closed cleanly when it goes down.
* `start_connection` / `stop_connection` run from an ordinary (synchronous)
  request handler the moment a company connects or disconnects a Discord
  account, so a new bot starts receiving messages without a server restart,
  and a removed one stops immediately rather than lingering until the next
  deploy. A sync route cannot `await` a coroutine, so these hand the actual
  work to the event loop captured at startup with
  `asyncio.run_coroutine_threadsafe` instead of running it inline.
"""

from __future__ import annotations

import asyncio
import logging

from backend.services.channel_account_service import channel_account_service
from channels.discord.gateway import DiscordGatewayConnection


logger = logging.getLogger(__name__)


_connections: dict[int, DiscordGatewayConnection] = {}
_loop: asyncio.AbstractEventLoop | None = None


def _start(account_id: int, company_id: int, token: str) -> None:
    if account_id in _connections:
        return

    connection = DiscordGatewayConnection(
        account_id=account_id, company_id=company_id, token=token
    )
    connection.task = asyncio.create_task(connection.run())
    _connections[account_id] = connection


async def start_all() -> None:
    """Connect every company's active Discord bot. Called once at boot."""
    global _loop
    _loop = asyncio.get_running_loop()

    accounts = channel_account_service.active_accounts_for_channel("discord")

    for account in accounts:
        _start(account["account_id"], account["company_id"], account["access_token"])

    if accounts:
        logger.info("Discord gateway: connecting %s bot(s)", len(accounts))


async def stop_all() -> None:
    for connection in list(_connections.values()):
        await connection.stop()

    _connections.clear()


async def _async_start(account_id: int, company_id: int, token: str) -> None:
    _start(account_id, company_id, token)


async def _async_stop(account_id: int) -> None:
    connection = _connections.pop(account_id, None)

    if connection:
        await connection.stop()


def start_connection(*, account_id: int, company_id: int, token: str) -> None:
    """Schedule a new connection onto the running event loop.

    Not run inline: a Gateway connection has to outlive the HTTP request that
    triggers it, and this function is called from a synchronous route
    handler that cannot itself `await`.

    A silent no-op before the app has finished starting (`_loop` unset, most
    often in a test that never ran `start_all`) rather than an error -- a
    channel account can legitimately be created before the gateway module
    has a loop to schedule onto, and failing that request over a background
    connection would be a worse trade than the connection simply starting
    the next time `start_all` runs.
    """
    if _loop is None:
        return

    asyncio.run_coroutine_threadsafe(
        _async_start(account_id, company_id, token), _loop
    )


def stop_connection(account_id: int) -> None:
    if _loop is None:
        return

    asyncio.run_coroutine_threadsafe(_async_stop(account_id), _loop)


def is_connected(account_id: int) -> bool:
    """For tests and diagnostics: whether this account currently holds an
    open connection."""
    return account_id in _connections
