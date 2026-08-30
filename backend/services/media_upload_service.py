"""Files an employee attaches to a reply.

Stored on disk under ``config.UPLOAD_DIR``, one directory per company, and named
by a random id rather than by what the uploader called the file. Two reasons for
the rename: the original name is attacker-controlled text that would otherwise
become a path, and two people attaching "invoice.pdf" must not overwrite each
other.

Not encrypted the way the databases are, and that is a deliberate limit worth
stating: the channel has to fetch this file over plain HTTPS to deliver it, so a
key only this server holds would make it unreadable to the recipient. What the
per-company directory does give is the same boundary as everything else -- one
company's uploads are never reachable through another company's id.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any

from config.settings import config


# Comfortably under what WhatsApp and Messenger accept, so a file this platform
# stores is a file the channel will take.
MAX_UPLOAD_BYTES = 16 * 1024 * 1024

# Extension -> the kind of message the channel senders build. Anything not here
# is refused at upload, rather than accepted and then failing at send time when
# the employee has already told the customer it is coming.
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
    ".gif": "image",
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
    ".mp3": "audio",
    ".ogg": "audio",
    ".m4a": "audio",
    ".wav": "audio",
    ".pdf": "document",
    ".doc": "document",
    ".docx": "document",
    ".xls": "document",
    ".xlsx": "document",
    ".ppt": "document",
    ".pptx": "document",
    ".csv": "document",
    ".txt": "document",
    ".zip": "document",
}

# What a stored name is allowed to look like, used when reading one back. A
# lookup is by id, so anything with a separator or a dot-segment in it is not a
# name this service ever wrote.
_STORED_NAME = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{1,8}$")


class MediaUploadError(RuntimeError):
    """An upload was refused for a reason worth showing the caller."""


class MediaUploadService:
    def _company_dir(self, company_id: int) -> Path:
        return Path(config.UPLOAD_DIR) / f"company_{int(company_id)}"

    def save(
        self, *, company_id: int, filename: str, content: bytes
    ) -> dict[str, Any]:
        """Store one file and describe it.

        Returns the stored name, the kind of media it is, and the path the
        channel will be given -- never the caller's own filename as a path.
        """
        if not content:
            raise MediaUploadError("That file is empty.")

        if len(content) > MAX_UPLOAD_BYTES:
            raise MediaUploadError(
                f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB."
            )

        # Only the suffix is taken from the uploader, and only after it matches
        # the allow-list -- so "../../etc/passwd" contributes nothing.
        extension = Path(str(filename or "")).suffix.lower()
        media_type = ALLOWED_EXTENSIONS.get(extension)

        if not media_type:
            raise MediaUploadError(
                f"{extension or 'That file type'} cannot be sent on any channel "
                "this platform supports."
            )

        directory = self._company_dir(company_id)
        directory.mkdir(parents=True, exist_ok=True)

        stored_name = f"{secrets.token_hex(16)}{extension}"
        (directory / stored_name).write_bytes(content)

        return {
            "stored_name": stored_name,
            "media_type": media_type,
            "filename": Path(str(filename or "")).name or stored_name,
            "size_bytes": len(content),
            "url": f"/api/media/{int(company_id)}/{stored_name}",
        }

    def path_for(self, *, company_id: int, stored_name: str) -> Path:
        """Where a stored file lives, or a refusal.

        The name is checked against the shape this service writes rather than
        merely cleaned: a lookup is by generated id, so anything else is either
        a bug or someone walking the filesystem.
        """
        name = str(stored_name or "")

        if not _STORED_NAME.match(name):
            raise MediaUploadError("No such file.")

        path = self._company_dir(company_id) / name

        if not path.is_file():
            raise MediaUploadError("No such file.")

        return path


media_upload_service = MediaUploadService()
