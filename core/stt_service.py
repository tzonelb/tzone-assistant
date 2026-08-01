import logging

import httpx

from config.settings import config

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # OpenAI's Whisper endpoint hard-rejects anything larger


class STTService:
    """Speech-to-text via OpenAI's Whisper transcription endpoint — lets
    the AI actually understand a customer's voice note by turning it into
    text and feeding it through the exact same reply pipeline as a typed
    message. Reuses the existing OPENAI_API_KEY."""

    def transcribe(self, audio_bytes: bytes, *, filename: str = "voice.ogg") -> str:
        if not audio_bytes:
            raise ValueError("No audio to transcribe.")
        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise ValueError(f"Audio file too large to transcribe ({len(audio_bytes)} bytes, max {MAX_AUDIO_BYTES}).")
        if not config.OPENAI_API_KEY:
            raise ValueError("AI is not configured on this server (missing OPENAI_API_KEY).")

        headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
        files = {"file": (filename, audio_bytes)}
        data = {"model": config.OPENAI_STT_MODEL}

        with httpx.Client(timeout=40) as client:
            response = client.post(config.OPENAI_STT_API_URL, headers=headers, files=files, data=data)

        if response.status_code >= 400:
            raise RuntimeError(f"OpenAI transcription error {response.status_code}: {response.text}")

        text = response.json().get("text", "").strip()
        if not text:
            raise RuntimeError("OpenAI returned an empty transcription")
        return text


stt_service = STTService()
