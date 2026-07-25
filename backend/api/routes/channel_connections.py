from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.services.auth_service import auth_service, get_current_user
from backend.services.channel_account_service import channel_account_service, ChannelAccountError


router = APIRouter(prefix="/api/channels", tags=["Channel Connections"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


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
    company_id = _company_id(current_user)
    return {"channels": channel_account_service.list_for_company(company_id=company_id)}


@router.post("/telegram/connect")
def connect_telegram(
    payload: ConnectTelegramRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        account = channel_account_service.connect_telegram(
            company_id=company_id, bot_token=payload.bot_token, name=payload.name,
        )
    except ChannelAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Go live immediately — no restart needed, matching the "connect
    # page, and it just works" experience that was asked for.
    try:
        from channels.telegram import manager as telegram_manager
        token = channel_account_service.get_decrypted_token(account_id=account["id"])
        telegram_manager.start_bot(account_id=account["id"], company_id=company_id, bot_token=token)
    except Exception as exc:
        # The account is saved either way; surface this so the company
        # knows the bot isn't receiving messages yet rather than assuming
        # silently that it is.
        account["_bot_start_warning"] = str(exc)

    return account


@router.post("/whatsapp/connect")
def connect_whatsapp(
    payload: ConnectWhatsAppRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        return channel_account_service.connect_whatsapp(
            company_id=company_id,
            phone_number_id=payload.phone_number_id,
            access_token=payload.access_token,
            name=payload.name,
        )
    except ChannelAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/instagram/connect")
def connect_instagram(
    payload: ConnectInstagramRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        return channel_account_service.connect_instagram(
            company_id=company_id,
            page_id=payload.page_id,
            access_token=payload.access_token,
            name=payload.name,
        )
    except ChannelAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{account_id}")
async def disconnect_channel(
    account_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    company_id = _company_id(current_user)
    try:
        account = channel_account_service.disconnect(company_id=company_id, account_id=account_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Channel account not found")

    if account["channel"] == "telegram":
        from channels.telegram import manager as telegram_manager
        await telegram_manager.stop_bot(account_id=account_id)

    return account
