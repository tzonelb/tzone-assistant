"""Signature verification for inbound platform webhooks.

Meta signs every webhook body with the app secret and sends the result in
``X-Hub-Signature-256``. Without checking it, the endpoint accepts messages from
anyone who knows the URL: forged customers, forged conversations, replies sent
out through the page token, and a metered AI call for every request.

The check is on the raw request body. Re-serialising the parsed JSON produces
different bytes and the signature will never match.

A body may be signed with one of three secrets, tried in this order:

1. the current platform app secret — one Meta app serves the whole platform, so
   this is essentially all traffic and stays the fast path,
2. the previous platform app secret, set only while a rotation drains. Meta
   signs with whichever secret was current when it queued the delivery, so
   without an overlap every rotation discards the events already in flight,
3. the receiving account's own app secret, for a customer who brings their own
   Meta app. Stored sealed under that company's key on ``channel_accounts``.

Whichever one matched is logged (never the secret itself) so an operator can
watch a rotation drain and tell when the old value is safe to clear.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple


logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "x-hub-signature-256"
SIGNATURE_PREFIX = "sha256="

# Which secret authenticated a request. Reported on the returned match and in
# the log line, so a rotation can be watched rather than guessed at.
SOURCE_CURRENT = "current"
SOURCE_PREVIOUS = "previous"
SOURCE_ACCOUNT = "account"
SOURCE_UNSIGNED = "unsigned"


class WebhookVerificationError(Exception):
    """The request could not be attributed to the configured app."""


class RoutingIds(NamedTuple):
    """The account identifiers named in a delivery, and nothing else."""

    channel: str
    page_id: str | None = None
    instagram_business_id: str | None = None
    phone_number_id: str | None = None


@dataclass(frozen=True)
class SignatureMatch:
    """Which secret verified the request, for logging and for the caller."""

    source: str
    company_id: int | None = None
    account_id: int | None = None


# Given the routing ids in a body, return that account's own app secret as
# ``{"app_secret", "company_id", "account_id"}``, or ``None``.
AccountSecretLookup = Callable[[RoutingIds], dict[str, Any] | None]


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

    The single-secret form: one secret, no rotation overlap and no per-account
    override. :func:`verify_webhook_signature` is what the endpoints call.

    ``allow_unsigned`` exists only for local development against a tunnel that
    cannot sign requests. It defaults to off and is never safe in production,
    which is why it is an explicit argument rather than a silent fallback.
    """
    verify_webhook_signature(
        raw_body=raw_body,
        signature_header=signature_header,
        app_secret=app_secret,
        allow_unsigned=allow_unsigned,
        # No account may widen a check the caller asked to make against exactly
        # one secret.
        account_secret_lookup=lambda routing: None,
    )


def _signature_matches(
    raw_body: bytes,
    signature_header: str,
    app_secret: str | None,
) -> bool:
    """Whether this secret produces the signature on the request."""
    if not app_secret:
        return False

    # Constant-time comparison: a byte-by-byte check would let an attacker
    # discover a valid signature one character at a time.
    return hmac.compare_digest(
        compute_signature(raw_body, app_secret),
        signature_header.strip(),
    )


def extract_routing_ids(raw_body: bytes) -> RoutingIds | None:
    """Read the account identifiers out of a body that is *not yet verified*.

    The module docstring says the signature is checked before the body is
    parsed, and that stays true of every path that acts on a delivery. This is
    the one exception, and it is narrow on purpose: it is reached only after
    both platform secrets have failed, and it pulls out one identifier string
    so the right per-account secret can be fetched and checked. Nothing is
    written, nothing is sent, no message is processed, and the id is discarded
    unless a signature then passes — reading a string out of untrusted JSON is
    not acting on it. Everything downstream still runs only on a verified body.

    Returns ``None`` when the body is not JSON or names no account.
    """
    try:
        payload = json.loads(raw_body)
    except (ValueError, TypeError):
        return None

    if not isinstance(payload, dict):
        return None

    entries = payload.get("entry")

    if not isinstance(entries, list):
        return None

    object_type = payload.get("object")

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        if object_type == "whatsapp_business_account":
            phone_number_id = _whatsapp_phone_number_id(entry)

            if phone_number_id:
                return RoutingIds(
                    channel="whatsapp",
                    phone_number_id=phone_number_id,
                )

            continue

        entry_id = entry.get("id")

        if not entry_id:
            continue

        if object_type == "instagram":
            return RoutingIds(
                channel="instagram",
                instagram_business_id=str(entry_id),
            )

        return RoutingIds(channel="messenger", page_id=str(entry_id))

    return None


def _whatsapp_phone_number_id(entry: dict[str, Any]) -> str | None:
    changes = entry.get("changes")

    if not isinstance(changes, list):
        return None

    for change in changes:
        if not isinstance(change, dict):
            continue

        value = change.get("value")

        if not isinstance(value, dict):
            continue

        metadata = value.get("metadata")

        if not isinstance(metadata, dict):
            continue

        phone_number_id = metadata.get("phone_number_id")

        if phone_number_id:
            return str(phone_number_id)

    return None


def _default_account_secret_lookup(routing: RoutingIds) -> dict[str, Any] | None:
    """Fetch the receiving account's own app secret, unsealed."""
    # Imported here rather than at module scope: verification is the innermost
    # thing this package does and must not pull in the database layer just to
    # compare two digests. This path is reached only when both platform
    # secrets have already failed, which is a rounding error of all traffic.
    from backend.services.channel_account_service import channel_account_service

    return channel_account_service.app_secret_for_routing_id(
        channel=routing.channel,
        page_id=routing.page_id,
        instagram_business_id=routing.instagram_business_id,
        phone_number_id=routing.phone_number_id,
    )


