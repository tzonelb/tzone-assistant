"""The Dialer: placing, transferring and ending live calls.

Two routers, because two very different callers reach this file.

`router` is the screen's. Every endpoint on it resolves the company from the
session and never from the request. Reading the dialer's state and its recent
calls asks only for a signed-in member of the company — the same shape as the
notification bell, and the same reasoning: it answers about the caller's own
company and takes nothing from a parameter. Doing anything that makes a phone
ring asks for `dialer.use`, which is a permission an owner grants deliberately,
because it spends the company's money and puts its number in front of a
customer.

`webhooks_router` is the telephony provider's, and it carries no session
because the provider has none. It is registered in `main.py` outside the module
gate for that reason: a gate that resolves a company from a session would reject
every callback about a call the company itself placed. What stands in for the
session is Twilio's request signature, verified on every request before a single
field of the body is read. With no auth token configured there is nothing to
verify against, so everything is rejected.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qsl

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status as http_status,
)
from fastapi.responses import Response

from backend.api.schemas.calls import PlaceCallRequest, TransferCallRequest
from backend.services.auth_service import (
    auth_service,
    get_current_user,
    require_permission,
)
from backend.services.telephony_service import (
    CallNotFound,
    TelephonyError,
    TelephonyNotConfiguredError,
    telephony_service,
    verify_twilio_signature,
)
from config.settings import config


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dialer", tags=["Dialer"])
webhooks_router = APIRouter(prefix="/api/dialer/webhooks", tags=["Dialer"])

# A provider callback body is small: a few dozen short fields. Anything larger
# is not a callback, and reading it into memory before rejecting it would be the
# whole point of a size limit missed.
MAX_WEBHOOK_BODY = 64 * 1024


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def read_context(current_user=Depends(get_current_user)):
    return _context(current_user)


def call_context(current_user=Depends(require_permission("dialer.use"))):
    return _context(current_user)


def _refusal(exc: Exception) -> HTTPException:
    """One place that turns a telephony failure into a status code.

    503 for "this platform has no phone line", because that is a deployment
    that is not finished rather than a request that is wrong, and the screen
    shows its setup notice on it. 422 for a call the provider or this company's
    own data refused — an unverified number, a colleague with no phone on their
    profile — which is a request that cannot be satisfied as sent.
    """
    if isinstance(exc, TelephonyNotConfiguredError):
        return HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )

    # The literal rather than the constant, for the reason `api/errors.py`
    # gives: this Starlette renamed `HTTP_422_UNPROCESSABLE_ENTITY` to
    # `..._CONTENT` and deprecated the old name, so either spelling ties this
    # file to one version's vocabulary for a number RFC 4918 fixed.
    return HTTPException(status_code=422, detail=str(exc))


# ----------------------------------------------------------------------
# The screen
# ----------------------------------------------------------------------


@router.get("/status")
def dialer_status(context=Depends(read_context)):
    return telephony_service.dialer_status()


@router.get("/calls")
def list_calls(
    active_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(read_context),
):
    _current_user, company_id = context

    return telephony_service.list_calls(
        company_id=company_id,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )


@router.post("/calls", status_code=http_status.HTTP_201_CREATED)
def place_call(payload: PlaceCallRequest, context=Depends(call_context)):
    current_user, company_id = context

    try:
        return telephony_service.place_call(
            company_id=company_id,
            to_number=payload.to_number,
            customer_id=payload.customer_id,
            actor_user_id=current_user.get("id"),
        )
    except (TelephonyNotConfiguredError, TelephonyError) as exc:
        raise _refusal(exc) from exc


@router.post("/calls/{call_id}/transfer")
def transfer_call(
    call_id: int, payload: TransferCallRequest, context=Depends(call_context)
):
    _current_user, company_id = context

    try:
        return telephony_service.transfer_call(
            company_id=company_id,
            call_id=call_id,
            employee_user_id=payload.employee_user_id,
        )
    except CallNotFound as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (TelephonyNotConfiguredError, TelephonyError) as exc:
        raise _refusal(exc) from exc


@router.post("/calls/{call_id}/hangup")
def hangup_call(call_id: int, context=Depends(call_context)):
    _current_user, company_id = context

    try:
        return telephony_service.hangup_call(
            company_id=company_id, call_id=call_id
        )
    except CallNotFound as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (TelephonyNotConfiguredError, TelephonyError) as exc:
        raise _refusal(exc) from exc


# ----------------------------------------------------------------------
# The provider
# ----------------------------------------------------------------------


async def _verified_form(request: Request) -> dict[str, str]:
    """Read a provider callback, and only if it proves it is one.

    The body is parsed with `urllib` rather than `request.form()` so the
    platform needs no multipart dependency for four endpoints that only ever
    receive `application/x-www-form-urlencoded`.

    The URL the signature is checked against is rebuilt from
    `PUBLIC_BASE_URL` when it is set, because that is the address the provider
    was configured with and therefore the one it signed — a reverse proxy that
    rewrites scheme or host would otherwise make every genuine callback fail
    verification.
    """
    body = await request.body()

    if len(body) > MAX_WEBHOOK_BODY:
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Callback body too large.",
        )

    params = dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))

    url = str(request.url)

    if config.PUBLIC_BASE_URL:
        url = config.PUBLIC_BASE_URL.rstrip("/") + request.url.path

    if not verify_twilio_signature(
        url=url,
        params=params,
        signature=request.headers.get("X-Twilio-Signature"),
        auth_token=config.TWILIO_AUTH_TOKEN,
    ):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature.",
        )

    return params


@webhooks_router.post("/voice")
async def voice_webhook(request: Request):
    """What the provider plays when an outbound call is answered."""
    await _verified_form(request)

    return Response(
        content=telephony_service.build_outbound_twiml(),
        media_type="application/xml",
    )


@webhooks_router.post("/inbound")
async def inbound_webhook(request: Request):
    """Somebody called the company's number and the platform answered."""
    params = await _verified_form(request)
    telephony_service.record_inbound_call(params)

    return Response(
        content=telephony_service.build_inbound_twiml(),
        media_type="application/xml",
    )


@webhooks_router.post("/status")
async def status_webhook(request: Request):
    params = await _verified_form(request)
    telephony_service.handle_status_callback(params)

    return {"ok": True}


@webhooks_router.post("/recording")
async def recording_webhook(request: Request):
    params = await _verified_form(request)
    telephony_service.handle_recording_callback(params)

    return {"ok": True}
