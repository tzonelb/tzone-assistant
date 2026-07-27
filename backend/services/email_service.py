import logging
import smtplib
from email.mime.text import MIMEText

from config.settings import config

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """Send a plain-text email via SMTP.

    Returns (True, "") on success, or (False, reason) on failure —
    callers decide how to react (e.g. block access vs. degrade
    gracefully) and can show the reason instead of a generic message.
    """
    if not config.SMTP_HOST or not config.SMTP_USER or not config.SMTP_PASSWORD:
        reason = (
            "SMTP is not configured (SMTP_HOST/SMTP_USER/SMTP_PASSWORD missing in .env)."
        )
        logger.warning("%s Cannot send email to %s.", reason, to_email)
        return False, reason

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = config.SMTP_FROM_EMAIL
    message["To"] = to_email

    # Port 465 is implicit SSL from the first byte (needs SMTP_SSL) —
    # trying STARTTLS on it hangs until timeout, which is exactly the
    # "Connection unexpectedly closed: timed out" error this fixes.
    # Port 587 (or anything else) uses plain SMTP + STARTTLS upgrade.
    use_implicit_ssl = config.SMTP_PORT == 465

    try:
        if use_implicit_ssl:
            with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as server:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.sendmail(config.SMTP_FROM_EMAIL, [to_email], message.as_string())
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as server:
                if config.SMTP_USE_TLS:
                    server.starttls()
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.sendmail(config.SMTP_FROM_EMAIL, [to_email], message.as_string())
        return True, ""
    except smtplib.SMTPAuthenticationError:
        reason = "SMTP login was rejected — check SMTP_USER/SMTP_PASSWORD (Gmail needs an App Password, not your normal password)."
        logger.exception("Failed to send email to %s", to_email)
        return False, reason
    except (TimeoutError, smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, OSError) as exc:
        reason = (
            f"Could not connect to {config.SMTP_HOST}:{config.SMTP_PORT} — "
            f"double-check SMTP_HOST/SMTP_PORT, and that this network/firewall allows outbound SMTP. ({exc})"
        )
        logger.exception("Failed to send email to %s", to_email)
        return False, reason
    except Exception as exc:
        reason = f"Email send failed: {exc}"
        logger.exception("Failed to send email to %s", to_email)
        return False, reason

