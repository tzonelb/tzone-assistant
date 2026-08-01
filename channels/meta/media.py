import logging

import httpx

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # matches OpenAI's Whisper/vision upload ceiling


def download_meta_attachment(url: str) -> bytes | None:
    """Messenger/Instagram attachment URLs (message.attachments[].payload.url)
    are pre-signed CDN links — unlike WhatsApp's Cloud API, no bearer token
    or two-step lookup is needed, just a plain GET. Returns None (never
    raises) on any failure so callers can skip the message rather than
    crash the webhook."""
    if not url:
        return None
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(url)
            if response.status_code >= 400:
                logger.warning("Meta attachment download failed: %s", response.status_code)
                return None
            if len(response.content) > MAX_ATTACHMENT_BYTES:
                logger.warning("Meta attachment too large to download: %s bytes", len(response.content))
                return None
            return response.content
    except httpx.HTTPError:
        logger.exception("Meta attachment download raised an exception")
        return None
