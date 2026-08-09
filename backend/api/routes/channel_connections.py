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


class ConnectInstagramDirectRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    verification_code: str | None = None


class ConnectFacebookDirectRequest(BaseModel):
    page: str = Field(min_length=1)
    c_user: str = Field(min_length=1)
    xs: str = Field(min_length=1)


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


@router.post("/instagram-direct/connect")
def connect_instagram_direct(
    payload: ConnectInstagramDirectRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    x_elevated_token: str | None = Header(default=None),
):
    """Instagram by direct account login (no Meta developer app). The
    password is used once for the login and never stored — only the
    resulting session, encrypted."""
    _require_elevated(current_user, x_elevated_token)
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "channels.manage")

    from backend.services.social_session_service import (
        DependencyMissingError, SocialSessionError, social_session_service,
    )
    try:
        account = social_session_service.connect_instagram(
            company_id=company_id, username=payload.username,
            password=payload.password, verification_code=payload.verification_code,
        )
    except DependencyMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (SocialSessionError, ChannelAccountError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    security_verification_service.log_change(
        user_id=current_user["id"], purpose=VERIFICATION_PURPOSE,
        description=f"Connected Instagram (direct login) \"{account['name']}\"",
    )
    return account


@router.post("/facebook-direct/connect")
def connect_facebook_direct(
    payload: ConnectFacebookDirectRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    x_elevated_token: str | None = Header(default=None),
):
    """Facebook Page download by browser cookies (no Meta developer app).
    Read-only: posts + comments download; replying stays on the official
    connection or the phone app."""
    _require_elevated(current_user, x_elevated_token)
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "channels.manage")

    from backend.services.social_session_service import (
        DependencyMissingError, SocialSessionError, social_session_service,
    )
    try:
        account = social_session_service.connect_facebook(
            company_id=company_id, page=payload.page,
            c_user=payload.c_user, xs=payload.xs,
        )
    except DependencyMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (SocialSessionError, ChannelAccountError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    security_verification_service.log_change(
        user_id=current_user["id"], purpose=VERIFICATION_PURPOSE,
        description=f"Connected Facebook (cookie download) \"{account['name']}\"",
    )
    return account


@router.post("/whatsapp-qr/start")
def start_whatsapp_qr(
    current_user: dict[str, Any] = Depends(get_current_user),
    x_elevated_token: str | None = Header(default=None),
):
    """Begin a WhatsApp Web QR pairing session (no Meta app needed). The
    frontend then polls the status endpoint, which returns the QR image
    until the phone scans it and the session connects."""
    _require_elevated(current_user, x_elevated_token)
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "channels.manage")

    from secrets import token_hex
    from channels.whatsapp_qr import service as wa_bridge

    session_key = f"waqr-{company_id}-{token_hex(8)}"
    try:
        wa_bridge.start_session(session_key)
    except wa_bridge.BridgeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except wa_bridge.BridgeRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    security_verification_service.log_change(
        user_id=current_user["id"], purpose=VERIFICATION_PURPOSE,
        description="Started WhatsApp QR pairing",
    )
    return {"session_key": session_key, "status": "starting"}


@router.get("/whatsapp-qr/status/{session_key}")
def whatsapp_qr_status(
    session_key: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    x_elevated_token: str | None = Header(default=None),
):
    """Polled by the connect page. Reading bridge status only needs
    channels.manage; the moment the bridge reports "connected" we persist
    the account, and THAT write requires the same elevated email-OTP token
    every other channel connect does — the frontend keeps it from the
    elevated-gated /start call and passes it on every poll."""
    company_id = _company_id(current_user)
    auth_service.require_permission(current_user, company_id, "channels.manage")
    if not session_key.startswith(f"waqr-{company_id}-"):
        raise HTTPException(status_code=403, detail="This pairing session belongs to another company.")

    from channels.whatsapp_qr import service as wa_bridge

    try:
        status = wa_bridge.get_session_status(session_key)
    except wa_bridge.BridgeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except wa_bridge.BridgeRequestError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    result: dict[str, Any] = {
        "status": status.get("status"),
        "qr": status.get("qr"),
        "phone": status.get("phone"),
    }
    if status.get("status") == "connected":
        _require_elevated(current_user, x_elevated_token)
        try:
            account = channel_account_service.connect_whatsapp_qr(
                company_id=company_id, session_key=session_key,
                phone=status.get("phone"), name=None,
            )
            result["account"] = account
        except ChannelAccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return result


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

    if account["channel"] == "whatsapp_qr" and account.get("external_account_id"):
        from channels.whatsapp_qr import service as wa_bridge
        try:
            wa_bridge.delete_session(account["external_account_id"])
        except (wa_bridge.BridgeUnavailableError, wa_bridge.BridgeRequestError):
            pass  # bridge down — the account row is gone either way

    security_verification_service.log_change(
        user_id=current_user["id"], purpose=VERIFICATION_PURPOSE,
        description=f"Disconnected {account['channel']} channel \"{account['name']}\"",
    )
    return account
