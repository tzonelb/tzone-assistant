"""Request and response shapes for the Super Admin control plane.

These models carry no company data by design. The platform console creates and
configures companies; it never reads what is inside one, so nothing here has a
field for a conversation, a customer or a message.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


# ----------------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------------


class PlatformLoginRequest(BaseModel):
    """A platform login asks for no workspace code, and that is deliberate.

    A platform session never opens a company database, so there is nothing for a
    code to unlock. Accepting one here would suggest otherwise.
    """

    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    # The six-digit code, or a recovery code. Optional in the schema and
    # required by the endpoint once the account is enrolled: the client asks
    # for it only after the first response says it is needed, so a caller who
    # does not know whether an account has a second factor learns nothing from
    # the shape of the request.
    totp_code: str | None = Field(default=None, max_length=32)


class PlatformLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    scope: str
    expires_in: int
    user: dict
    # Whether this administrator still has to enrol. The session is minted
    # either way and every other console route refuses until they do, so the
    # console needs this to route them to enrolment rather than to a dashboard
    # that will 403 on every request it makes.
    totp: dict | None = None
    # The double-submit partner to the session cookie. Returned in the body so
    # a client does not have to parse cookies to find it; the cookie copy is
    # what makes the comparison possible.
    csrf_token: str | None = None


class PlatformUserResponse(BaseModel):
    user: dict
    scope: str


class PlatformLogoutResponse(BaseModel):
    success: bool
    message: str


# ----------------------------------------------------------------------
# Companies
# ----------------------------------------------------------------------


class CompanyCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=120)
    workspace: str = Field(min_length=2, max_length=120)

    owner_email: EmailStr
    owner_name: str = Field(min_length=2, max_length=120)
    # Matches AuthService.MIN_PASSWORD_LENGTH, so a password this accepts is
    # never rejected later by the hasher.
    owner_password: str = Field(min_length=10, max_length=200)

    country: str | None = Field(default=None, max_length=60)
    currency: str = Field(default="USD", max_length=10)
    timezone: str = Field(default="Asia/Beirut", max_length=60)
    language: str = Field(default="ar", max_length=10)
    plan_code: str | None = Field(default=None, max_length=40)


class CompanyStatusRequest(BaseModel):
    status: Literal["active", "suspended"]
    reason: str | None = Field(default=None, max_length=500)


class PlanAssignRequest(BaseModel):
    plan_code: str = Field(min_length=2, max_length=40)
    # ISO date or timestamp; empty means the plan does not expire.
    expires_at: str | None = Field(default=None, max_length=40)


class ActivationCodeMintRequest(BaseModel):
    """Ask the platform to mint one activation code.

    Every field is optional. A code with no plan lifts the demonstration and
    leaves the plan to be chosen later -- what a code handed out at a trade
    show does. `expires_at` is an ISO timestamp; empty means it never lapses.
    """

    plan_id: int | None = None
    note: str | None = Field(default=None, max_length=200)
    expires_at: str | None = Field(default=None, max_length=40)


class PlatformConfigUpdate(BaseModel):
    """A partial edit. An omitted section is left exactly as it was.

    The values are validated against the platform's real module keys, branding
    fields and layout flags in the service, not here: the service owns that list
    and is also reachable from the CLI and from tests.
    """

    modules: dict[str, bool] | None = None
    branding: dict[str, Any] | None = None
    layout: dict[str, bool] | None = None


class PlanCreateRequest(BaseModel):
    """A new plan.

    The numbers are validated in the service rather than here — the same
    validation has to hold for the CLI and for tests, and duplicating it is how
    two rules that were meant to be one drift apart.
    """

    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    values: dict[str, Any] = Field(default_factory=dict)


class PlanUpdateRequest(BaseModel):
    """A partial edit. `code` is deliberately absent: every subscription points
    at a plan by code, so renaming one would move every company on it onto a
    plan that no longer exists."""

    values: dict[str, Any] = Field(default_factory=dict)


class PlanOverrideRequest(BaseModel):
    """One company's departure from its plan's allowance.

    A note rather than a free hand: an override with no reason is a number
    nobody can review later, and reviewing them is the point of keeping them
    out of the plan row.
    """

    value: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=500)


class TotpConfirmRequest(BaseModel):
    """The code from the authenticator app, proving the secret arrived.

    Six digits, but not validated as such here: `pyotp` decides what a valid
    code is, and a length rule in the schema would answer a wrong code with a
    different error than an invalid one — which is a way to probe the account.
    """

    code: str = Field(min_length=4, max_length=32)


class SettingOverrideRequest(BaseModel):
    """Pin a company's setting, lock it, or both.

    `value` and `is_locked` are independent: an operator may lock a company to
    whatever it has already chosen without deciding the value for them, or
    correct a value without taking the control away. `value` omitted leaves any
    existing pin untouched — `None` is a legitimate thing to pin, so it cannot
    double as "leave it alone".
    """

    section: str = Field(min_length=2, max_length=60)
    setting_key: str = Field(min_length=1, max_length=80)
    value: Any = None
    set_value: bool = False
    is_locked: bool | None = None
    note: str | None = Field(default=None, max_length=500)
