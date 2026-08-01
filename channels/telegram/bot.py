import logging

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from config.settings import config
from backend.services.diagnostics_service import diagnostics_service
from channels.telegram.processor import process_telegram_message
from core.stt_service import stt_service
from core.vision_service import vision_service


logger = logging.getLogger(__name__)

ATTACHMENT_FALLBACK_TEXT = "Sorry, I couldn't understand that — could you type your message instead?"


async def _notify_attachment_failure(update: Update, company_id: int, attachment_type: str) -> None:
    """A voice note/image we couldn't transcribe or describe would
    otherwise vanish silently. Records it for monitoring and lets the
    customer know to retry as text instead of being left hanging."""
    diagnostics_service.record(
        event_type="attachment_processing_failed",
        company_id=company_id,
        channel="telegram",
        external_user_id=str(update.effective_user.id) if update.effective_user else None,
        severity="warning",
        status="failed",
        data={"attachment_type": attachment_type},
    )
    if update.message:
        await update.message.reply_text(ATTACHMENT_FALLBACK_TEXT)


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


def make_start_handler(company_id: int):
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        process_telegram_message(
            user_id=str(update.effective_user.id),
            text="start",
            customer_name=update.effective_user.full_name,
            username=update.effective_user.username,
            company_id=company_id,
        )
        await update.message.reply_text(
            "You can share your phone number to help us assist you faster (optional).",
            reply_markup=CONTACT_REQUEST_KEYBOARD,
        )
    return start


def make_message_handler(company_id: int):
    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_message = update.message.text if update.message and update.message.text else ""
        if not user_message:
            return
        process_telegram_message(
            user_id=str(update.effective_user.id),
            text=user_message,
            customer_name=update.effective_user.full_name,
            username=update.effective_user.username,
            company_id=company_id,
        )
    return handle_message


def make_voice_handler(company_id: int):
    async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
        voice = update.message.voice if update.message else None
        if not voice:
            return
        try:
            file = await context.bot.get_file(voice.file_id)
            audio_bytes = bytes(await file.download_as_bytearray())
            text = stt_service.transcribe(audio_bytes, filename="voice.ogg")
        except Exception:
            logger.exception("Telegram voice note transcription failed")
            await _notify_attachment_failure(update, company_id, "audio")
            return
        process_telegram_message(
            user_id=str(update.effective_user.id),
            text=text,
            customer_name=update.effective_user.full_name,
            username=update.effective_user.username,
            company_id=company_id,
            source_type="voice",
        )
    return handle_voice


def make_photo_handler(company_id: int):
    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        photos = update.message.photo if update.message else None
        if not photos:
            return
        try:
            file = await context.bot.get_file(photos[-1].file_id)
            image_bytes = bytes(await file.download_as_bytearray())
            description = vision_service.describe_image(image_bytes, mime_type="image/jpeg")
        except Exception:
            logger.exception("Telegram image description failed")
            await _notify_attachment_failure(update, company_id, "image")
            return
        caption = (update.message.caption or "").strip()
        text = f"[Customer sent an image — what's in it: {description}]"
        if caption:
            text += f"\nCustomer's caption: {caption}"
        process_telegram_message(
            user_id=str(update.effective_user.id),
            text=text,
            customer_name=update.effective_user.full_name,
            username=update.effective_user.username,
            company_id=company_id,
            source_type="image",
        )
    return handle_photo


def make_contact_handler(company_id: int):
    async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
        contact = update.message.contact if update.message else None
        if not contact or not contact.phone_number:
            return
        if contact.user_id and contact.user_id != update.effective_user.id:
            return
        process_telegram_message(
            user_id=str(update.effective_user.id),
            text="[shared phone number]",
            customer_name=update.effective_user.full_name,
            username=update.effective_user.username,
            phone=contact.phone_number,
            company_id=company_id,
        )
        await update.message.reply_text("Thanks — we've saved your number.")
    return handle_contact


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Telegram error", exc_info=context.error)


def build_telegram_application(bot_token: str | None = None, company_id: int | None = None) -> Application:
    """Build one bot Application bound to a specific company's token.

    bot_token/company_id default to the legacy single-tenant .env config
    (TELEGRAM_BOT_TOKEN + DEFAULT_COMPANY_ID) for backward compatibility
    with local/manual runs — the real multi-tenant path (one bot per
    connected company) always passes both explicitly. See
    channels/telegram/manager.py for that.
    """
    token = bot_token or config.TELEGRAM_BOT_TOKEN
    resolved_company_id = company_id if company_id is not None else config.DEFAULT_COMPANY_ID

    if not token:
        raise RuntimeError(
            "No Telegram bot token available. Please add TELEGRAM_BOT_TOKEN to your .env file, "
            "or connect a Telegram bot from a company's Channels page."
        )

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", make_start_handler(resolved_company_id)))
    app.add_handler(MessageHandler(filters.CONTACT, make_contact_handler(resolved_company_id)))
    app.add_handler(MessageHandler(filters.VOICE, make_voice_handler(resolved_company_id)))
    app.add_handler(MessageHandler(filters.PHOTO, make_photo_handler(resolved_company_id)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, make_message_handler(resolved_company_id)))
    app.add_error_handler(error_handler)

    return app


def run_telegram():
    """Standalone entry point (python -m channels.telegram.bot) — kept
    for manual/local testing with a single .env-configured bot."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    app = build_telegram_application()
    print("Telegram IPTV bot is running...")
    app.run_polling()


if __name__ == "__main__":
    run_telegram()
