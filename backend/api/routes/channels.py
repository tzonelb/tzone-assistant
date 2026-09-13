"""Connecting messaging accounts to a company.

Until this existed, routing a company's inbound messages required someone to
write a row by hand with SQL, which made onboarding a second company impossible
in practice.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.business_department_service import business_department_service
from backend.services.module_access import refuse_a_demonstration
from backend.services.channel_account_service import (
    ChannelAccountError,
    ROUTING_FIELD,
    channel_account_service,
    register_viber_webhook,
    unregister_viber_webhook,
)
from backend.services import channel_verification_service, mailer
from channels.discord import manager as discord_manager
from config.settings import config


from database.manager import database_manager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["Channels"])


ChannelName = Literal[
    "messenger", "instagram", "whatsapp", "telegram", "slack", "discord", "webchat",
    "email", "viber",
]


class ChannelAccountCreate(BaseModel):
    channel: ChannelName
    name: str = Field(min_length=1, max_length=120)
    branch_id: int | None = None
    # The section this account feeds. Optional: a company may connect three
    # Instagram accounts and point each at a different department, or point
    # none of them anywhere and let the customer choose from the menu.
    department_id: int | None = None

    page_id: str | None = Field(default=None, max_length=120)
    instagram_business_id: str | None = Field(default=None, max_length=120)
    phone_number_id: str | None = Field(default=None, max_length=120)
    # Email's routing value: the mailbox address itself. The one channel that
    # types this rather than deriving or generating it -- see
    # channel_account_service.ROUTING_FIELD's comment on why -- so it is the
    # one channel that needs it as a field on this model at all.
    external_account_id: str | None = Field(default=None, max_length=255)

    access_token: str | None = Field(default=None, max_length=1000)
    verify_token: str | None = Field(default=None, max_length=500)

    # Email's own connection settings. Ignored by every other channel, the
    # same way `page_id` is ignored by Slack.
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_use_ssl: bool = True
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_use_starttls: bool = True

    ai_enabled: bool = True
    flow_enabled: bool = True
    voice_ai_enabled: bool = False
    image_ai_enabled: bool = False

    @model_validator(mode="after")
    def require_routing_identifier(self) -> "ChannelAccountCreate":
        """Reject an account that inbound messages could not be routed to.

        Catching this here gives a clear field-level message instead of a
        database error, and prevents a record that silently receives nothing.
        """
        # Telegram is the exception, and deliberately so: its routing id is the
        # prefix of the bot token, so channel_account_service._validate derives
        # it rather than asking the operator to transcribe it. That means the
        # field is not on this model at all -- checking for it here rejected
        # every Telegram account ever submitted, because getattr found nothing
        # and returned None. What this layer can check is the token the
        # derivation needs.
        if self.channel == "telegram":
            if not self.access_token:
                raise ValueError(
                    "A telegram account requires the bot token from BotFather."
                )

            return self

        # Slack and Discord are the same shape as Telegram just above: the
        # routing id is derived from the bot token by
        # channel_account_service, not typed in.
        if self.channel == "slack":
            if not self.access_token:
                raise ValueError(
                    "A slack account requires its Bot User OAuth Token."
                )

            return self

        if self.channel == "discord":
            if not self.access_token:
                raise ValueError("A discord account requires its bot token.")

            return self

        if self.channel == "viber":
            if not self.access_token:
                raise ValueError(
                    "A viber account requires its bot Authentication Token."
                )

            return self

        # Website live chat needs nothing typed in at all -- there is no bot,
        # app or account on another platform to connect, so there is nothing
        # here to validate. channel_account_service mints the widget key.
        if self.channel == "webchat":
            return self

        # Email is the opposite of every branch above: there is no bot token
        # to derive a routing id from, so the operator types the mailbox
        # address (checked below by the generic fallback, since it is this
        # model's `external_account_id` field) and also the mailbox's own
        # credentials and mail servers, which the fallback has no field name
        # for.
        if self.channel == "email":
            if not self.access_token:
                raise ValueError("An email account requires the mailbox password.")

            if not self.imap_host:
                raise ValueError(
                    "An email account requires its IMAP server address."
                )

            if not self.smtp_host:
                raise ValueError(
                    "An email account requires its SMTP server address."
                )

        field = ROUTING_FIELD[self.channel]

        if not getattr(self, field, None):
            raise ValueError(
                f"A {self.channel} account requires {field.replace('_', ' ')}."
            )

        return self


class ChannelAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    branch_id: int | None = None
    # Sent explicitly as null to stop routing this account by channel; omitted
    # to leave the current pointer alone.
    department_id: int | None = None
    status: Literal["active", "disabled"] | None = None

    page_id: str | None = Field(default=None, max_length=120)
    instagram_business_id: str | None = Field(default=None, max_length=120)
    phone_number_id: str | None = Field(default=None, max_length=120)

    # An omitted secret keeps the stored one; an empty string clears it.
    access_token: str | None = Field(default=None, max_length=1000)
    verify_token: str | None = Field(default=None, max_length=500)

    # Email's connection settings. Unset means "keep whatever is already
    # stored" (see `channel_account_service.update_account`'s merge, not a
    # replace, of `config_json`) -- unlike the secrets above, there is no
    # separate "clear" action for these; a mailbox with no IMAP host is not a
    # state this form can reach.
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_use_ssl: bool | None = None
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_use_starttls: bool | None = None

    ai_enabled: bool | None = None
    flow_enabled: bool | None = None
    voice_ai_enabled: bool | None = None
    image_ai_enabled: bool | None = None


@router.get("")
def list_channels(
    current_user: dict[str, Any] = Depends(require_permission("channels.view")),
):
    company_id = auth_service.resolve_company_id(current_user)

    return {
        "items": channel_account_service.list_accounts(company_id),
        "supported_channels": list(ROUTING_FIELD.keys()),
        "routing_fields": ROUTING_FIELD,
        # The sections an account may be pointed at, so the screen can offer
        # them without a second round trip. This company's own, and only ever
        # this company's — the id is written into a control-plane column that
        # nothing else validates.
        "departments": [
            {
                "id": row["id"],
                "code": row["code"],
                "label": row.get("name_en") or row.get("name_ar") or row["code"],
            }
            for row in business_department_service.list_departments(
                company_id=company_id,
                enabled_only=True,
            )
        ],
        # The company's locations, for the same reason and with the same
        # scoping. The screen used to ask an owner to type a raw branch id,
        # which is a number nobody running a business knows — and one that
        # named another company's branch until the write started checking it.
        "branches": _branches(company_id),
    }


def _branches(company_id: int) -> list[dict[str, Any]]:
    """This company's active branches, id and name only.

    Never raises: a branch list that will not load costs the screen a dropdown,
    not the ability to connect a channel.
    """
    try:
        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT id, name
                FROM branches
                WHERE company_id = ? AND status = 'active'
                ORDER BY name
                """,
                (int(company_id),),
            ).fetchall()
    except Exception:  # noqa: BLE001
        logger.exception("Could not read the branches of company %s", company_id)

        return []

    return [{"id": int(row["id"]), "name": row["name"]} for row in rows]


