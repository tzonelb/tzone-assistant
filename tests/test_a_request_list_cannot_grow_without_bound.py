"""Request lists that become SQL bind parameters must be bounded.

`member_user_ids`, `permission_codes` and `notification_ids` are each expanded
into an `IN (?, ?, ...)` clause. Left unbounded, a caller could send hundreds of
thousands of ids: at best a several-hundred-KB error body echoing the whole list
back (amplification), at worst `too many SQL variables` -> a 500 that also breaks
the platform's own "no endpoint answers with a crash" invariant. Each list now
declares a `max_length`, so an oversized body is refused at validation with 422
before any query is built.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.schemas.notifications import (
    NotificationClearRequest,
    NotificationReadStateRequest,
)
from backend.api.schemas.roles import RoleCreateRequest, RoleUpdateRequest
from backend.api.schemas.team_chat import (
    MAX_CHANNEL_MEMBERS,
    ChannelCreateRequest,
)


def test_channel_membership_is_capped():
    ChannelCreateRequest(name="ok", member_user_ids=list(range(MAX_CHANNEL_MEMBERS)))
    with pytest.raises(ValidationError):
        ChannelCreateRequest(
            name="ok", member_user_ids=list(range(MAX_CHANNEL_MEMBERS + 1))
        )


def test_permission_code_lists_are_capped():
    RoleCreateRequest(name="Role", code="role", permission_codes=["x"] * 200)
    with pytest.raises(ValidationError):
        RoleCreateRequest(name="Role", code="role", permission_codes=["x"] * 201)
    with pytest.raises(ValidationError):
        RoleUpdateRequest(permission_codes=["x"] * 201)


def test_notification_id_lists_are_capped():
    NotificationReadStateRequest(notification_ids=list(range(1000)))
    with pytest.raises(ValidationError):
        NotificationReadStateRequest(notification_ids=list(range(1001)))
    with pytest.raises(ValidationError):
        NotificationClearRequest(notification_ids=list(range(1001)))


def test_a_quarter_million_ids_is_refused_not_executed():
    """The concrete crash the fix prevents: a list large enough to blow past
    SQLite's bind-parameter limit is rejected at the door."""
    with pytest.raises(ValidationError):
        ChannelCreateRequest(name="ok", member_user_ids=list(range(250_000)))
