"""Google Chat webhook, one Chat app (service account) per company.

Built the same shape as `channels/sms/webhook.py`: identity in the URL is
this platform's own row id, and inbound authentication is unrelated to the
row's own account-derived identity. But Google Chat has no self-service
registration call the way Viber's `set_webhook`, LINE's `PUT .../webhook/
endpoint` or Twilio's `IncomingPhoneNumbers` update are: a Chat app's HTTP
endpoint is a setting on its Google Cloud project, changed on the Chat API's
own Configuration page in Cloud Console -- there is no public REST endpoint
for it. The operator is shown this URL after connecting and pastes it there
themselves, the same one manual step Slack's own Event Subscriptions Request
URL already asks for.

### How a delivery is authenticated

Every request from Google Chat carries `Authorization: Bearer <token>`, an
OIDC id token Google itself signs (not the company's own credential -- that
authenticates *outbound* sends, not inbound deliveries). Verified against:

* signature, against Google's own public keys at
  `https://www.googleapis.com/oauth2/v3/certs`
* `aud` (audience), against this exact account's own webhook URL -- built
  from `config.APP_PUBLIC_URL`, not trusted from the incoming request, the
  same reasoning `channels/sms/webhook.py` documents for Twilio
* `iss` (issuer), one of Google's own documented values
* `email`, the fixed address `chat@system.gserviceaccount.com` Chat's own
  outbound calls always sign as -- this is what actually proves the caller
  is Google Chat, since a request signed by any other Google-issued token
  would still pass the two checks above

This is a fundamentally different scheme from every synced channel before
it: the JWT authenticates the *caller*, not the request body, so it does not
depend on the body at all -- there is nothing here shaped like a raw-body
HMAC (Slack, Viber, LINE, Twilio) to compute.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import jwt
from fastapi import APIRouter, HTTPException, Path, Request, status

from backend.services.channel_account_service import channel_account_service
from channels.inbound import process_inbound_event
from channels.meta.logger import log_meta_event
from channels.webhook_limits import dispatch, read_capped_body
from config.settings import config
from database.manager import database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/google_chat", tags=["Google Chat"])

# The one identity every genuine Google Chat delivery signs as. Documented at
# https://developers.google.com/workspace/chat/verify-requests-from-chat --
# checking it is what actually distinguishes "signed by Google" (true of any
# Google-issued OIDC token) from "signed by Google *Chat*".
CHAT_SERVICE_ACCOUNT_EMAIL = "chat@system.gserviceaccount.com"
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ACCEPTED_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

# Cached across requests: fetching Google's public keys on every delivery
# would mean a Google outage or a slow response there stalls every inbound
# message. `lifespan` re-fetches periodically rather than once forever, so a
# key Google rotates in is picked up within the hour rather than requiring a
# restart.
_jwk_client = jwt.PyJWKClient(GOOGLE_CERTS_URL, cache_keys=True, lifespan=3600)


def parse_google_chat_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """The one customer message a Chat interaction event carries.

    Every other event `type` here -- `ADDED_TO_SPACE`, `REMOVED_FROM_SPACE`,
    `CARD_CLICKED` and the rest -- is not a message and is ignored, the same
    "only a real message reaches the assistant" rule every other channel's
    parser applies to its own non-text events.
    """
    if str(event.get("type") or "") != "MESSAGE":
        return None

    message = event.get("message") or {}
    text = str(message.get("text") or "").strip()
    space_name = str((event.get("space") or {}).get("name") or "").strip()

    if not text or not space_name:
        return None

    return {
        "channel": "google_chat",
        "user_id": space_name,
        "text": text,
        "message_id": message.get("name") or None,
    }


def _signing_url(account_id: int) -> str:
    return f"{config.APP_PUBLIC_URL}/webhook/google_chat/{int(account_id)}"


def _authenticate(account_id: int, authorization: str | None) -> dict[str, Any]:
    """Find the account this delivery is for, and prove it is really Google Chat."""
    with database_manager.control() as conn:
        row = conn.execute(
            """
            SELECT id, company_id FROM channel_accounts
            WHERE id = ? AND channel = 'google_chat' AND status = 'active'
            LIMIT 1
            """,
            (int(account_id),),
        ).fetchone()

    if not row:
        log_meta_event("google_chat_event_unrouted", {"account_id": account_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unknown account."
        )

    company_id = int(row["company_id"])

    if not authorization or not authorization.startswith("Bearer "):
        log_meta_event(
            "google_chat_webhook_rejected",
            {"company_id": company_id, "account_id": account_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Missing bearer token."
        )

    token = authorization[len("Bearer ") :]

    # Every failure here -- a bad signature, an expired token, the wrong
    # audience, a network error reaching Google's own keys -- collapses to
    # the same refusal. What went wrong is for the log, not the caller.
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_signing_url(int(row["id"])),
        )
    except Exception:  # noqa: BLE001
        log_meta_event(
            "google_chat_webhook_rejected",
            {"company_id": company_id, "account_id": account_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature."
        ) from None

    if (
        claims.get("iss") not in ACCEPTED_ISSUERS
        or claims.get("email") != CHAT_SERVICE_ACCOUNT_EMAIL
    ):
        log_meta_event(
            "google_chat_webhook_rejected",
            {"company_id": company_id, "account_id": account_id},
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
    raw_body = await read_capped_body(request, source="google_chat")
    account = _authenticate(account_id, request.headers.get("authorization"))

    try:
        payload = json.loads(raw_body.decode("utf-8", errors="replace"))
    except ValueError:
        payload = None

    event = parse_google_chat_event(payload) if isinstance(payload, dict) else None

    # An empty JSON object, not a TwiML-style placeholder: Chat apps may
    # optionally reply synchronously in this response body, and this
    # platform deliberately never does -- every reply here is composed after
    # the assistant's own collection delay, the same "no reply token to use"
    # reasoning `channels/line/sender.py` documents -- so an empty object is
    # a genuine "no immediate reply" rather than a placeholder standing in
    # for one.
    if not event:
        return {}

    dispatch(
        _process_events,
        [
            {
                **event,
                "_company_id": account["company_id"],
                "_account_id": account["account_id"],
            }
        ],
        source="google_chat",
    )

    return {}


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
            logger.exception("Failed to process Google Chat event")
            log_meta_event(
                "google_chat_event_failed",
                {"company_id": company_id, "error": type(exc).__name__},
            )
            results.append({"status": "error", "reason": "processing_failed"})

    return results
