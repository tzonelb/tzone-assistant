"""Python-side client for the local WhatsApp Web bridge.

The bridge (channels/whatsapp_qr/bridge/, a small Node app speaking the
WhatsApp Web protocol) lets a company connect WhatsApp by scanning a QR
code — no Meta developer app, no Cloud API credentials. This module is
the only place the backend talks to it: session lifecycle for the
connect flow, and text sending for the unified outbound path in
channels/whatsapp/sender.py.

Everything degrades cleanly when the bridge process isn't running:
callers get BridgeUnavailableError (surfaced as HTTP 503 with a setup
notice, same pattern as the Twilio dialer), never a crash.
"""

import hmac
import logging
from typing import Any

import httpx

from config.settings import config

logger = logging.getLogger(__name__)


class BridgeUnavailableError(RuntimeError):
    """The bridge process is not reachable at WA_BRIDGE_URL."""


class BridgeRequestError(RuntimeError):
    """The bridge answered, but refused the request."""


def _headers() -> dict[str, str]:
    return {"X-Bridge-Secret": config.WA_BRIDGE_SECRET}


def _request(method: str, path: str, json_body: dict[str, Any] | None = None, timeout: float = 15) -> dict[str, Any]:
    url = f"{config.WA_BRIDGE_URL.rstrip('/')}{path}"
    try:
        response = httpx.request(method, url, json=json_body, headers=_headers(), timeout=timeout)
    except httpx.HTTPError as exc:
        raise BridgeUnavailableError(
            "The WhatsApp QR bridge is not running. Start it with: "
            "node channels/whatsapp_qr/bridge/index.js"
        ) from exc
    payload: dict[str, Any] = {}
    if response.text:
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text[:500]}
    if response.status_code >= 400:
        raise BridgeRequestError(payload.get("detail") or f"Bridge error {response.status_code}")
    return payload


DEFAULT_BRIDGE_SECRET = "tzone-local-bridge-secret"
DEFAULT_JWT_SECRET = "change-this-before-production"


def _is_local_dev() -> bool:
    """True only for an untouched local-development environment.

    We do NOT trust APP_ENV alone: it defaults to "development", so a real
    deploy that forgets to set APP_ENV=production would otherwise be
    treated as dev (fail-open). Requiring the JWT secret to still be its
    built-in default as well means any genuinely-configured deployment
    (which must set a real JWT_SECRET to function securely at all) is
    treated as production here even if APP_ENV was left unset."""
    looks_dev = config.DEBUG or getattr(config, "APP_ENV", "") == "development"
    jwt_untouched = getattr(config, "JWT_SECRET", "") == DEFAULT_JWT_SECRET
    return looks_dev and jwt_untouched


def verify_webhook_secret(candidate: str | None) -> bool:
    """Constant-time compare of the bridge's shared secret. Outside local
    development, an empty OR default secret is refused outright: the bridge
    and this webhook ship with the same published default, and an empty
    secret would make `compare_digest(candidate or "", "")` accept an empty
    header — either way anyone who read the source could inject messages
    (and trigger paid AI). Mirrors the Meta webhook's "no real secret +
    production -> reject" stance (channels/meta/webhook.py)."""
    real_secret = (config.WA_BRIDGE_SECRET or "").strip()
    if not _is_local_dev() and (not real_secret or config.WA_BRIDGE_SECRET == DEFAULT_BRIDGE_SECRET):
        logger.error(
            "WA_BRIDGE_SECRET is empty or the built-in default in a non-development "
            "environment; refusing the WhatsApp bridge webhook. Set a strong "
            "WA_BRIDGE_SECRET in .env and in the bridge process."
        )
        return False
    return hmac.compare_digest(str(candidate or ""), config.WA_BRIDGE_SECRET)


def start_session(session_key: str) -> dict[str, Any]:
    return _request("POST", f"/sessions/{session_key}/start")


def get_session_status(session_key: str) -> dict[str, Any]:
    return _request("GET", f"/sessions/{session_key}")


def delete_session(session_key: str) -> dict[str, Any]:
    # Shorter timeout: this runs on the connect request path (revoke-on-repair),
    # so a hung bridge shouldn't tie up the response for the full 15s.
    return _request("DELETE", f"/sessions/{session_key}", timeout=5)


def send_text(session_key: str, to: str, text: str) -> dict[str, Any]:
    """Send a plain text message through a paired QR session. Returns the
    same result contract as channels/whatsapp/sender.py's Cloud API path
    ({"sent", "status_code", "response": {"messages": [{"id": ...}]}}) so
    every existing caller — smart replies, manual replies, reply flows,
    broadcasts — can treat both transports identically."""
    try:
        payload = _request("POST", f"/sessions/{session_key}/send", {"to": str(to), "text": text})
    except (BridgeUnavailableError, BridgeRequestError) as exc:
        logger.warning("WhatsApp QR send failed: %s", exc)
        return {"sent": False, "status_code": 503, "reason": str(exc), "response": {}}
    return {
        "sent": bool(payload.get("sent")),
        "status_code": 200,
        "response": {"messages": [{"id": payload.get("message_id")}]},
    }
