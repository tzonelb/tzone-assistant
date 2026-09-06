"""The call history screen.

The company is taken from the caller's session and never from the request, the
same rule every router here follows: a handler that trusted a client-supplied
company id would hand one company the key to another company's call history,
which is exactly what a phone number list is.

Permissions follow the conversations they belong to. Reading the history is
`conversations.view` and recording a call is `conversations.reply` — logging a
call is the same act as answering a customer, done on the phone instead of in
the inbox, and an agent who may do one may do the other. Deleting is
`settings.manage`, because removing a record of a customer contact is a
record-keeping decision rather than a day's work.

Employee names are resolved once per response through
`auth_service.user_display_names`: calls live in the company's encrypted
database and users live in the control plane, so there is no join to make.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status

from backend.api.schemas.calls import CallLogCreateRequest
from backend.services.auth_service import (
    auth_service,
    get_current_user,
    require_permission,
)
from backend.services.call_log_service import (
    DIRECTIONS,
    STATUSES,
    CallLogNotFound,
    CustomerNotFound,
    call_log_service,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calls", tags=["Calls"])


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("conversations.view"))):
    return _context(current_user)


def log_context(current_user=Depends(require_permission("conversations.reply"))):
    return _context(current_user)


def delete_context(current_user=Depends(require_permission("settings.manage"))):
    return _context(current_user)


def _with_names(company_id: int, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = [
        int(item["called_by_user_id"])
        for item in items
        if item.get("called_by_user_id") is not None
    ]
    names = auth_service.user_display_names(company_id, ids) if ids else {}

    for item in items:
        logged_by = item.get("called_by_user_id")
        item["called_by_name"] = (
            names.get(int(logged_by), f"User {logged_by}") if logged_by else None
        )

    return items


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------


@router.get("/options")
def call_options(current_user=Depends(get_current_user)):
    """The vocabulary the form is built from.

    Two constant lists and nothing about any company, which is why this asks
    only that the caller is signed in: the screen needs them to draw its
    dropdowns before it knows whether the employee may read anything.
    """
    return {"directions": list(DIRECTIONS), "statuses": list(STATUSES)}


@router.get("")
def list_call_logs(
    customer_id: int | None = Query(default=None, ge=1),
    direction: str | None = Query(default=None, max_length=20),
    status: str | None = Query(default=None, max_length=20),
    context=Depends(view_context),
):
    _current_user, company_id = context

    result = call_log_service.list_call_logs(
        company_id=company_id,
        customer_id=customer_id,
        direction=direction,
        status=status,
    )
    result["items"] = _with_names(company_id, result["items"])

    return result


@router.post("", status_code=http_status.HTTP_201_CREATED)
def create_call_log(payload: CallLogCreateRequest, context=Depends(log_context)):
    current_user, company_id = context

    try:
        call = call_log_service.create_call_log(
            company_id=company_id,
            direction=payload.direction,
            phone_number=payload.phone_number,
            customer_id=payload.customer_id,
            duration_seconds=payload.duration_seconds,
            status=payload.status,
            notes=payload.notes,
            actor_user_id=current_user.get("id"),
        )
    except CustomerNotFound as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return _with_names(company_id, [call])[0]


@router.delete("/{call_id}")
def delete_call_log(call_id: int, context=Depends(delete_context)):
    _current_user, company_id = context

    try:
        call_log_service.delete_call_log(company_id=company_id, call_id=call_id)
    except CallLogNotFound as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    return {"deleted": True}
