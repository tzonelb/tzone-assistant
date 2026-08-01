import logging

import httpx

from channels.whatsapp.sender import _resolve_whatsapp_credentials
from config.settings import config

logger = logging.getLogger(__name__)


def download_whatsapp_media(media_id: str, company_id: int | None = None) -> tuple[bytes, str] | None:
    """WhatsApp Cloud API is a two-step fetch: GET the media id for a
    short-lived download URL + mime type, then GET that URL with the same
    bearer token. Returns (bytes, mime_type) or None on any failure —
    callers must treat a missing download as non-fatal (skip this
    message rather than crash the webhook)."""
    _, access_token = _resolve_whatsapp_credentials(company_id)
    if not access_token:
        logger.warning("Cannot download WhatsApp media: no access token available.")
        return None

    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        lookup_url = f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/{media_id}"
        with httpx.Client(timeout=20) as client:
            lookup = client.get(lookup_url, headers=headers)
            if lookup.status_code >= 400:
                logger.warning("WhatsApp media lookup failed: %s %s", lookup.status_code, lookup.text)
                return None
            info = lookup.json()
            download_url = info.get("url")
            mime_type = info.get("mime_type", "")
            if not download_url:
                return None

            download = client.get(download_url, headers=headers)
            if download.status_code >= 400:
                logger.warning("WhatsApp media download failed: %s", download.status_code)
                return None
            return download.content, mime_type
    except httpx.HTTPError:
        logger.exception("WhatsApp media download raised an exception")
        return None
