"""The "Log in with Facebook" connect flow for Messenger and Instagram.

This is the scaffold for one-click channel connection: an owner clicks Connect,
approves on Facebook, and the Pages they manage (and the Instagram accounts
linked to them) become channel accounts -- no page ids or access tokens typed by
hand. It is real OAuth against the Graph API, but it does nothing until a Meta
app is configured (``META_APP_ID`` + ``META_APP_SECRET``) and that app has passed
App Review for the messaging permissions. Until then ``is_configured()`` is
False, the connect button is never shown, and ``start()`` refuses -- so the flow
never reports a "connected" it cannot deliver.

Security: the ``state`` carried through the redirect is signed with a key
derived from the platform master key and binds the flow to the company and user
that began it, with a short expiry. A callback whose state does not verify, is
expired, or is for a different signed-in user is rejected -- this is the CSRF
guard the OAuth spec requires.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlencode

import httpx

from backend.security import keyring
from config.settings import config


logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 600  # ten minutes from click to callback is plenty
_GRAPH = "https://graph.facebook.com"
_DIALOG = "https://www.facebook.com"


class MetaOAuthError(Exception):
    """A connect flow that cannot proceed, with a reason a person may read."""


def _signing_key() -> bytes:
    # A dedicated key for signing OAuth state, derived from the master key so it
    # needs no separate secret to manage and rotates with the master key.
    return hashlib.sha256(b"meta-oauth-state:" + keyring.load_master_key()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class MetaOAuthService:
    def is_configured(self) -> bool:
        return bool(config.META_APP_ID and config.META_APP_SECRET)

    def redirect_uri(self) -> str:
        # Must match a redirect URI registered on the Meta app exactly.
        base = str(config.APP_PUBLIC_URL or "").rstrip("/")
        return f"{base}/api/channels/oauth/facebook/callback"

    # ------------------------------------------------------------ state token

    def sign_state(self, *, company_id: int, user_id: int) -> str:
        payload = {
            "c": int(company_id),
            "u": int(user_id),
            "e": int(time.time()) + STATE_TTL_SECONDS,
        }
        body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
        return f"{body}.{_b64(signature)}"

    def decode_state(self, state: str) -> dict | None:
        """Verify the signed state and return who started the flow, or None.

        The callback is a top-level redirect from facebook.com, so the
        SameSite=Strict session cookie is not sent with it. The signed state is
        therefore the authority on which company and user this flow belongs to:
        it cannot be forged without the master-derived key, and it expires. A
        state that fails the signature or is past its expiry returns None and the
        callback is refused.
        """
        try:
            body, sig = str(state).split(".", 1)
            expected = hmac.new(
                _signing_key(), body.encode("ascii"), hashlib.sha256
            ).digest()
            if not hmac.compare_digest(_unb64(sig), expected):
                return None
            payload = json.loads(_unb64(body))
        except Exception:  # noqa: BLE001
            return None

        if int(payload.get("e", 0)) < int(time.time()):
            return None
        return {"company_id": int(payload.get("c", 0)), "user_id": int(payload.get("u", 0))}

    # ---------------------------------------------------------------- the flow

    def authorize_url(self, *, company_id: int, user_id: int) -> str:
        if not self.is_configured():
            raise MetaOAuthError(
                "Facebook login is not set up on this platform yet. Connect a "
                "Page with its access token on the Channels screen instead."
            )
        params = {
            "client_id": config.META_APP_ID,
            "redirect_uri": self.redirect_uri(),
            "state": self.sign_state(company_id=company_id, user_id=user_id),
            "scope": config.META_OAUTH_SCOPES,
            "response_type": "code",
        }
        return f"{_DIALOG}/{config.META_GRAPH_VERSION}/dialog/oauth?{urlencode(params)}"

    def exchange_code(self, code: str) -> str:
        """The short-lived user access token for an authorization code."""
        params = {
            "client_id": config.META_APP_ID,
            "client_secret": config.META_APP_SECRET,
            "redirect_uri": self.redirect_uri(),
            "code": code,
        }
        data = self._graph_get("oauth/access_token", params)
        token = data.get("access_token")
        if not token:
            raise MetaOAuthError("Facebook did not return an access token.")
        return str(token)

    def list_pages(self, user_token: str) -> list[dict]:
        """The Pages this person manages, each with its own page token.

        Each entry also carries the linked Instagram business account when the
        Page has one, so a single approval can connect both a Messenger and an
        Instagram channel.
        """
        data = self._graph_get(
            f"{config.META_GRAPH_VERSION}/me/accounts",
            {
                "access_token": user_token,
                "fields": "id,name,access_token,instagram_business_account{id,username}",
                "limit": 100,
            },
        )
        pages: list[dict] = []
        for item in data.get("data", []):
            ig = item.get("instagram_business_account") or {}
            pages.append(
                {
                    "page_id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "page_access_token": str(item.get("access_token") or ""),
                    "instagram_business_id": str(ig.get("id") or "") or None,
                    "instagram_username": str(ig.get("username") or "") or None,
                }
            )
        return pages

    # ----------------------------------------------------------------- helper

    def _graph_get(self, path: str, params: dict) -> dict:
        url = f"{_GRAPH}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=20) as client:
                response = client.get(url, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Graph API request failed: %s", path)
            raise MetaOAuthError("Could not reach Facebook. Please try again.") from exc

        if response.status_code >= 400:
            # Meta returns a JSON error with a human message; surface it without
            # leaking the request (which carries the app secret / token).
            try:
                message = response.json().get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001
                message = ""
            logger.warning("Graph API error %s on %s", response.status_code, path)
            raise MetaOAuthError(
                message or "Facebook rejected the request. Please try again."
            )
        return response.json()


meta_oauth_service = MetaOAuthService()
