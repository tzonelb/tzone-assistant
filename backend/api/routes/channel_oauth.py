"""Facebook OAuth connect flow for Messenger/Instagram channel accounts.

Two endpoints:

- POST /api/channels/facebook/connect (authenticated) builds a signed,
  tamper-proof "state" value and returns a Facebook OAuth dialog URL for
  the frontend to redirect the browser to.
- GET  /api/channels/facebook/callback (unauthenticated -- Facebook
  redirects the raw browser here with no Bearer token) verifies that
  state, exchanges the auth code for page tokens, stores them encrypted
  in channel_accounts, and always ends in an HTTP redirect back to the
  frontend's Company Settings > Channels tab, success or failure.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

from backend.services.auth_service import auth_service, get_current_user
from backend.services.token_crypto import encrypt_token
from config.settings import config
from database.database import db


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels/facebook", tags=["Channel OAuth"])

STATE_TTL_SECONDS = 600

FACEBOOK_OAUTH_SCOPES = (
    "pages_show_list,pages_messaging,pages_manage_metadata,"
    "instagram_basic,instagram_manage_messages"
)


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


def _require_channels_manage(current_user: dict[str, Any], company_id: int) -> None:
    if current_user.get("is_super_admin"):
        return
    if auth_service.has_permission(
        current_user["id"], company_id, "channels.manage", False
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to manage channels.",
    )


# ---------------------------------------------------------------------------
# Signed state helpers. The state param is the only thing carried through
# the Facebook redirect round-trip, so it must be cryptographically
# verified on callback (not just base64'd) -- this is what prevents CSRF
# and prevents an attacker from linking a Facebook page to an arbitrary
# company_id.
# ---------------------------------------------------------------------------


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign_state(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded_body = _b64url_encode(body)
    signature = hmac.new(
        config.JWT_SECRET.encode("utf-8"),
        encoded_body.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded_body}.{signature}"


def _verify_state(state: str | None) -> dict[str, Any] | None:
    if not state:
        return None

    try:
        encoded_body, signature = state.rsplit(".", 1)
    except ValueError:
        return None

    expected_signature = hmac.new(
        config.JWT_SECRET.encode("utf-8"),
        encoded_body.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        return None

    try:
        payload = json.loads(_b64url_decode(encoded_body))
    except Exception:
        return None

    issued_at = payload.get("iat")
    if not isinstance(issued_at, (int, float)):
        return None

    if time.time() - issued_at > STATE_TTL_SECONDS:
        return None

    if not isinstance(payload.get("company_id"), int):
        return None

    return payload


def _callback_url() -> str:
    return f"{config.PUBLIC_APP_URL}/api/channels/facebook/callback"


def _frontend_redirect(status_value: str, reason: str | None = None) -> RedirectResponse:
    url = (
        f"{config.FRONTEND_URL}/company-settings/channels"
        f"?connected=facebook&status={status_value}"
    )
    if reason:
        url += f"&reason={quote(reason)}"
    return RedirectResponse(url=url, status_code=302)


@router.post("/connect")
def connect_facebook(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = _company_id(current_user)
    _require_channels_manage(current_user, company_id)

    if not config.FACEBOOK_APP_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Facebook integration is not configured (missing FACEBOOK_APP_ID).",
        )

    state = _sign_state({
        "company_id": company_id,
        "actor_user_id": current_user["id"],
        "nonce": secrets.token_hex(16),
        "iat": time.time(),
    })

    authorize_url = (
        f"https://www.facebook.com/{config.META_API_VERSION}/dialog/oauth"
        f"?client_id={quote(config.FACEBOOK_APP_ID)}"
        f"&redirect_uri={quote(_callback_url())}"
        f"&scope={FACEBOOK_OAUTH_SCOPES}"
        f"&state={quote(state)}"
    )

    return {"authorize_url": authorize_url}


@router.get("/callback")
def facebook_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        return _frontend_redirect("error", error_description or error)

    if not code:
        return _frontend_redirect("error", "missing_code")

    payload = _verify_state(state)
    if payload is None:
        return _frontend_redirect("error", "invalid_or_expired_state")

    company_id = payload["company_id"]

    if not config.FACEBOOK_APP_ID or not config.FACEBOOK_APP_SECRET:
        return _frontend_redirect("error", "facebook_not_configured")

    try:
        return _run_callback_exchange(code=code, company_id=company_id)
    except Exception:
        logger.exception("facebook oauth: unexpected callback failure")
        return _frontend_redirect("error", "unexpected_error")


def _run_callback_exchange(code: str, company_id: int) -> RedirectResponse:
    try:
        token_response = httpx.get(
            f"https://graph.facebook.com/{config.META_API_VERSION}/oauth/access_token",
            params={
                "client_id": config.FACEBOOK_APP_ID,
                "client_secret": config.FACEBOOK_APP_SECRET,
                "redirect_uri": _callback_url(),
                "code": code,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        logger.warning("facebook oauth: token exchange request failed: %s", exc)
        return _frontend_redirect("error", "token_exchange_failed")

    if not token_response.is_success:
        logger.warning(
            "facebook oauth: token exchange failed: status=%s body=%s",
            token_response.status_code,
            token_response.text,
        )
        return _frontend_redirect("error", "token_exchange_failed")

    token_payload = token_response.json() if token_response.content else {}
    user_access_token = token_payload.get("access_token")

    if not user_access_token:
        return _frontend_redirect("error", "token_exchange_failed")

    try:
        pages_response = httpx.get(
            f"https://graph.facebook.com/{config.META_API_VERSION}/me/accounts",
            params={
                "access_token": user_access_token,
                "fields": "id,name,access_token",
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        logger.warning("facebook oauth: pages fetch request failed: %s", exc)
        return _frontend_redirect("error", "pages_fetch_failed")

    if not pages_response.is_success:
        logger.warning(
            "facebook oauth: pages fetch failed: status=%s body=%s",
            pages_response.status_code,
            pages_response.text,
        )
        return _frontend_redirect("error", "pages_fetch_failed")

    pages_payload = pages_response.json() if pages_response.content else {}
    pages = pages_payload.get("data") or []

    if not pages:
        return _frontend_redirect("error", "no_pages_found")

    connected_count = 0

    with db.connect() as conn:
        for page in pages:
            page_id = page.get("id")
            page_token = page.get("access_token")
            page_name = page.get("name") or f"Facebook Page {page_id}"

            if not page_id or not page_token:
                continue

            encrypted_token = encrypt_token(page_token)

            conn.execute(
                """
                INSERT INTO channel_accounts (
                    company_id, channel, name, external_account_id,
                    page_id, access_token_encrypted, status
                ) VALUES (?, 'messenger', ?, ?, ?, ?, 'active')
                ON CONFLICT(channel, external_account_id)
                WHERE external_account_id IS NOT NULL
                DO UPDATE SET
                    company_id = excluded.company_id,
                    name = excluded.name,
                    page_id = excluded.page_id,
                    access_token_encrypted = excluded.access_token_encrypted,
                    status = excluded.status,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (company_id, page_name, page_id, page_id, encrypted_token),
            )
            connected_count += 1

            instagram_business_id = _lookup_instagram_business_id(page_id, page_token)

            if instagram_business_id:
                conn.execute(
                    """
                    INSERT INTO channel_accounts (
                        company_id, channel, name, external_account_id,
                        instagram_business_id, access_token_encrypted, status
                    ) VALUES (?, 'instagram', ?, ?, ?, ?, 'active')
                    ON CONFLICT(channel, external_account_id)
                    WHERE external_account_id IS NOT NULL
                    DO UPDATE SET
                        company_id = excluded.company_id,
                        name = excluded.name,
                        instagram_business_id = excluded.instagram_business_id,
                        access_token_encrypted = excluded.access_token_encrypted,
                        status = excluded.status,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        company_id,
                        f"{page_name} (Instagram)",
                        instagram_business_id,
                        instagram_business_id,
                        encrypted_token,
                    ),
                )

        conn.commit()

    if connected_count == 0:
        return _frontend_redirect("error", "no_pages_connected")

    return _frontend_redirect("ok")


def _lookup_instagram_business_id(page_id: str, page_token: str) -> str | None:
    try:
        response = httpx.get(
            f"https://graph.facebook.com/{config.META_API_VERSION}/{page_id}",
            params={
                "fields": "instagram_business_account",
                "access_token": page_token,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "facebook oauth: instagram lookup failed for page %s: %s", page_id, exc
        )
        return None

    if not response.is_success or not response.content:
        return None

    try:
        data = response.json()
    except Exception:
        return None

    account = data.get("instagram_business_account") or {}
    return account.get("id")
