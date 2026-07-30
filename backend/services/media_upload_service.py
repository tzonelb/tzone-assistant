from __future__ import annotations

import uuid
from pathlib import Path

from config.settings import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent
UPLOAD_ROOT = BASE_DIR / "uploads"

MAX_UPLOAD_BYTES = 16 * 1024 * 1024  # 16MB — comfortably under WhatsApp/Messenger's own media limits.

# Extension -> media_type, restricted to what the channel senders below
# actually know how to send. Anything else is rejected up front rather
# than silently failing at send time.
ALLOWED_EXTENSIONS = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".gif": "image",
    ".mp4": "video", ".mov": "video", ".webm": "video",
    ".mp3": "audio", ".ogg": "audio", ".m4a": "audio", ".wav": "audio",
}


class MediaUploadService:
    def ensure_storage(self) -> None:
        (UPLOAD_ROOT / "broadcast").mkdir(parents=True, exist_ok=True)

    def save_upload(self, *, filename: str, content: bytes) -> dict[str, str]:
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError(f"File is too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB).")
        if not content:
            raise ValueError("Uploaded file is empty.")

        extension = Path(filename or "").suffix.lower()
        media_type = ALLOWED_EXTENSIONS.get(extension)
        if not media_type:
            allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
            raise ValueError(f'"{extension or "(none)"}" is not a supported file type. Allowed: {allowed}.')

        self.ensure_storage()
        stored_name = f"{uuid.uuid4().hex}{extension}"
        destination = UPLOAD_ROOT / "broadcast" / stored_name
        destination.write_bytes(content)

        relative_path = f"/uploads/broadcast/{stored_name}"
        return {
            "url": f"{config.PUBLIC_BACKEND_URL.rstrip('/')}{relative_path}",
            "media_type": media_type,
        }


media_upload_service = MediaUploadService()