# Connecting, editing or removing a channel account, for a workspace allowed to
# have one at all.
#
# `channels.manage` says this employee may; `refuse_a_demonstration` says this
# workspace may. They are different questions, and the second is what keeps a
# self-service sign-up from becoming a spam relay -- see
# `backend/services/demo_gate.py` for why the line is drawn at connecting a
# channel rather than at sending on one.
#
# One object rather than the pair repeated at three routes, so a fourth write
# route picks both up by asking for the same thing.
def manage_context(
    current_user: dict[str, Any] = Depends(require_permission("channels.manage")),
    _live: dict[str, Any] = Depends(refuse_a_demonstration),
) -> dict[str, Any]:
    return current_user


class ChannelVerificationConfirm(BaseModel):
    code: str = Field(min_length=6, max_length=6)


@router.post("/verification/request")
def request_channel_verification(
    current_user: dict[str, Any] = Depends(manage_context),
):
    """Email the signed-in account a 6-digit code, required before connecting
    or disconnecting a channel. Sent to the account's own address -- there is
    no one else to ask -- so unlike a forgot-password request there is no
    enumeration risk in refusing plainly when it cannot be delivered."""
    try:
        mailer.assert_configured()
    except mailer.MailerNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    company_id = auth_service.resolve_company_id(current_user)

    channel_verification_service.request_code(
        user_id=int(current_user["id"]),
        company_id=company_id,
        email=current_user["email"],
        full_name=current_user.get("full_name"),
    )

    return {
        "sent": True,
        "expires_in_minutes": config.CHANNEL_VERIFICATION_TTL_MINUTES,
    }


