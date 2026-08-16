"""Outbound email.

The platform has one thing it genuinely needs to say to a person who cannot
sign in: here is a link to set a new password. Everything else it tells people
happens inside the application, where a session already exists. So this module
is deliberately small — one message shape, three backends, no templating engine
and no queue.

WHY IT REPORTS FAILURE INSTEAD OF SWALLOWING IT
-----------------------------------------------
A mailer that logs an exception and returns quietly is worse than no mailer at
all in this particular flow. The administrator clicks "send reset link", sees a
success message, tells the employee to check their mail, and the employee waits
for something that was never sent. Both of them now believe the account is
recoverable and it is not.

So `send` returns a :class:`DeliveryResult` and never raises into the caller's
happy path. The caller decides what a failure means — and for the password
reset endpoint it means a 503 that names the problem.

No new dependency: `smtplib` and `ssl` are standard library. The seam for an
HTTP provider (Resend, SendGrid) and later for SMS is `_BACKENDS` — adding one
is a function and a config value, not a rewrite.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable

from config.settings import config


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    """What happened, in a form the caller can act on.

    `delivered` False with `reason` set is an ordinary outcome here, not an
    exception — misconfiguration is the most likely failure and it deserves a
    message an operator can act on rather than a traceback.
    """

    delivered: bool
    backend: str
    reason: str = ""

    def __bool__(self) -> bool:
        return self.delivered


class MailerNotConfigured(RuntimeError):
    """Raised only by :func:`assert_configured`, never by :func:`send`."""


def _missing_smtp_settings() -> list[str]:
    required = {
        "SMTP_HOST": config.SMTP_HOST,
        "SMTP_FROM": config.SMTP_FROM,
    }
    return sorted(name for name, value in required.items() if not str(value).strip())


def _build_message(*, to: str, subject: str, body: str, sender: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return message


def _send_smtp(*, to: str, subject: str, body: str) -> DeliveryResult:
    missing = _missing_smtp_settings()

    if missing:
        return DeliveryResult(
            delivered=False,
            backend="smtp",
            reason=f"Email is not configured: {', '.join(missing)} is empty.",
        )

    message = _build_message(
        to=to, subject=subject, body=body, sender=config.SMTP_FROM
    )

    try:
        with smtplib.SMTP(
            config.SMTP_HOST,
            config.SMTP_PORT,
            timeout=config.SMTP_TIMEOUT_SECONDS,
        ) as smtp:
            if config.SMTP_STARTTLS:
                smtp.starttls(context=ssl.create_default_context())

            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)

            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        # The subject is safe to log; the body carries a reset link, so it is
        # not. Same reasoning as company_settings_service logging keys only.
        logger.exception("Could not send %r to %s", subject, to)
        return DeliveryResult(
            delivered=False,
            backend="smtp",
            reason=f"The mail server refused the message: {exc}",
        )

    logger.info("Sent %r to %s", subject, to)
    return DeliveryResult(delivered=True, backend="smtp")


def _send_console(*, to: str, subject: str, body: str) -> DeliveryResult:
    """Development and tests. Prints the message rather than sending it.

    The body is logged in full here on purpose — this backend exists so a
    developer can copy the reset link out of the log — which is exactly why it
    must never be the backend in production.
    """
    logger.warning(
        "EMAIL_BACKEND=console, so this message was printed and NOT sent.\n"
        "  To      : %s\n"
        "  Subject : %s\n"
        "  Body    :\n%s",
        to,
        subject,
        body,
    )
    return DeliveryResult(delivered=True, backend="console")


def _send_disabled(*, to: str, subject: str, body: str) -> DeliveryResult:
    return DeliveryResult(
        delivered=False,
        backend="disabled",
        reason="Email delivery is switched off (EMAIL_BACKEND=disabled).",
    )


# Add a backend by adding a function and a key. An HTTP provider (Resend,
# SendGrid) fits the same signature and would use the `httpx` already in
# requirements; an SMS backend would be a sibling module with the same shape.
_BACKENDS: dict[str, Callable[..., DeliveryResult]] = {
    "smtp": _send_smtp,
    "console": _send_console,
    "disabled": _send_disabled,
}


def configured_backend() -> str:
    """The backend name, falling back to `disabled` for an unknown value.

    An unrecognised `EMAIL_BACKEND` refuses rather than guessing. Guessing
    `smtp` would try to reach a server that may not exist; guessing `console`
    would silently stop sending real mail in production.
    """
    name = (config.EMAIL_BACKEND or "").strip().lower()

    if name not in _BACKENDS:
        logger.error(
            "EMAIL_BACKEND=%r is not one of %s; refusing to send.",
            config.EMAIL_BACKEND,
            ", ".join(sorted(_BACKENDS)),
        )
        return "disabled"

    return name


def is_configured() -> bool:
    """Whether a send would have any chance of arriving."""
    backend = configured_backend()

    if backend == "disabled":
        return False

    if backend == "smtp":
        return not _missing_smtp_settings()

    return True


def assert_configured() -> None:
    """Raise if email cannot be delivered, with a message worth showing.

    Used by endpoints that must not report success for a mail that will never
    be sent.
    """
    if is_configured():
        return

    backend = configured_backend()

    if backend == "disabled":
        raise MailerNotConfigured(
            "Email delivery is switched off, so no reset link can be sent. "
            "Set EMAIL_BACKEND and the SMTP settings, or reset the password "
            "from the server with: "
            "python -m tools.manage_platform reset-password --email <address>"
        )

    raise MailerNotConfigured(
        "Email is not configured: "
        f"{', '.join(_missing_smtp_settings())} is empty. "
        "Set it, or reset the password from the server with: "
        "python -m tools.manage_platform reset-password --email <address>"
    )


def send(*, to: str, subject: str, body: str) -> DeliveryResult:
    """Deliver one plain-text message. Never raises."""
    backend = configured_backend()
    return _BACKENDS[backend](to=to, subject=subject, body=body)
