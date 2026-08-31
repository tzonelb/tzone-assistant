"""Long-polling Telegram bot, for one connected account.

This script worked. It held `TELEGRAM_BOT_TOKEN` from the environment, polled
Telegram, and called the engine — and it answered customers. Then the platform
became multi-tenant, `message_gateway.handle_text` gained a required
`company_id`, and nothing updated the one caller that lives outside the request
path. Every message has raised `TypeError` since, silently, because no test
covers a standalone script and no import in `main.py` reaches it.

Two things are fixed here, and the second is the one that matters:

* **It knows whose bot it is.** The token is looked up against
  `channel_accounts` at startup, which resolves the company that connected it.
  A token no company has connected refuses to start, rather than starting and
  answering nobody.
* **It is not an IPTV bot.** It went through the engine's Telegram branch, which
  pinned every conversation into `telegram_iptv_start` and forced the department
  to `iptv` — T-ZONE's own support script, applied to whichever company ran it.
  That branch is gone; this now goes through `process_inbound_event`, the same
  path Messenger and WhatsApp use, so the reply comes from the company's own
  departments, knowledge and assistant profile.

### When to use this rather than the webhook

The webhook (`channels/telegram/webhook.py`) is how the platform serves many
companies: Telegram delivers to a URL, the bot id in the path names the account,
and one process handles all of them. This script serves exactly one bot and
needs no public URL, which makes it the right tool for local development and for
a single-company installation behind a firewall — and the wrong one for a
thousand companies, because it is one process per bot.
"""

from __future__ import annotations

import logging

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from backend.services.channel_account_service import telegram_bot_id
from channels.inbound import process_inbound_event
from config.settings import config
from database.manager import database_manager


logger = logging.getLogger(__name__)


# Resolved once at startup and read by the handlers. A module-level value rather
# than a lookup per message: the answer cannot change while this process runs,
# and a database read on every inbound message would be pure waste.
_ACCOUNT: dict[str, int] = {}


def keyboard(buttons):
    if not buttons:
        return None

    return ReplyKeyboardMarkup(
        [[button] for button in buttons],
        resize_keyboard=True,
    )


def _resolve_account(token: str) -> dict[str, int]:
    """Find the company that connected this bot.

    Refuses rather than guessing. A token nobody has connected used to be
    answered by whichever company the engine happened to resolve — which, on a
    platform with more than one, is somebody else's business replying to a
    customer who did not write to them.
    """
    bot_id = telegram_bot_id(token)

    match = database_manager.resolve_account_for_channel(
        channel="telegram", page_id=bot_id
    )

    if not match:
        raise RuntimeError(
            f"No company has connected the Telegram bot {bot_id}. Add it under "
            "Channels first — this script answers on behalf of one connected "
            "account, and will not guess which."
        )

    return {
        "company_id": int(match["company_id"]),
        "account_id": int(match["account_id"]),
    }


async def _handle(update: Update, text: str) -> None:
    """Push one message through the same path every other channel uses."""
    if not _ACCOUNT:
        logger.error("Received a message before the account was resolved")

        return

    chat = update.effective_chat
    sender = update.effective_user

    event = {
        "ignored": False,
        "channel": "telegram",
        "user_id": str(chat.id),
        "recipient_id": str(_ACCOUNT["account_id"]),
        "text": text,
        "message_id": str(update.message.message_id) if update.message else None,
        "customer_name": " ".join(
            part
            for part in (
                getattr(sender, "first_name", None),
                getattr(sender, "last_name", None),
            )
            if part
        ).strip()
        or None,
        "raw_event": {"chat_id": chat.id},
    }

    # The reply is not sent from here. `process_inbound_event` stores the
    # message and queues the assistant, and the queue sends through
    # `channels/sender.py` using this company's own bot token — the same route
    # an employee's manual reply takes. Replying inline would bypass the human
    # takeover check and talk over an employee who had taken the conversation.
    process_inbound_event(
        event=event,
        company_id=_ACCOUNT["company_id"],
        channel_account_id=_ACCOUNT["account_id"],
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle(update, "start")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        update.message.text
        if update.message and update.message.text
        else ""
    )

    if text.strip():
        await _handle(update, text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram error", exc_info=context.error)


def run_telegram():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Add it to your .env file, and "
            "connect the same bot under Channels so the platform knows which "
            "company it answers for."
        )

    _ACCOUNT.update(_resolve_account(config.TELEGRAM_BOT_TOKEN))

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info(
        "Telegram bot running for company %s (account %s)",
        _ACCOUNT["company_id"],
        _ACCOUNT["account_id"],
    )

    app.run_polling()
