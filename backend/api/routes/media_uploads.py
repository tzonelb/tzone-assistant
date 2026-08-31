"""Uploading a file to attach to a reply, and serving it back.

Two routes with deliberately different guards.

The upload takes `conversations.reply`: attaching a file is part of answering a
customer, and someone who may only read the inbox must not be able to put files
on this server.

The read is **unauthenticated by necessity**, and that is the interesting one.
The channel -- Meta, WhatsApp, Telegram -- fetches the file from this URL to
deliver it, and it arrives with no session. What stands in for a session is the
name: 128 bits of randomness, unguessable, and issued only to the employee who
uploaded the file. A URL that leaks is a file that leaks, which is the same
property every "unlisted link" has; nothing else about the company is reachable
through it.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from backend.services.auth_service import auth_service, require_permission
from backend.services.media_upload_service import (
    MAX_UPLOAD_BYTES,
    MediaUploadError,
    media_upload_service,
)


# Two routers on purpose. `router` is registered behind the conversations
# module gate like every other inbox route. `public_router` is registered
# without it, because the thing fetching a delivered file is the channel, and it
# arrives with no session and no company -- a gate there would mean no customer
# ever receives an attachment.
router = APIRouter(prefix="/api/media", tags=["Media"])
public_router = APIRouter(prefix="/api/media", tags=["Media"])


def reply_context(
    current_user: dict[str, Any] = Depends(require_permission("conversations.reply")),
):
    company_id = auth_service.resolve_company_id(current_user)
    return current_user, int(company_id)


async def _store(file: UploadFile, company_id: int) -> dict[str, Any]:
    # Read with a ceiling rather than reading it all and checking afterwards: a
    # 2GB upload would otherwise be held in memory before being refused.
    content = await file.read(MAX_UPLOAD_BYTES + 1)

    try:
        return media_upload_service.save(
            company_id=company_id,
            filename=file.filename or "",
            content=content,
        )
    except MediaUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.post("/upload")
async def upload_media(file: UploadFile, context=Depends(reply_context)):
    _, company_id = context
    return await _store(file, company_id)


@router.post("/upload-voice-note")
async def upload_voice_note(file: UploadFile, context=Depends(reply_context)):
    """A recording made in the composer.

    Same storage as any other upload; a separate route because the interface
    treats a voice note as its own kind of message and the browser sends it
    without a filename worth keeping.
    """
    _, company_id = context
    stored = await _store(file, company_id)

    if stored["media_type"] != "audio":
        # The bytes are already written by this point, and the caller is about
        # to be told the upload was refused. Anything left behind would be
        # unreferenced, unreachable through the product, and still served by
        # the public read route -- so it goes before the refusal does.
        media_upload_service.remove(
            company_id=company_id, stored_name=stored["stored_name"]
        )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A voice note has to be an audio recording.",
        )

    return stored


@public_router.get("/{company_id}/{stored_name}")
def read_media(company_id: int, stored_name: str):
    """Serve a stored file to whoever holds its link -- the channel, usually."""
    try:
        path = media_upload_service.path_for(
            company_id=company_id, stored_name=stored_name
        )
    except MediaUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    return FileResponse(path)
