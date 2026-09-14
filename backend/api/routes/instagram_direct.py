"""Connecting an Instagram account the unofficial way: the operator's own
username and password, logged in through Instagram's private mobile API
(via `instagrapi`) rather than a Meta developer app and OAuth -- the
"instagram" channel already built in this file's sibling, `channels.py`, is
the official Graph API version of this same platform; this is the other
one, the one that needs no Meta app review because it never talks to a
Meta-sanctioned endpoint at all.

### Why this is a separate router, not a branch of `POST /api/channels`

Every other channel's connect step is one request: paste a token, maybe
have the service call the provider once to confirm it, done. This one can
pause mid-login and ask the operator for something only they have --
Instagram may demand a two-factor code from a call to `login()` that
already reached Instagram's servers, and there is no way to suspend an
HTTP request across the minute or two it takes a person to open their
authenticator app and type six digits back in. So the flow is two calls:

    POST /api/instagram-direct/connect/start   -- username + password
    POST /api/instagram-direct/connect/verify  -- the code, if asked for

If Instagram's response to `start` is a plain success, `verify` is never
called. If it demands a code, `start` returns a `pending_id` naming an
in-memory, short-lived login attempt (the live `instagrapi.Client`,
already holding the username/password internally, cannot be serialized to
a database row between requests) and `verify` resumes that exact attempt.

### What this does not try to resolve

A *hard* Instagram checkpoint -- a photo ID, a selfie video, anything past
a plain SMS/email/authenticator code -- has no API path at all; Instagram's
own web and app clients are the only way through one. `start` and `verify`
both surface this as a plain refusal ("open the Instagram app...") rather
than looping or retrying, because retrying into a checkpoint is one of the
documented ways these accounts get permanently banned -- see
`channels/instagram_direct/poller.py`'s own docstring for the fuller
picture of why this channel treats every Instagram-side error as something
to stop and surface, never to retry past.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from instagrapi import Client
from instagrapi import exceptions as ig_exceptions
from pydantic import BaseModel, Field

from backend.api.routes.channels import require_elevated
from backend.services.auth_service import auth_service
from backend.services.channel_account_service import (
    ChannelAccountError,
    channel_account_service,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/instagram-direct", tags=["Instagram (direct login)"])


# How long an in-progress login (a live, already-authenticated-with-
# username-and-password `Client` object, waiting on a 2FA code) is kept
# before it is discarded. Single-process, in-memory, the same shape as
# every other short-lived server-side cache in this codebase -- there is
# nothing here worth persisting past a restart, and a stale pending login
# is exactly as good as none.
PENDING_LOGIN_TTL_SECONDS = 300

_pending_logins: dict[str, dict[str, Any]] = {}


def _prune_pending_logins() -> None:
    now = time.monotonic()
    expired = [key for key, entry in _pending_logins.items() if entry["expires_at"] < now]

    for key in expired:
        _pending_logins.pop(key, None)


class InstagramDirectConnectStart(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=500)
    branch_id: int | None = None
    department_id: int | None = None
    # Not required. Stored sealed (see channel_account_service's comment on
    # why it reuses `verify_token` for this) and, per the platform's own
    # unofficial-channel research, the single highest-leverage mitigation
    # against this account being flagged for calling Instagram from a
    # datacenter IP -- one stable residential/mobile proxy, kept for the
    # life of this connection, rather than the platform's own shared egress.
    proxy_url: str | None = Field(default=None, max_length=500)


class InstagramDirectConnectVerify(BaseModel):
    pending_id: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=20)


def _finish_connect(*, company_id: int, client: Any, values_extra: dict[str, Any]) -> dict[str, Any]:
    """The one place both `start` (no 2FA needed) and `verify` (2FA
    resolved) land once Instagram has actually authenticated the client."""
    name = values_extra.pop("name")

    try:
        account = channel_account_service.create_account(
            company_id=company_id,
            channel="instagram_direct",
            name=name,
            values={
                **values_extra,
                "external_account_id": str(client.user_id),
                "access_token": json.dumps(client.get_settings()),
                "_instagram_username": client.username,
            },
        )
    except ChannelAccountError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return {"status": "connected", "account": account}


def _values_extra(payload: InstagramDirectConnectStart) -> dict[str, Any]:
    return {
        "name": payload.name.strip(),
        "branch_id": payload.branch_id,
        "department_id": payload.department_id,
        "verify_token": payload.proxy_url or None,
    }


@router.post("/connect/start")
def start_connect(
    payload: InstagramDirectConnectStart,
    current_user: dict[str, Any] = Depends(require_elevated),
):
    _prune_pending_logins()

    company_id = auth_service.resolve_company_id(current_user)
    client = Client()

    if payload.proxy_url:
        try:
            client.set_proxy(payload.proxy_url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That proxy URL could not be used.",
            ) from exc

    try:
        client.login(payload.username.strip(), payload.password)
    except ig_exceptions.TwoFactorRequired:
        pending_id = secrets.token_urlsafe(24)
        _pending_logins[pending_id] = {
            "client": client,
            "company_id": company_id,
            "expires_at": time.monotonic() + PENDING_LOGIN_TTL_SECONDS,
            "values_extra": _values_extra(payload),
        }
        return {"status": "needs_code", "pending_id": pending_id}
    except (ig_exceptions.BadPassword, ig_exceptions.BadCredentials) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Instagram rejected that username or password.",
        ) from exc
    except ig_exceptions.ChallengeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Instagram needs this account verified before it can log in "
                "here. Open the Instagram app on the account's own phone, "
                "complete whatever it asks for there, then try connecting "
                "again."
            ),
        ) from exc
    except ig_exceptions.ClientError as exc:
        logger.warning("Instagram direct-login failed to start: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Instagram. Please try again.",
        ) from exc

    return _finish_connect(
        company_id=company_id, client=client, values_extra=_values_extra(payload)
    )


@router.post("/connect/verify")
def verify_connect(
    payload: InstagramDirectConnectVerify,
    current_user: dict[str, Any] = Depends(require_elevated),
):
    _prune_pending_logins()

    company_id = auth_service.resolve_company_id(current_user)
    entry = _pending_logins.get(payload.pending_id)

    if not entry or entry["company_id"] != company_id:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="That connection attempt has expired. Start again.",
        )

    client = entry["client"]

    try:
        client.login(verification_code=payload.code.strip())
    except ig_exceptions.TwoFactorRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code was not accepted. Please try again.",
        ) from exc
    except ig_exceptions.ChallengeError as exc:
        _pending_logins.pop(payload.pending_id, None)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Instagram needs this account verified before it can log in "
                "here. Open the Instagram app on the account's own phone, "
                "complete whatever it asks for there, then try connecting "
                "again."
            ),
        ) from exc
    except ig_exceptions.ClientError as exc:
        logger.warning("Instagram direct-login verify failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Instagram. Please try again.",
        ) from exc

    _pending_logins.pop(payload.pending_id, None)

    return _finish_connect(
        company_id=company_id, client=client, values_extra=entry["values_extra"]
    )
