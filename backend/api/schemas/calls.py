"""Request bodies for the call log and the dialer.

Neither carries a ``company_id`` or the id of whoever is calling. Both are
resolved from the caller's session in the router, so a client cannot log a call
into another company's history or attribute one to a colleague by editing a
payload.

The bounds here are the first refusal, not the only one: `call_log_service`
validates direction, outcome and duration again, because the Dialer writes into
the same log without passing through this schema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


MAX_DIRECTION = 20
MAX_PHONE = 80
MAX_STATUS = 20
MAX_NOTES = 2000

# A day. Long enough for any real call and short enough that a typo in the
# minutes box cannot store a number the duration column will render as
# nonsense.
MAX_DURATION_SECONDS = 24 * 60 * 60


class CallLogCreateRequest(BaseModel):
    direction: str = Field(max_length=MAX_DIRECTION)
    phone_number: str | None = Field(default=None, max_length=MAX_PHONE)
    customer_id: int | None = Field(default=None, ge=1)
    duration_seconds: int = Field(default=0, ge=0, le=MAX_DURATION_SECONDS)
    status: str = Field(default="completed", max_length=MAX_STATUS)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)


class PlaceCallRequest(BaseModel):
    to_number: str = Field(min_length=3, max_length=MAX_PHONE)
    customer_id: int | None = Field(default=None, ge=1)


class TransferCallRequest(BaseModel):
    employee_user_id: int = Field(ge=1)
