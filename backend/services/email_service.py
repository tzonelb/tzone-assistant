import logging
import smtplib
from email.mime.text import MIMEText

from config.settings import config

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str) -> bool:
    """Send a plain-text email via SMTP. Returns False (and logs a clear
    reason) instead of raising if SMTP isn't configured yet — callers
    decide how to react (e.g. block access vs. degrade gracefully)."""
    if not config.SMTP_HOST or not config.SMTP_USER or not config.SMTP_PASSWORD:
        logger.warning(
            "SMTP is not configured (SMTP_HOST/SMTP_USER/SMTP_PASSWORD missing in .env) — "
            "cannot send email to %s.", to_email,
        )
        return False

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = config.SMTP_FROM_EMAIL
    message["To"] = to_email

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as server:
            if config.SMTP_USE_TLS:
                server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.SMTP_FROM_EMAIL, [to_email], message.as_string())
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False
