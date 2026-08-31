"""Request bodies for the appointments API.

`company_id` deliberately appears in none of these models. The company an
appointment belongs to is resolved from the caller's session by
`auth_service.resolve_company_id`; accepting it from the client would let one
company write into another's calendar simply by editing a JSON field.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AppointmentCreateRequest(BaseModel):
    staff_user_id: int = Field(ge=1)
    starts_at: str = Field(min_length=8, max_length=64)
    ends_at: str = Field(min_length=8, max_length=64)
    title: str = Field(default="Appointment", min_length=1, max_length=200)
    customer_id: int | None = Field(default=None, ge=1)
    conversation_id: int | None = Field(default=None, ge=1)
    branch_id: int | None = Field(default=None, ge=1)
    notes: str | None = Field(default=None, max_length=4000)
    status: Literal["scheduled", "confirmed"] = "scheduled"


class AppointmentRescheduleRequest(BaseModel):
    starts_at: str = Field(min_length=8, max_length=64)
    ends_at: str = Field(min_length=8, max_length=64)
    staff_user_id: int | None = Field(default=None, ge=1)


class AppointmentCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AppointmentStatusRequest(BaseModel):
    status: Literal["scheduled", "confirmed", "completed", "no_show"]


class AvailabilityRuleCreateRequest(BaseModel):
    staff_user_id: int = Field(ge=1)
    weekday: int = Field(ge=0, le=6, description="0 = Monday ... 6 = Sunday")
    start_time: str = Field(min_length=3, max_length=8, description="HH:MM, UTC")
    end_time: str = Field(min_length=3, max_length=8, description="HH:MM, UTC")
    slot_minutes: int = Field(default=30, ge=5, le=480)
    status: Literal["active", "inactive"] = "active"


class AvailabilityRuleUpdateRequest(BaseModel):
    staff_user_id: int | None = Field(default=None, ge=1)
    weekday: int | None = Field(default=None, ge=0, le=6)
    start_time: str | None = Field(default=None, min_length=3, max_length=8)
    end_time: str | None = Field(default=None, min_length=3, max_length=8)
    slot_minutes: int | None = Field(default=None, ge=5, le=480)
    status: Literal["active", "inactive"] | None = None
