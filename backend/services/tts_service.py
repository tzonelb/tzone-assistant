"""Text-to-speech for the assistant's voice replies.

Provider model
--------------
`TTSProvider` is the interface. `OpenAITTSProvider` is the only
implementation, speaking OpenAI's audio API over httpx — the same key the
assistant already uses to answer, `OPENAI_API_KEY`, rather than a second
secret a company would have to go find. `NullProvider` is what the platform
runs on when that key is not set: `voice_status()` reports it as
unconfigured, the settings screen shows the toggle disabled with a reason,
and no reply path is asked to speak.

What a voice reply actually is
-------------------------------
Synthesised audio is stored the same way an employee's attachment is —
through `media_upload_service`, under the company's own upload directory —
and delivered through the existing `channels.sender.send_media`, the same
function a manually attached voice note goes through. There is no new
transport: text-to-speech only decides what audio to produce, not how a
company's channels receive one.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config.settings import config


logger = logging.getLogger(__name__)


# OpenAI's own limit on one request's input text. A reply longer than this is
# truncated before being sent for synthesis rather than left to fail at the
# provider with an error nobody watching the conversation would see.
MAX_TEXT_LENGTH = 4000

NOT_CONFIGURED_MESSAGE = (
    "Voice replies are not configured. Set OPENAI_API_KEY to enable them."
)


class TTSNotConfiguredError(Exception):
    """Synthesis was attempted with no provider credentials."""


class TTSError(Exception):
    """The provider refused or could not be reached."""


# ----------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------


class TTSProvider:
    """What a provider has to be able to do."""

    name = "none"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def synthesize(self, *, text: str) -> bytes:
        raise NotImplementedError


class NullProvider(TTSProvider):
    """No credentials, so no synthesis — a clear refusal rather than a crash."""

    name = "none"

    def is_configured(self) -> bool:
        return False

    def synthesize(self, *, text: str) -> bytes:
        raise TTSNotConfiguredError(NOT_CONFIGURED_MESSAGE)


class OpenAITTSProvider(TTSProvider):
    name = "openai"

    def __init__(self, api_key: str, *, model: str, voice: str, api_url: str, timeout: int) -> None:
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.api_url = api_url
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def synthesize(self, *, text: str) -> bytes:
        try:
            response = httpx.post(
                self.api_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "voice": self.voice,
                    "input": text,
                    "response_format": "mp3",
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise TTSError("The voice provider could not be reached.") from exc

        if response.status_code >= 400:
            # The provider's own words, truncated: the response is JSON on
            # error and raw audio bytes on success, so this branch is the only
            # place text is safe to read out of it.
            raise TTSError(
                f"Voice provider error {response.status_code}: "
                f"{response.text[:300]}"
            )

        if not response.content:
            raise TTSError("The voice provider returned no audio.")

        return response.content


def build_provider() -> TTSProvider:
    if config.OPENAI_API_KEY:
        return OpenAITTSProvider(
            config.OPENAI_API_KEY,
            model=config.OPENAI_TTS_MODEL,
            voice=config.OPENAI_TTS_VOICE,
            api_url=config.OPENAI_TTS_API_URL,
            timeout=config.OPENAI_TTS_TIMEOUT_SECONDS,
        )

    return NullProvider()


# ----------------------------------------------------------------------
# The service
# ----------------------------------------------------------------------


class TTSService:
    def __init__(self) -> None:
        self.provider = build_provider()

    def voice_status(self) -> dict[str, Any]:
        """Whether voice replies work here, and what is missing when they do
        not — the same shape the Dialer's own status check answers with, for
        the same reason: a disabled switch that does not say why is a dead
        end, and a missing name sends someone to one line of one file."""
        missing = [] if config.OPENAI_API_KEY else ["OPENAI_API_KEY"]
        configured = not missing and self.provider.is_configured()

        return {
            "configured": configured,
            "provider": self.provider.name,
            "missing": missing,
        }

    def synthesize_and_store(self, *, company_id: int, text: str) -> dict[str, Any]:
        """Turn a reply's text into a stored audio file and describe it.

        Returns the same shape `media_upload_service.save` returns for any
        other attachment — `url` is a same-origin path, not yet the absolute
        one a channel provider needs to fetch it.
        """
        if not self.provider.is_configured():
            raise TTSNotConfiguredError(NOT_CONFIGURED_MESSAGE)

        clean_text = str(text or "").strip()

        if not clean_text:
            raise TTSError("There is no text to speak.")

        audio = self.provider.synthesize(text=clean_text[:MAX_TEXT_LENGTH])

        from backend.services.media_upload_service import media_upload_service

        return media_upload_service.save(
            company_id=int(company_id), filename="reply.mp3", content=audio
        )


tts_service = TTSService()


# ----------------------------------------------------------------------
# Wiring into a reply
# ----------------------------------------------------------------------


def send_voice_reply_if_enabled(
    *,
    company_id: int,
    channel: str,
    recipient_id: str,
    text: str,
    buttons: list[str] | None = None,
) -> dict[str, Any] | None:
    """Speak a reply instead of sending it as text, when the company has
    turned this on and a provider is actually configured to do it.

    Returns ``None`` — never raises — whenever a voice reply does not apply,
    so the caller's existing text path is exactly what runs instead: the
    setting being off, no provider configured, quick-reply buttons attached
    (Messenger's attachment payload carries no buttons, so sending one would
    silently drop them), synthesis failing, or the platform having no public
    address a channel provider could fetch the audio from.
    """
    if buttons:
        return None

    try:
        from backend.services.company_settings_service import (
            company_settings_service,
        )

        enabled = bool(
            company_settings_service.get_section(int(company_id), "ai_behavior")[
                "values"
            ].get("voice_reply_enabled")
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not read the voice reply setting of company %s", company_id
        )
        return None

    if not enabled or not tts_service.provider.is_configured():
        return None

    public_base = str(config.APP_PUBLIC_URL or "").rstrip("/")

    if not public_base.lower().startswith(("http://", "https://")):
        logger.warning(
            "Voice reply enabled for company %s but APP_PUBLIC_URL is not set "
            "to a public address; sending as text instead.",
            company_id,
        )
        return None

    try:
        media = tts_service.synthesize_and_store(company_id=company_id, text=text)
    except (TTSNotConfiguredError, TTSError):
        logger.exception("Voice reply synthesis failed for company %s", company_id)
        return None

    from channels.sender import send_media

    result = send_media(
        channel=channel,
        recipient_id=recipient_id,
        company_id=company_id,
        media_url=f"{public_base}{media['url']}",
        media_type="audio",
    )

    # The same-origin path, not the absolute one just given to the provider:
    # the conversation transcript is served to this platform's own browser,
    # which resolves a relative path against itself.
    result["media_url"] = media["url"]
    result["media_type"] = "audio"

    return result
