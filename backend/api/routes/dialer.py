from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.telephony_service import (
    TelephonyError,
    TelephonyNotConfiguredError,
    telephony_service,
    verify_twilio_signature,
)
from config.settings import config


router = APIRouter(prefix="/api/dialer", tags=["Dialer"])


class PlaceCallRequest(BaseModel):
    to_number: str = Field(..., min_length=3, max_length=40)
    customer_id: int | None = None


class TransferRequest(BaseModel):
    employee_user_id: int


def current_context(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


# RBAC notes: viewing dialer state/history needs only company
# membership (matching this branch's calls.py convention); actually
# placing/transferring/hanging up calls requires the dedicated
# "dialer.use" permission (seeded in database.py, auto-granted to
# owner/admin like every other code, assignable from Roles & Permissions).


@router.get("/status")
def dialer_status(context=Depends(current_context)):
    current_user, company_id = context
    return telephony_service.dialer_status()


@router.get("/calls")
def list_calls(
    active_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    current_user, company_id = context
    return telephony_service.list_calls(
        company_id=company_id,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )


@router.post("/calls", status_code=status.HTTP_201_CREATED)
def place_call(payload: PlaceCallRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "dialer.use")

    try:
        return telephony_service.place_call(
            company_id=company_id,
            to_number=payload.to_number,
            customer_id=payload.customer_id,
            actor_user_id=current_user.get("id"),
        )
    except TelephonyNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TelephonyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/calls/{call_id}/transfer")
def transfer_call(
    call_id: int,
    payload: TransferRequest,
    context=Depends(current_context),
):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "dialer.use")

    try:
        return telephony_service.transfer_call(
            company_id=company_id,
            call_id=call_id,
            employee_user_id=payload.employee_user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TelephonyNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TelephonyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/calls/{call_id}/hangup")
def hangup_call(call_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "dialer.use")

    try:
        return telephony_service.hangup_call(
            company_id=company_id, call_id=call_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TelephonyNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TelephonyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------
# Twilio webhooks. No session auth (Twilio calls these), but every
# request is verified with the X-Twilio-Signature scheme; unverifiable
# requests are rejected. When telephony isn't configured these endpoints
# reject everything (no auth token to verify against).
# ---------------------------------------------------------------------


async def _verified_form(request: Request) -> dict[str, str]:
    # Twilio posts application/x-www-form-urlencoded. Parsed manually
    # from the raw body (urllib) rather than request.form() so no
    # python-multipart dependency is needed.
    from urllib.parse import parse_qsl

    body = await request.body()
    params = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
    url = str(request.url)
    if config.PUBLIC_BASE_URL:
        # Reconstruct the public URL Twilio actually signed (the app may
        # sit behind a proxy that rewrites scheme/host).
        url = config.PUBLIC_BASE_URL.rstrip("/") + request.url.path
    signature = request.headers.get("X-Twilio-Signature")
    if not verify_twilio_signature(
        url=url,
        params=params,
        signature=signature,
        auth_token=config.TWILIO_AUTH_TOKEN,
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook signature.")
    return params


@router.post("/webhooks/voice")
async def voice_webhook(request: Request):
    await _verified_form(request)
    return Response(
        content=telephony_service.build_outbound_twiml(),
        media_type="application/xml",
    )


@router.post("/webhooks/inbound")
async def inbound_webhook(request: Request):
    params = await _verified_form(request)
    telephony_service.record_inbound_call(params)
    return Response(
        content=telephony_service.build_inbound_twiml(),
        media_type="application/xml",
    )


@router.post("/webhooks/status")
async def status_webhook(request: Request):
    params = await _verified_form(request)
    telephony_service.handle_status_callback(params)
    return {"ok": True}


@router.post("/webhooks/recording")
async def recording_webhook(request: Request):
    params = await _verified_form(request)
    telephony_service.handle_recording_callback(params)
    return {"ok": True}
