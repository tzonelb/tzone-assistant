"""Connect Messenger/Instagram with "Log in with Facebook".

Three endpoints. ``config`` tells the screen whether to offer the button at all
(it does not, until a Meta app is configured). ``start`` hands back the Facebook
authorization URL, signed with a state that binds the flow to this company and
user. ``callback`` is where Facebook returns the person: it trusts the signed
state rather than the session cookie -- a top-level redirect from facebook.com
does not carry a SameSite=Strict cookie -- exchanges the code, and turns each
Page the person manages into a channel account.

Nothing here fabricates a connection: with no Meta app, ``config`` reports it is
off and ``start`` refuses, so the screen never shows a Connect button that
cannot work.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from backend.services.auth_service import auth_service, require_permission
from backend.services.channel_account_service import (
    ChannelAccountError,
    channel_account_service,
)
from backend.services.meta_oauth_service import MetaOAuthError, meta_oauth_service
from config.settings import config


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels/oauth", tags=["Channel OAuth"])


def _view(current_user: dict[str, Any] = Depends(require_permission("channels.view"))) -> int:
    return auth_service.resolve_company_id(current_user)


def _manage(current_user: dict[str, Any] = Depends(require_permission("channels.manage"))):
    return current_user


@router.get("/facebook/config")
def facebook_config(_company_id: int = Depends(_view)) -> dict[str, Any]:
    return {"configured": meta_oauth_service.is_configured()}


@router.post("/facebook/start")
def facebook_start(current_user: dict = Depends(_manage)) -> dict[str, Any]:
    # A platform fact, checked before the per-company resolution: with no Meta
    # app there is nothing to start, whoever is asking.
    if not meta_oauth_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Facebook login is not set up on this platform yet. Connect a "
                "Page with its access token on the Channels screen instead."
            ),
        )
    company_id = auth_service.resolve_company_id(current_user)
    user_id = int(current_user["id"])
    try:
        url = meta_oauth_service.authorize_url(company_id=company_id, user_id=user_id)
    except MetaOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"authorize_url": url}


def _settings_redirect(status: str, detail: str = "") -> RedirectResponse:
    # Always land the person back on the Channels screen, with a short status
    # the page can show. The detail is kept brief and free of anything sensitive.
    base = str(config.APP_PUBLIC_URL or "").rstrip("/")
    from urllib.parse import urlencode

    query = urlencode({"section": "channels", "connect": status, **({"reason": detail} if detail else {})})
    return RedirectResponse(url=f"{base}/company-settings?{query}", status_code=303)


@router.get("/facebook/callback")
def facebook_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    # The person declined on Facebook, or Meta returned an error.
    if error or not code:
        return _settings_redirect("cancelled")

    decoded = meta_oauth_service.decode_state(state or "")
    if not decoded or not decoded.get("company_id"):
        # A forged, tampered, or expired state. Refuse without touching anything.
        return _settings_redirect("invalid")

    company_id = int(decoded["company_id"])

    try:
        user_token = meta_oauth_service.exchange_code(code)
        pages = meta_oauth_service.list_pages(user_token)
    except MetaOAuthError as exc:
        logger.warning("Facebook connect failed for company %s: %s", company_id, exc)
        return _settings_redirect("failed")

    connected = 0
    for page in pages:
        page_id = page.get("page_id")
        token = page.get("page_access_token")
        if not page_id or not token:
            continue

        # One Messenger account per Page.
        if _connect_account(
            company_id=company_id,
            channel="messenger",
            name=page.get("name") or f"Page {page_id}",
            values={"page_id": page_id, "access_token": token},
        ):
            connected += 1

        # And an Instagram account when the Page has a linked IG business
        # account (the same Page token authorises IG messaging).
        ig_id = page.get("instagram_business_id")
        if ig_id:
            if _connect_account(
                company_id=company_id,
                channel="instagram",
                name=page.get("instagram_username") or f"Instagram {ig_id}",
                values={"instagram_business_id": ig_id, "access_token": token},
            ):
                connected += 1

    if connected == 0:
        return _settings_redirect("none")
    return _settings_redirect("ok", str(connected))


def _connect_account(*, company_id: int, channel: str, name: str, values: dict) -> bool:
    """Create one channel account, treating "already connected" as success.

    A person who reconnects, or whose Page was already linked, should see the
    account present -- not an error -- so a routing-id clash is swallowed. Any
    other failure is logged and skipped so one bad Page cannot abort the rest.
    """
    try:
        channel_account_service.create_account(
            company_id=company_id, channel=channel, name=name, values=values
        )
        return True
    except ChannelAccountError as exc:
        # Most often: this Page/IG id is already connected. Not an error worth
        # failing the whole flow over.
        logger.info("Skipped connecting %s for company %s: %s", channel, company_id, exc)
        return False
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected error connecting %s for company %s", channel, company_id)
        return False
