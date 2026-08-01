import logging

import httpx

from config.settings import config

logger = logging.getLogger(__name__)


class TTSService:
    """Text-to-speech via OpenAI's Audio Speech API — reuses the same
    OPENAI_API_KEY already configured for chat replies, so enabling voice
    replies needs no new credential from the company."""

    def generate_speech(self, text: str) -> bytes:
        text = (text or "").strip()
        if not text:
            raise ValueError("Nothing to speak.")
        if not config.OPENAI_API_KEY:
            raise ValueError("AI is not configured on this server (missing OPENAI_API_KEY).")

        payload = {
            "model": config.OPENAI_TTS_MODEL,
            "voice": config.OPENAI_TTS_VOICE,
            "input": text[:4000],
            "response_format": "mp3",
        }
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=40) as client:
            response = client.post(config.OPENAI_TTS_API_URL, headers=headers, json=payload)

        if response.status_code >= 400:
            raise RuntimeError(f"OpenAI TTS error {response.status_code}: {response.text}")

        if not response.content:
            raise RuntimeError("OpenAI TTS returned empty audio")

        return response.content


tts_service = TTSService()
