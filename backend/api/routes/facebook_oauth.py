from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from backend.services.auth_service import auth_service, get_current_user
from backend.services.facebook_oauth_service import facebook_oauth_service, FacebookOAuthError
from config.settings import config


router = APIRouter(prefix="/api/channels/facebook", tags=["Facebook OAuth"])


def _return_url() -> str:
    # CompanySettingsPage only ever resolves the active tab from a
    # ?section= query param (every other caller — Dashboard, Reply Flow
    # Builder — already uses that form); a path segment here silently
    # lands on the default Profile tab instead of Channels.
    return f"{config.FRONTEND_BASE_URL}/company-settings?section=channels"


@router.get("/oauth/start")
def start_oauth(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(current_user)
    auth_service.require_permission(current_user, company_id, "channels.manage")
    try:
        url = facebook_oauth_service.build_authorize_url(company_id=company_id)
    except FacebookOAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"authorize_url": url}


@router.get("/oauth/callback")
def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code or not state:
        return RedirectResponse(url=f"{_return_url()}&fb_error=login_cancelled")

    try:
        result = facebook_oauth_service.handle_callback(code=code, state=state)
    except FacebookOAuthError as exc:
        return RedirectResponse(url=f"{_return_url()}&fb_error={exc}")

    connected_count = len(result["connected"])
    return RedirectResponse(url=f"{_return_url()}&fb_connected={connected_count}")
