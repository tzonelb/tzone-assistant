from __future__ import annotations

import logging
import subprocess
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioTranscodeError(Exception):
    pass


def transcode_to_mp3(content: bytes) -> bytes:
    """Converts browser-recorded audio (webm/opus in Chrome, ogg/opus in
    Firefox — MediaRecorder can't produce mp3 directly) into mp3 bytes.

    mp3 (audio/mpeg) is the one audio format already proven to work as a
    voice note across all four channels (it's what core/tts_service.py's
    AI voice replies use), so every voice note gets normalized to it
    instead of juggling per-browser/per-channel codec compatibility —
    WhatsApp Cloud API in particular only accepts a narrow audio
    allowlist (aac/mp4/mpeg/amr/ogg-opus) and rejects raw webm outright.

    Uses the portable ffmpeg binary bundled by imageio-ffmpeg (no system
    install required). Raises AudioTranscodeError on any failure."""
    import imageio_ffmpeg

    if not content:
        raise AudioTranscodeError("No audio recorded.")

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        input_path = tmp_dir_path / f"{uuid.uuid4().hex}.input"
        output_path = tmp_dir_path / f"{uuid.uuid4().hex}.mp3"
        input_path.write_bytes(content)

        try:
            result = subprocess.run(
                [
                    ffmpeg_path, "-y",
                    "-i", str(input_path),
                    "-vn", "-ac", "1", "-ar", "44100", "-b:a", "64k",
                    str(output_path),
                ],
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AudioTranscodeError(f"Could not run ffmpeg: {exc}") from exc

        if result.returncode != 0 or not output_path.exists():
            stderr = (result.stderr or b"").decode("utf-8", errors="replace")[-500:]
            logger.warning("Voice note transcode failed: %s", stderr)
            raise AudioTranscodeError("Voice note could not be converted to a playable format.")

        return output_path.read_bytes()
