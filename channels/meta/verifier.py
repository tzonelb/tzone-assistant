"""HMAC-SHA256 signature verification for Meta (Messenger/Instagram) and
WhatsApp Cloud API webhooks.

Meta signs every webhook POST body with the app secret and sends the
resulting digest in the ``X-Hub-Signature-256`` header as
``sha256=<hex digest>``. This module recomputes that digest from the raw
request body and compares it against the header in constant time so that
only requests actually originating from Meta are processed.

WhatsApp Business Cloud API webhooks use the identical scheme (same
header, same HMAC-SHA256-over-raw-body construction) because in a typical
setup WhatsApp Cloud API is configured under the same Meta developer app
as Messenger/Instagram, so the same app secret verifies both.
"""

import hashlib
import hmac

SIGNATURE_PREFIX = "sha256="


def verify_meta_signature(
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
) -> bool:
    """Return True only if signature_header is a valid HMAC-SHA256 of
    raw_body computed with app_secret, using Meta's "sha256=<hex>" format.

    Returns False on any missing/malformed header, missing/empty secret,
    or a mismatched digest. Uses hmac.compare_digest for constant-time
    comparison to avoid leaking timing information about the secret.
    """
    if not signature_header:
        return False

    if not app_secret:
        return False

    if not signature_header.startswith(SIGNATURE_PREFIX):
        return False

    provided_digest = signature_header[len(SIGNATURE_PREFIX):]

    if not provided_digest:
        return False

    expected_digest = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_digest, provided_digest)
