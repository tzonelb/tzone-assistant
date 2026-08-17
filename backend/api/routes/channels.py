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
from backend.services.channel_account_service import (
    ChannelAccountError,
    ROUTING_FIELD,
    channel_account_service,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["Channels"])


ChannelName = Literal["messenger", "instagram", "whatsapp"]


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

    access_token: str | None = Field(default=None, max_length=1000)
    verify_token: str | None = Field(default=None, max_length=500)

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
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: ChannelAccountCreate,
    request: Request,
    current_user: dict[str, Any] = Depends(require_permission("channels.manage")),
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
    current_user: dict[str, Any] = Depends(require_permission("channels.manage")),
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
    current_user: dict[str, Any] = Depends(require_permission("channels.manage")),
):
    company_id = auth_service.resolve_company_id(current_user)
    previous = channel_account_service.get_account(company_id, account_id)

    if not channel_account_service.delete_account(company_id, account_id):
        raise HTTPException(status_code=404, detail="Channel account not found.")

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
