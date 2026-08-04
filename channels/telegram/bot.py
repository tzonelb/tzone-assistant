import logging

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from config.settings import config
from gateway.message_gateway import message_gateway


logger = logging.getLogger(__name__)


def keyboard(buttons):
    if not buttons:
        return None

    return ReplyKeyboardMarkup(
        [[button] for button in buttons],
        resize_keyboard=True
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = message_gateway.handle_text(
        channel="telegram",
        user_id=update.effective_user.id,
        message="start"
    )

    # engine.handle() returns None when the channel is disabled via the
    # ops-level automation_policy kill switch -- stay silent rather than
    # crash on response.text.
    if response is None:
        return

    await update.message.reply_text(
        response.text,
        reply_markup=keyboard(response.buttons)
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text if update.message and update.message.text else ""

    response = message_gateway.handle_text(
        channel="telegram",
        user_id=update.effective_user.id,
        message=user_message
    )

    if response is None:
        return

    await update.message.reply_text(
        response.text,
        reply_markup=keyboard(response.buttons)
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram error", exc_info=context.error)


def run_telegram():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing. Please add it to your .env file."
        )

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("Telegram IPTV bot is running...")
    app.run_polling()