@router.post("/verification/confirm")
def confirm_channel_verification(
    payload: ChannelVerificationConfirm,
    current_user: dict[str, Any] = Depends(manage_context),
):
    company_id = auth_service.resolve_company_id(current_user)

    result = channel_verification_service.confirm_code(
        user_id=int(current_user["id"]),
        company_id=company_id,
        code=payload.code,
    )

    if not result["granted"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code is wrong, expired, or already used.",
        )

    return result


def require_elevated(
    request: Request,
    current_user: dict[str, Any] = Depends(manage_context),
) -> dict[str, Any]:
    """The extra gate on connecting or disconnecting a channel: everything
    ``manage_context`` already requires, plus a live elevated grant from
    confirming an emailed code. Editing an already-connected account's name,
    branch or AI toggles does not need this -- only establishing or removing
    the credential that routes a company's messages does, per the Channels
    screen's own documented behaviour.
    """
    token = request.headers.get("x-elevated-token", "")
    company_id = auth_service.resolve_company_id(current_user)

    if not channel_verification_service.is_elevated(
        user_id=int(current_user["id"]), company_id=company_id, token=token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "channel_verification_required",
                "message": (
                    "Verify your email with the 6-digit code before "
                    "connecting or disconnecting a channel."
                ),
            },
        )

    return current_user


@router.post("", status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: ChannelAccountCreate,
    request: Request,
    current_user: dict[str, Any] = Depends(require_elevated),
):
    company_id = auth_service.resolve_company_id(current_user)
    values = payload.model_dump(exclude={"channel", "name"})

    try:
        account = channel_account_service.create_account(
            company_id=company_id,
            channel=payload.channel,
            name=payload.name,
            values=values,
        )
    except ChannelAccountError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    # Discord alone needs this: every other channel is reachable the moment
    # its webhook route exists, but a bot receives nothing until this
    # platform opens its own Gateway connection to it -- so connecting the
    # account here is only half the work.
    if payload.channel == "discord":
        discord_manager.start_connection(
            account_id=int(account["id"]),
            company_id=company_id,
            token=payload.access_token,
        )

    # Viber, the same idea as Discord just above but the opposite shape: not
    # a connection to keep open, a one-time REST call to point this bot's
    # webhook at this platform -- which cannot happen until the account row
    # exists, since the webhook URL is built from its id. If Viber refuses
    # the registration, the account is rolled back rather than left
    # "connected" with nothing actually able to reach it -- the same "never
    # look connected and not be" rule the channel catalogue itself was
    # rebuilt around.
    if payload.channel == "viber":
        try:
            register_viber_webhook(
                token=payload.access_token, account_id=int(account["id"])
            )
        except ChannelAccountError as exc:
            channel_account_service.delete_account(company_id, int(account["id"]))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc

    # A security event as well as a business one: connecting a channel points
    # a company's customers at this platform, and it is mirrored to the control
    # plane so an operator can see it. The routing identifier is recorded, the
    # access token never — it is sealed and unreadable by design.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.CHANNEL_CONNECTED,
        category="channels",
        kind="security",
        target_type="channel_account",
        target_id=account.get("id"),
        summary=f"Connected {payload.channel} account {payload.name}",
        after={"channel": payload.channel, "name": payload.name},
        severity="notice",
        ip_address=client_ip(request),
    )

    return {"status": "connected", "account": account}


@router.get("/{account_id}")
def get_channel(
    account_id: int,
    current_user: dict[str, Any] = Depends(require_permission("channels.view")),
):
    company_id = auth_service.resolve_company_id(current_user)
    account = channel_account_service.get_account(company_id, account_id)

    if not account:
        raise HTTPException(status_code=404, detail="Channel account not found.")

    return account


