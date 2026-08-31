"""The company's own record of what happened in its workspace.

Of seventeen modules, three wrote any audit at all — and two of those had no
endpoint to read it back, so the trail existed and nobody could see it. This is
the endpoint that was missing.

Behind `settings.view`, which is the permission an owner and their
administrators hold. The log names who did what: giving it to everybody with
`dashboard.view` would turn it into a surveillance feed of one's colleagues, and
the owner decides who sees that by deciding who holds the permission.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.services.activity_service import KINDS, activity_service
from backend.services.auth_service import auth_service, require_permission


router = APIRouter(prefix="/api/activity", tags=["Activity Log"])


def _company(current_user: dict[str, Any]) -> int:
    # Always the caller's own company, taken from the session and never from a
    # parameter. A log is the last thing that should accept a company id from
    # the person reading it.
    return auth_service.resolve_company_id(current_user)


@router.get("")
def list_activity(
    kind: str | None = Query(default=None),
    category: str | None = Query(default=None, max_length=40),
    action: str | None = Query(default=None, max_length=80),
    actor_user_id: int | None = Query(default=None, ge=1),
    search: str | None = Query(default=None, max_length=120),
    since: str | None = Query(default=None, max_length=40),
    until: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict[str, Any] = Depends(require_permission("settings.view")),
):
    return activity_service.list_entries(
        company_id=_company(current_user),
        kind=kind if kind in KINDS else None,
        category=category,
        action=action,
        actor_user_id=actor_user_id,
        search=search,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get("/options")
def activity_options(
    current_user: dict[str, Any] = Depends(require_permission("settings.view")),
):
    """The filters this company's log can offer.

    Built from what is actually in the table: a dropdown listing thirty actions
    a company has never performed is a dropdown nobody can use.
    """
    return activity_service.options(_company(current_user))


# ---------------------------------------------------------------- the detail
#
# The log above says a settings section or a customer changed, and which keys.
# It deliberately never carries the values: a settings section is an open bag
# and a customer field is somebody's phone number, and the log is read by
# everyone holding `settings.view`.
#
# The values were being written all along, to `company_setting_audit` and
# `customer_audit`, and no endpoint had ever read either. These two are that
# reader. Each sits behind the permission that already guards the thing it
# describes rather than behind `settings.view`, because the values are the
# sensitive half: whoever may see a customer's phone number may see what it was
# before, and nobody else.


@router.get("/settings/{section}/history")
def settings_history(
    section: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict[str, Any] = Depends(require_permission("settings.manage")),
):
    """What one settings section held before and after each change.

    `settings.manage` rather than `settings.view`: reading the old value of a
    setting is closer to being able to change it than to being able to look at
    the current one.
    """
    return {
        "section": section,
        "items": activity_service.settings_history(
            company_id=_company(current_user), section=section, limit=limit
        ),
    }


@router.get("/customers/{customer_id}/history")
def customer_history(
    customer_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict[str, Any] = Depends(require_permission("customers.view")),
):
    """What changed on one customer's record, and what it was changed to."""
    return {
        "customer_id": customer_id,
        "items": activity_service.customer_history(
            company_id=_company(current_user), customer_id=customer_id, limit=limit
        ),
    }
