import logging

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from config.settings import config
from channels.telegram.processor import process_telegram_message


logger = logging.getLogger(__name__)


def keyboard(buttons):
    if not buttons:
        return None

    return ReplyKeyboardMarkup(
        [[button] for button in buttons],
        resize_keyboard=True
    )


CONTACT_REQUEST_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Share my phone number", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    process_telegram_message(
        user_id=str(update.effective_user.id),
        text="start",
        customer_name=update.effective_user.full_name,
        username=update.effective_user.username,
    )
    # Ask for the customer's phone once, right at the start of support.
    # Telegram never exposes a phone number unless the user explicitly
    # shares it via this contact button — there is no other way to get it.
    await update.message.reply_text(
        "You can share your phone number to help us assist you faster (optional).",
        reply_markup=CONTACT_REQUEST_KEYBOARD,
    )
    # No AI reply sent here directly — the same batched pipeline
    # Messenger uses (schedule_smart_reply) answers a few seconds later.


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text if update.message and update.message.text else ""
    if not user_message:
        return

    process_telegram_message(
        user_id=str(update.effective_user.id),
        text=user_message,
        customer_name=update.effective_user.full_name,
        username=update.effective_user.username,
    )


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact if update.message else None
    if not contact or not contact.phone_number:
        return

    # Only accept a contact the user shared for themselves, not a
    # forwarded contact card for someone else.
    if contact.user_id and contact.user_id != update.effective_user.id:
        return

    process_telegram_message(
        user_id=str(update.effective_user.id),
        text="[shared phone number]",
        customer_name=update.effective_user.full_name,
        username=update.effective_user.username,
        phone=contact.phone_number,
    )
    await update.message.reply_text(
        "Thanks — we've saved your number.",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram error", exc_info=context.error)


def build_telegram_application() -> Application:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Please add it to your .env file."
        )

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    return app


def run_telegram():
    """Standalone entry point (python -m channels.telegram.bot) — kept
    for manual/local testing. The app's normal startup runs the bot
    inside the same asyncio loop instead; see main.py's telegram_bot_worker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    app = build_telegram_application()
    print("Telegram IPTV bot is running...")
    app.run_polling()


if __name__ == "__main__":
    run_telegram()