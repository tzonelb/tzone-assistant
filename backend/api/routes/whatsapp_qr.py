"""Connecting a WhatsApp number by scanning a QR code, the way WhatsApp Web
itself works -- rather than a Meta developer app and the Cloud API. The
official "whatsapp" channel already built in this file's sibling,
`channels.py`, is the Cloud API version of this same platform; this is the
unofficial one, kept live deliberately: see this module's own place in the
catalogue for why it stays offered even though it carries real risk to the
connected number.

### Why this is a poll, not a single call

Every other unofficial channel here (`instagram_direct.py`,
`facebook_direct.py`) resolves within one or two ordinary request/response
round trips. A QR scan cannot: the phone has to actually be picked up and
pointed at the screen, which is real human time no HTTP request should
block a thread on. So the flow is three calls instead:

    POST /api/whatsapp-qr/connect/start           -- begin, get a pending_id
    GET  /api/whatsapp-qr/connect/status/{id}      -- poll: QR image, then
                                                       connected/failed/expired
    POST /api/whatsapp-qr/connect/cancel/{id}      -- give up early

`channels/whatsapp_qr/browser.py`'s `start_connect` hands the live browser
session to a background thread that owns it until the scan succeeds, fails,
or times out; this router only ever reads that thread's own status under
its lock, and finishes the account the moment it sees `connected`.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.routes.channels import require_elevated
from backend.services.auth_service import auth_service
from backend.services.channel_account_service import (
    ChannelAccountError,
    channel_account_service,
)
from channels.whatsapp_qr.browser import PendingConnection, start_connect


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/whatsapp-qr", tags=["WhatsApp (QR scan)"])

# How long a pending connection is kept in this table after the background
# thread that owns it has stopped touching it -- long enough for a slow
# frontend poll to still see the final status, short enough that a tab
# closed mid-scan does not leak forever. The thread's own
# `CONNECT_TIMEOUT_SECONDS` already bounds how long the *browser* stays
# open; this only bounds how long its *result* is still readable here.
PENDING_RESULT_TTL_SECONDS = 300

_pending: dict[str, dict[str, Any]] = {}


def _prune() -> None:
    now = time.monotonic()
    expired = [key for key, entry in _pending.items() if entry["expires_at"] < now]

    for key in expired:
        connection: PendingConnection = _pending[key]["connection"]
        connection.cancel()
        _pending.pop(key, None)


class WhatsAppQrConnectStart(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    branch_id: int | None = None
    department_id: int | None = None


@router.post("/connect/start")
def start(
    payload: WhatsAppQrConnectStart,
    current_user: dict[str, Any] = Depends(require_elevated),
):
    _prune()

    company_id = auth_service.resolve_company_id(current_user)

    pending_id = secrets.token_urlsafe(24)
    connection = start_connect()

    _pending[pending_id] = {
        "connection": connection,
        "company_id": company_id,
        "expires_at": time.monotonic() + PENDING_RESULT_TTL_SECONDS,
        "values_extra": {
            "name": payload.name.strip(),
            "branch_id": payload.branch_id,
            "department_id": payload.department_id,
        },
    }

    return {"pending_id": pending_id}


@router.get("/connect/status/{pending_id}")
def poll_status(
    pending_id: str,
    current_user: dict[str, Any] = Depends(require_elevated),
):
    _prune()

    company_id = auth_service.resolve_company_id(current_user)
    entry = _pending.get(pending_id)

    if not entry or entry["company_id"] != company_id:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="That connection attempt has expired. Start again.",
        )

    connection: PendingConnection = entry["connection"]

    with connection.lock:
        current_status = connection.status
        qr_png_base64 = connection.qr_png_base64
        error = connection.error
        storage_state = connection.storage_state
        phone_number = connection.phone_number

    if current_status == "starting":
        return {"status": "starting"}

    if current_status == "qr_ready":
        return {"status": "qr_ready", "qr_png_base64": qr_png_base64}

    if current_status in ("failed", "expired"):
        _pending.pop(pending_id, None)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error or "That connection attempt did not complete. Start again.",
        )

    # current_status == "connected"
    values_extra = entry["values_extra"]
    _pending.pop(pending_id, None)

    try:
        account = channel_account_service.create_account(
            company_id=company_id,
            channel="whatsapp_qr",
            name=values_extra["name"],
            values={
                "branch_id": values_extra["branch_id"],
                "department_id": values_extra["department_id"],
                "external_account_id": phone_number,
                "access_token": json.dumps(storage_state),
                "_whatsapp_phone_number": phone_number,
            },
        )
    except ChannelAccountError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return {"status": "connected", "account": account}


@router.post("/connect/cancel/{pending_id}")
def cancel(
    pending_id: str,
    current_user: dict[str, Any] = Depends(require_elevated),
):
    company_id = auth_service.resolve_company_id(current_user)
    entry = _pending.get(pending_id)

    if entry and entry["company_id"] == company_id:
        connection: PendingConnection = entry["connection"]
        connection.cancel()
        _pending.pop(pending_id, None)

    return {"status": "cancelled"}
