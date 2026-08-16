"""Signature verification for inbound platform webhooks.

Meta signs every webhook body with the app secret and sends the result in
``X-Hub-Signature-256``. Without checking it, the endpoint accepts messages from
anyone who knows the URL: forged customers, forged conversations, replies sent
out through the page token, and a metered AI call for every request.

The check is on the raw request body. Re-serialising the parsed JSON produces
different bytes and the signature will never match.
"""

from __future__ import annotations

import hashlib
import hmac
import logging


logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "x-hub-signature-256"
SIGNATURE_PREFIX = "sha256="


class WebhookVerificationError(Exception):
    """The request could not be attributed to the configured app."""


def compute_signature(raw_body: bytes, app_secret: str) -> str:
    digest = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return f"{SIGNATURE_PREFIX}{digest}"


def verify_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
    allow_unsigned: bool = False,
) -> None:
    """Raise :class:`WebhookVerificationError` unless the signature is valid.

    ``allow_unsigned`` exists only for local development against a tunnel that
    cannot sign requests. It defaults to off and is never safe in production,
    which is why it is an explicit argument rather than a silent fallback.
    """
    if not app_secret:
        if allow_unsigned:
            logger.warning(
                "Webhook accepted without a signature because no app secret is "
                "configured and unsigned webhooks are explicitly allowed. "
                "Never run this way in production."
            )
            return

        raise WebhookVerificationError(
            "No app secret is configured, so inbound webhooks cannot be "
            "verified. Set META_APP_SECRET before exposing this endpoint."
        )

    if not signature_header:
        raise WebhookVerificationError("Missing signature header.")

    if not signature_header.startswith(SIGNATURE_PREFIX):
        raise WebhookVerificationError("Malformed signature header.")

    expected = compute_signature(raw_body, app_secret)

    # Constant-time comparison: a byte-by-byte check would let an attacker
    # discover a valid signature one character at a time.
    if not hmac.compare_digest(expected, signature_header.strip()):
        raise WebhookVerificationError("Signature does not match the request body.")


def verify_token_challenge(
    *,
    mode: str | None,
    token: str | None,
    expected_token: str,
) -> bool:
    """Validate the one-time subscription handshake Meta performs."""
    if not expected_token:
        return False

    if mode != "subscribe":
        return False

    return hmac.compare_digest(str(token or ""), expected_token)
