from fastapi import APIRouter, Depends, HTTPException, UploadFile

from backend.services.auth_service import auth_service, get_current_user
from backend.services.media_upload_service import media_upload_service


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
