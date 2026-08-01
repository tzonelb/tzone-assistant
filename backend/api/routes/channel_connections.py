from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.channel_account_service import channel_account_service, ChannelAccountError
from backend.services.security_verification_service import security_verification_service


router = APIRouter(prefix="/api/channels", tags=["Channel Connections"])

VERIFICATION_PURPOSE = "channels_access"


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


def _require_elevated(
    current_user: dict[str, Any],
    x_elevated_token: str | None,
) -> None:
    """Double security: connecting/disconnecting a channel (which stores
    real credentials) requires a verified, still-fresh email OTP session —
    not just being logged in. See /api/security/send-code + /verify-code."""
    if not security_verification_service.check_elevated(
        user_id=current_user["id"], token=x_elevated_token or "", purpose=VERIFICATION_PURPOSE,
    ):
        raise HTTPException(
            status_code=401,
            detail="This action requires email verification. Please verify your identity first.",
        )


class ConnectTelegramRequest(BaseModel):
    bot_token: str = Field(min_length=1)
    name: str | None = None


class ConnectWhatsAppRequest(BaseModel):
    phone_number_id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    name: str | None = None


class ConnectInstagramRequest(BaseModel):
    page_id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    name: str | None = None


@router.get("")
def list_channels(current_user: dict[str, Any] = Depends(get_current_user)):
    # Read-only, no secrets in the response (tokens are never returned) —
    # no elevated verification needed just to see what's connected.
    company_id = _company_id(current_user)
    return {"channels": channel_account_service.list_for_company(company_id=company_id)}


@router.post("/telegram/connect")
def connect_telegram(
    payload: ConnectTelegramRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    x_elevated_token: str | None = Header(default=None),
):
    _require_elevated(current_user, x_elevated_token)
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "channels.manage")
    try:
        account = channel_account_service.connect_telegram(
            company_id=company_id, bot_token=payload.bot_token, name=payload.name,
        )
    except ChannelAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        from channels.telegram import manager as telegram_manager
        token = channel_account_service.get_decrypted_token(account_id=account["id"])
        telegram_manager.start_bot(account_id=account["id"], company_id=company_id, bot_token=token)
    except Exception as exc:
        account["_bot_start_warning"] = str(exc)

    security_verification_service.log_change(
        user_id=current_user["id"], purpose=VERIFICATION_PURPOSE,
        description=f"Connected Telegram channel \"{account['name']}\"",
    )
    return account


@router.post("/whatsapp/connect")
def connect_whatsapp(
    payload: ConnectWhatsAppRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    x_elevated_token: str | None = Header(default=None),
):
    _require_elevated(current_user, x_elevated_token)
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "channels.manage")
    try:
        account = channel_account_service.connect_whatsapp(
            company_id=company_id,
            phone_number_id=payload.phone_number_id,
            access_token=payload.access_token,
            name=payload.name,
        )
    except ChannelAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    security_verification_service.log_change(
        user_id=current_user["id"], purpose=VERIFICATION_PURPOSE,
        description=f"Connected WhatsApp channel \"{account['name']}\"",
    )
    return account


@router.post("/instagram/connect")
def connect_instagram(
    payload: ConnectInstagramRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    x_elevated_token: str | None = Header(default=None),
):
    _require_elevated(current_user, x_elevated_token)
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "channels.manage")
    try:
        account = channel_account_service.connect_instagram(
            company_id=company_id,
            page_id=payload.page_id,
            access_token=payload.access_token,
            name=payload.name,
        )
    except ChannelAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    security_verification_service.log_change(
        user_id=current_user["id"], purpose=VERIFICATION_PURPOSE,
        description=f"Connected Instagram channel \"{account['name']}\"",
    )
    return account


@router.delete("/{account_id}")
async def disconnect_channel(
    account_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    x_elevated_token: str | None = Header(default=None),
):
    _require_elevated(current_user, x_elevated_token)
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "channels.manage")
    try:
        account = channel_account_service.disconnect(company_id=company_id, account_id=account_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Channel account not found")

    if account["channel"] == "telegram":
        from channels.telegram import manager as telegram_manager
        await telegram_manager.stop_bot(account_id=account_id)

    security_verification_service.log_change(
        user_id=current_user["id"], purpose=VERIFICATION_PURPOSE,
        description=f"Disconnected {account['channel']} channel \"{account['name']}\"",
    )
    return account
