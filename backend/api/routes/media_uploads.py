import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from backend.services.auth_service import auth_service, get_current_user
from backend.services.media_upload_service import media_upload_service
from core.audio_transcode import AudioTranscodeError, transcode_to_mp3


router = APIRouter(prefix="/api/media", tags=["Media Uploads"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


@router.post("/upload")
async def upload_media(file: UploadFile, context=Depends(current_context)):
    _, _company_id = context
    content = await file.read()
    try:
        return media_upload_service.save_upload(filename=file.filename or "", content=content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/upload-voice-note")
async def upload_voice_note(file: UploadFile, context=Depends(current_context)):
    """Recorded voice notes arrive as whatever MediaRecorder's browser
    produces (webm/opus in Chrome, ogg/opus in Firefox) — neither is in
    WhatsApp Cloud API's accepted audio allowlist, so this always
    transcodes to mp3 before storing, then reuses the normal upload
    pipeline unchanged."""
    _, _company_id = context
    content = await file.read()
    try:
        mp3_bytes = transcode_to_mp3(content)
    except AudioTranscodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        return media_upload_service.save_upload(
            filename=f"voice-note-{uuid.uuid4().hex}.mp3", content=mp3_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