@router.patch("/{account_id}")
def update_channel(
    account_id: int,
    payload: ChannelAccountUpdate,
    request: Request,
    current_user: dict[str, Any] = Depends(manage_context),
):
    company_id = auth_service.resolve_company_id(current_user)
    values = payload.model_dump(exclude_unset=True)

    # Replacing a credential is its own event. It is the change that can
    # silently redirect a company's messages, and it looks identical to a
    # rename in a log that records only "account updated".
    replaced_credentials = any(
        key in values for key in ("access_token", "verify_token", "app_secret")
    )
    previous = channel_account_service.get_account(company_id, account_id)

    try:
        account = channel_account_service.update_account(
            company_id=company_id,
            account_id=account_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except ChannelAccountError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=404 if "not found" in message.lower() else 409,
            detail=message,
        ) from exc

    # Keep the Gateway connection in step with the account it belongs to.
    # Disabling the account must stop it receiving messages immediately, not
    # at the next deploy; a new token means the old connection is
    # authenticated as a bot that may no longer even be this one.
    if (previous or {}).get("channel") == "discord":
        if account.get("status") == "disabled":
            discord_manager.stop_connection(account_id)
        elif "access_token" in values or (previous or {}).get("status") == "disabled":
            fresh = channel_account_service.credentials_for(
                company_id=company_id, channel="discord", account_id=account_id
            )

            if fresh and fresh.get("access_token"):
                discord_manager.stop_connection(account_id)
                discord_manager.start_connection(
                    account_id=account_id,
                    company_id=company_id,
                    token=fresh["access_token"],
                )

    # Same idea for Viber, and more lenient than the connect path above on
    # purpose: this account already worked before the edit, so a transient
    # failure here is logged rather than failing an otherwise-valid rename
    # or department change.
    if (previous or {}).get("channel") == "viber":
        if account.get("status") == "disabled":
            fresh = channel_account_service.credentials_for(
                company_id=company_id, channel="viber", account_id=account_id
            )

            if fresh and fresh.get("access_token"):
                unregister_viber_webhook(fresh["access_token"])
        elif "access_token" in values or (previous or {}).get("status") == "disabled":
            fresh = channel_account_service.credentials_for(
                company_id=company_id, channel="viber", account_id=account_id
            )

            if fresh and fresh.get("access_token"):
                try:
                    register_viber_webhook(
                        token=fresh["access_token"], account_id=account_id
                    )
                except ChannelAccountError:
                    logger.exception(
                        "Could not re-register the Viber webhook for "
                        "company %s account %s",
                        company_id,
                        account_id,
                    )

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=(
            Action.CHANNEL_CREDENTIALS_REPLACED
            if replaced_credentials
            else Action.CHANNEL_UPDATED
        ),
        category="channels",
        kind="security" if replaced_credentials else "change",
        target_type="channel_account",
        target_id=account_id,
        summary=(
            f"Replaced the credentials for {account.get('name')}"
            if replaced_credentials
            else f"Edited the {account.get('name')} channel"
        ),
        before={
            "name": (previous or {}).get("name"),
            "status": (previous or {}).get("status"),
        },
        after={"name": account.get("name"), "status": account.get("status")},
        severity="notice" if replaced_credentials else "info",
        ip_address=client_ip(request),
    )

    return {"status": "updated", "account": account}


@router.delete("/{account_id}")
def delete_channel(
    account_id: int,
    request: Request,
    current_user: dict[str, Any] = Depends(require_elevated),
):
    company_id = auth_service.resolve_company_id(current_user)
    previous = channel_account_service.get_account(company_id, account_id)

    # Read before the row is gone: the token needed to tell Viber to stop
    # delivering lives in the sealed column `delete_account` is about to
    # remove.
    viber_token = None

    if (previous or {}).get("channel") == "viber":
        fresh = channel_account_service.credentials_for(
            company_id=company_id, channel="viber", account_id=account_id
        )
        viber_token = (fresh or {}).get("access_token")

    if not channel_account_service.delete_account(company_id, account_id):
        raise HTTPException(status_code=404, detail="Channel account not found.")

    if (previous or {}).get("channel") == "discord":
        discord_manager.stop_connection(account_id)

    if viber_token:
        unregister_viber_webhook(viber_token)

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.CHANNEL_DISCONNECTED,
        category="channels",
        kind="security",
        target_type="channel_account",
        target_id=account_id,
        summary=(
            f"Disconnected {(previous or {}).get('name') or account_id}"
        ),
        before={
            "channel": (previous or {}).get("channel"),
            "name": (previous or {}).get("name"),
        },
        severity="notice",
        ip_address=client_ip(request),
    )

    return {"status": "disconnected"}
