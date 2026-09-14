"""Connecting a Facebook Page by the operator's own exported browser cookies,
rather than a Meta developer app and the official Graph API -- the same
"unofficial, ask the operator for their own session instead" shape
`instagram_direct.py` already uses, but simpler in one real way: Facebook
cookies exported from an already-logged-in browser (with a cookie-export
extension, hence the catalogue's own "cookie download" name) are already
fully authenticated. There is no username/password to submit and so no 2FA
step this route has to pause for -- connecting is one call, not two.

### What this is a channel account for, and what it is not

Every other channel here routes customer *messages* into the same
conversation pipeline (`channels/inbound.py`). This one does not: a
Facebook Page's comments are public replies from many different people
under a post, not a private conversation with one customer, and this
platform already has a home for exactly that shape --
`backend/services/comment_service.py`'s ``post_comments`` table, which the
official "messenger"/"instagram" Graph API channels already feed via
webhook. `channels/facebook_direct/poller.py` feeds the same table by
reading Facebook's own pages instead of asking Meta's API, so a company's
Comments queue looks the same regardless of which channel a comment came in
on.

It is still a real `channel_accounts` row: the same sealed-credential
storage, the same elevated-grant connect gate, the same account list and
disconnect button as every messaging channel. What it deliberately is not
part of is any of the *messaging* machinery -- see
`channel_account_service.COMMENT_ONLY_CHANNELS` for where that is drawn.

### Read-only, on purpose

The catalogue has only ever promised reading a Page's posts and comments,
never replying to them, and this route holds that line: there is no
`facebook_direct` branch in `channels/comment_sender.py`'s publisher, and
there will not be one. A reply typed by a browser macro against someone
else's session is a materially different -- and materially riskier -- action
than reading a page that session can already see, and the one thing this
platform's own unofficial-channel research found in every case worth
worrying about was exactly that kind of active, repeated, automated write.
Reading is the whole feature.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.api.routes.channels import require_elevated
from backend.services.auth_service import auth_service
from backend.services.channel_account_service import (
    ChannelAccountError,
    channel_account_service,
)
from channels.facebook_direct.browser import (
    FacebookSessionError,
    validate_session_and_fetch_page_name,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/facebook-direct", tags=["Facebook (cookie download)"])


class FacebookDirectConnect(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # The Facebook Page's own numeric id, not a vanity name -- the cookies'
    # own account may manage several Pages, so this is what picks one, and
    # what `mbasic.facebook.com/<page_id>` is fetched with below.
    page_id: str = Field(min_length=1, max_length=64)
    # The JSON array a browser cookie-export extension produces: objects with
    # at least `name` and `value`, one entry per exported cookie.
    cookies_json: str = Field(min_length=2, max_length=40000)
    branch_id: int | None = None
    department_id: int | None = None


def _parse_cookies(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ChannelAccountError(
            "That doesn't look like exported cookies. Paste the whole JSON "
            "array your browser's cookie-export extension produced."
        ) from exc

    if not isinstance(data, list) or not data:
        raise ChannelAccountError(
            "The cookies must be a JSON array with at least one cookie in it."
        )

    cookies: list[dict[str, Any]] = []

    for entry in data:
        if not isinstance(entry, dict):
            continue

        name = entry.get("name")
        value = entry.get("value")

        if not name or value is None:
            continue

        same_site = str(entry.get("sameSite") or "").strip().capitalize()

        cookies.append(
            {
                "name": str(name),
                "value": str(value),
                "domain": str(entry.get("domain") or ".facebook.com"),
                "path": str(entry.get("path") or "/"),
                "secure": bool(entry.get("secure", True)),
                "httpOnly": bool(entry.get("httpOnly", False)),
                # Playwright only accepts these three literal values; an
                # extension's own export uses whatever casing its own author
                # chose, or leaves it out. "Lax" is Facebook's own default and
                # the safe fallback for anything unrecognised.
                "sameSite": same_site if same_site in ("Strict", "Lax", "None") else "Lax",
            }
        )

    if not any(cookie["name"] == "c_user" for cookie in cookies):
        raise ChannelAccountError(
            "These cookies don't include c_user, the one Facebook uses to "
            "identify who is signed in. Export cookies from a browser tab "
            "that is currently signed in to Facebook."
        )

    return cookies


@router.post("/connect")
def connect(
    payload: FacebookDirectConnect,
    current_user: dict[str, Any] = Depends(require_elevated),
):
    company_id = auth_service.resolve_company_id(current_user)
    page_id = payload.page_id.strip()

    try:
        cookies = _parse_cookies(payload.cookies_json)
    except ChannelAccountError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    try:
        page_name = validate_session_and_fetch_page_name(cookies, page_id)
    except FacebookSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    try:
        account = channel_account_service.create_account(
            company_id=company_id,
            channel="facebook_direct",
            name=payload.name.strip(),
            values={
                "external_account_id": page_id,
                "access_token": json.dumps(cookies),
                "_facebook_page_name": page_name,
                "branch_id": payload.branch_id,
                "department_id": payload.department_id,
            },
        )
    except ChannelAccountError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return {"status": "connected", "account": account}
