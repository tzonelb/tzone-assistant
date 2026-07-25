import asyncio
import logging

from channels.telegram.bot import build_telegram_application

logger = logging.getLogger(__name__)

# account_id -> {"app": Application, "task": asyncio.Task}
_running_bots: dict[int, dict] = {}


async def _run_until_cancelled(app) -> None:
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def start_bot(*, account_id: int, company_id: int, bot_token: str) -> None:
    """Start polling for one company's Telegram bot. Safe to call for an
    already-running account_id (it's a no-op) — used both at app startup
    (for every previously-connected bot) and immediately when a company
    connects a new one, so it's live within seconds, not after a restart.
    """
    if account_id in _running_bots:
        return

    try:
        app = build_telegram_application(bot_token=bot_token, company_id=company_id)
    except Exception:
        logger.exception("Failed to build Telegram application for account %s", account_id)
        return

    task = asyncio.create_task(_run_until_cancelled(app))
    _running_bots[account_id] = {"app": app, "task": task}
    logger.info("Started Telegram bot for company %s (account %s)", company_id, account_id)


async def stop_bot(*, account_id: int) -> None:
    entry = _running_bots.pop(account_id, None)
    if not entry:
        return
    entry["task"].cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await entry["task"]
    logger.info("Stopped Telegram bot for account %s", account_id)


async def stop_all() -> None:
    for account_id in list(_running_bots.keys()):
        await stop_bot(account_id=account_id)


def start_all_connected_bots() -> None:
    """Called once at app startup — starts a polling task for every
    company that already has a connected, active Telegram bot."""
    from backend.services.channel_account_service import channel_account_service

    for account in channel_account_service.list_active_telegram_accounts():
        try:
            token = channel_account_service.get_decrypted_token(account_id=account["id"])
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping Telegram account %s: %s", account["id"], exc)
            continue
        start_bot(account_id=account["id"], company_id=account["company_id"], bot_token=token)