def verify_webhook_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
    previous_app_secret: str = "",
    allow_unsigned: bool = False,
    account_secret_lookup: AccountSecretLookup | None = None,
) -> SignatureMatch:
    """Verify a delivery against the platform secrets, then the account's own.

    Returns the :class:`SignatureMatch` describing which secret passed, and
    raises :class:`WebhookVerificationError` when none does. Like
    :func:`verify_signature` it fails closed: no configured secret and no
    explicit ``allow_unsigned`` means the request is refused.
    """
    if not app_secret and not previous_app_secret:
        if allow_unsigned:
            logger.warning(
                "Webhook accepted without a signature because no app secret is "
                "configured and unsigned webhooks are explicitly allowed. "
                "Never run this way in production."
            )
            return SignatureMatch(source=SOURCE_UNSIGNED)

        raise WebhookVerificationError(
            "No app secret is configured, so inbound webhooks cannot be "
            "verified. Set META_APP_SECRET before exposing this endpoint."
        )

    if not signature_header:
        raise WebhookVerificationError("Missing signature header.")

    if not signature_header.startswith(SIGNATURE_PREFIX):
        raise WebhookVerificationError("Malformed signature header.")

    # 1. The current platform secret: one Meta app, so nearly every request
    #    ends here after a single HMAC.
    if _signature_matches(raw_body, signature_header, app_secret):
        logger.debug("Webhook signature matched the current platform app secret.")
        return SignatureMatch(source=SOURCE_CURRENT)

    # 2. The previous platform secret, live only during a rotation overlap.
    if _signature_matches(raw_body, signature_header, previous_app_secret):
        logger.warning(
            "Webhook signature matched the %s (rotated-out) platform app "
            "secret. Deliveries queued before the rotation are still "
            "draining; keep the previous secret set until these stop.",
            SOURCE_PREVIOUS,
        )
        return SignatureMatch(source=SOURCE_PREVIOUS)

    # 3. The receiving account's own secret, for a customer on their own Meta
    #    app. Only now is the body read at all — see extract_routing_ids.
    match = _verify_against_account_secret(
        raw_body=raw_body,
        signature_header=signature_header,
        account_secret_lookup=account_secret_lookup or _default_account_secret_lookup,
    )

    if match is not None:
        return match

    raise WebhookVerificationError("Signature does not match the request body.")


def _verify_against_account_secret(
    *,
    raw_body: bytes,
    signature_header: str,
    account_secret_lookup: AccountSecretLookup,
) -> SignatureMatch | None:
    routing = extract_routing_ids(raw_body)

    if routing is None:
        return None

    try:
        candidate = account_secret_lookup(routing)
    except Exception:  # noqa: BLE001
        # A lookup failure is not an authorisation. Log it and fall through to
        # the rejection, so a database blip cannot turn into an open endpoint.
        logger.exception("Per-account app secret lookup failed; rejecting request")
        return None

    if not candidate or not candidate.get("app_secret"):
        return None

    # Only this account's secret is tried, and only for the first account named
    # in the body. A delivery that mixes accounts is signed by one app, so
    # accepting it on any other account's secret would let one company's secret
    # authenticate another company's events.
    if not _signature_matches(raw_body, signature_header, candidate["app_secret"]):
        return None

    logger.info(
        "Webhook signature matched the per-%s app secret for company=%s "
        "account=%s on channel=%s.",
        SOURCE_ACCOUNT,
        candidate.get("company_id"),
        candidate.get("account_id"),
        routing.channel,
    )

    return SignatureMatch(
        source=SOURCE_ACCOUNT,
        company_id=candidate.get("company_id"),
        account_id=candidate.get("account_id"),
    )


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


# A rejected webhook is anonymous by construction: the signature failed, so the
# body was never parsed and no company is known. It goes to the control plane
# unattributed, where an operator can see the shape of it across the platform.
#
# Bounded, because forging webhooks costs an attacker nothing and a write per
# attempt would make the audit trail the payload. One entry a minute per source
# says everything a hundred would.
REJECTION_WINDOW_SECONDS = 60

_rejections_seen: dict[tuple[str, str | None], datetime] = {}


def record_signature_rejection(
    *, source: str, ip_address: str | None, reason: str
) -> None:
    """File a refused webhook, at most once a minute per source and address.

    `Action.WEBHOOK_SIGNATURE_REJECTED` was declared when the security mirror
    was built and nothing ever raised it. A forged delivery is the one attack
    against this platform that needs no account at all, and it left no trace an
    operator could read — only a line in the process log.

    Never raises: the request is already being refused, and a log entry must not
    be able to turn a correct 403 into a 500.
    """
    now = datetime.now(timezone.utc)
    key = (source, ip_address)
    last = _rejections_seen.get(key)

    if last and (now - last).total_seconds() < REJECTION_WINDOW_SECONDS:
        return

    _rejections_seen[key] = now

    if len(_rejections_seen) > 10_000:
        _rejections_seen.clear()
        _rejections_seen[key] = now

    try:
        from backend.services.activity_service import Action, activity_service

        activity_service.record_unattributed(
            action=Action.WEBHOOK_SIGNATURE_REJECTED,
            summary=f"Refused a {source} webhook: {reason}",
            ip_address=ip_address,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record a webhook signature rejection")
