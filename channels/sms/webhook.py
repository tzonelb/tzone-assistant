"""SMS webhook, one Twilio phone number per company.

Built the same shape as `channels/viber/webhook.py`: identity in the URL is
this platform's own row id, because `register_sms_webhook` has to build that
URL before Twilio's own resource ids for the number are otherwise relevant.

### How a delivery is routed

    POST /webhook/sms/{account_id}

`account_id` is this platform's own channel-account id. The phone number
itself -- what `ROUTING_FIELD["sms"]` is enforced unique on -- is typed in by
the operator at connect time and confirmed against Twilio's own account
(`channel_account_service.twilio_phone_number_sid`), the same "typed, then
verified" shape email's mailbox address has.

### How a delivery is authenticated

Twilio signs every request with `X-Twilio-Signature`: the full request URL
(scheme, host and path, exactly as Twilio itself called it) with every POST
parameter's name and value appended in sorted order, HMAC-SHA1'd with the
account's own Auth Token and base64-encoded. Built from `config.APP_PUBLIC_URL`
rather than the URL Starlette hands back from the request itself -- a
terminating proxy in front of this platform can rewrite the scheme a request
arrives with (https becomes http once TLS is already stripped), and Twilio
signed the URL it actually called, not whatever this process happens to see.

### The body is form-encoded, not JSON

Every other webhook here parses `json.loads`. Twilio posts
`application/x-www-form-urlencoded`, the same shape an HTML form submission
takes, so this parses the raw body with `urllib.parse.parse_qsl` instead --
and that parse has to run over the exact same raw bytes the signature was
computed over, before anything is decoded into a dict.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Path, Request, Response, status

from backend.services.channel_account_service import channel_account_service
from channels.inbound import process_inbound_event
from channels.meta.logger import log_meta_event
from channels.webhook_limits import dispatch, read_capped_body
from config.settings import config
from database.manager import database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/sms", tags=["SMS"])

SIGNATURE_HEADER = "X-Twilio-Signature"

# An empty TwiML document. This platform never replies inline from the
# webhook itself -- every reply is composed after the assistant's own
# collection delay, the same reasoning `channels/line/sender.py` documents
# for never using a reply token -- so there is nothing to put inside it, but
# Twilio's own messaging webhook contract expects a TwiML response body.
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def parse_sms_event(fields: dict[str, str]) -> dict[str, Any] | None:
    """The one customer message a Twilio SMS webhook ever carries.

    `NumMedia` above zero means an MMS attachment came with it -- not
    handled yet, matching every other channel here with no attachment
    support (see `channels/sender.py`'s `MEDIA_SUPPORTED_CHANNELS`). A
    message with a picture and no caption text is dropped rather than
    answered with nothing, the same choice `channels/viber/webhook.py` makes
    for a non-text message.
    """
    from_number = str(fields.get("From") or "").strip()
    text = str(fields.get("Body") or "").strip()

    if not from_number or not text:
        return None

    return {
        "channel": "sms",
        "user_id": from_number,
        "text": text,
        "message_id": str(fields.get("MessageSid") or "") or None,
    }


def _signing_url(account_id: int) -> str:
    return f"{config.APP_PUBLIC_URL}/webhook/sms/{int(account_id)}"


def _authenticate(
    account_id: int, form_fields: list[tuple[str, str]], signature: str | None
) -> dict[str, Any]:
    """Find the account this delivery is for, and prove it is really Twilio."""
    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT id, company_id FROM channel_accounts
            WHERE id = ? AND channel = 'sms' AND status = 'active'
            LIMIT 1
            """,
            (int(account_id),),
        ).fetchone()

    if not row:
        log_meta_event("sms_event_unrouted", {"account_id": account_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unknown account."
        )

    company_id = int(row["company_id"])

    try:
        credentials = channel_account_service.credentials_for(
            company_id=company_id, channel="sms", account_id=int(row["id"])
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not read the Twilio Auth Token for company %s", company_id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook verification failed.",
        ) from None

    auth_token = (credentials or {}).get("access_token")

    if not auth_token:
        log_meta_event(
            "sms_webhook_no_token", {"company_id": company_id, "account_id": account_id}
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has no Auth Token configured.",
        )

    base = _signing_url(int(row["id"]))

    for key, value in sorted(form_fields):
        base += key + value

    expected = base64.b64encode(
        hmac.new(auth_token.encode("utf-8"), base.encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")

    if not signature or not hmac.compare_digest(expected, signature):
        log_meta_event(
            "sms_webhook_rejected", {"company_id": company_id, "account_id": account_id}
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature."
        )

    return {"company_id": company_id, "account_id": int(row["id"])}


@router.post("/{account_id}")
async def receive_event(
    request: Request,
    account_id: int = Path(ge=1),
):
    raw_body = await read_capped_body(request, source="sms")

    # Decoded once, from the exact bytes the signature is computed over --
    # decoding twice (once for the signature, once for the fields) risks the
    # two disagreeing on a byte this parser and Twilio's own encoder treat
    # differently.
    form_fields = parse_qsl(raw_body.decode("utf-8", errors="replace"), keep_blank_values=True)

    account = _authenticate(
        account_id, form_fields, request.headers.get(SIGNATURE_HEADER)
    )

    fields = dict(form_fields)
    event = parse_sms_event(fields)

    if not event:
        return Response(content=_EMPTY_TWIML, media_type="text/xml")

    dispatch(
        _process_events,
        [
            {
                **event,
                "_company_id": account["company_id"],
                "_account_id": account["account_id"],
            }
        ],
        source="sms",
    )

    return Response(content=_EMPTY_TWIML, media_type="text/xml")


def _process_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for event in events:
        company_id = event.pop("_company_id", None)
        account_id = event.pop("_account_id", None)

        if company_id is None:
            results.append({"status": "ignored", "reason": "unknown_account"})
            continue

        try:
            results.append(
                process_inbound_event(
                    event=event,
                    company_id=company_id,
                    channel_account_id=account_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to process SMS event")
            log_meta_event(
                "sms_event_failed",
                {"company_id": company_id, "error": type(exc).__name__},
            )
            results.append({"status": "error", "reason": "processing_failed"})

    return results
