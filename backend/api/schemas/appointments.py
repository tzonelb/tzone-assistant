from pydantic import BaseModel, Field


class AppointmentCreateRequest(BaseModel):
    title: str = Field(max_length=200)
    scheduled_at: str = Field(max_length=40)
    customer_id: int | None = None
    employee_user_id: int | None = None
    duration_minutes: int = 30
    status: str = Field(default="scheduled", max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


class AppointmentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    scheduled_at: str | None = Field(default=None, max_length=40)
    customer_id: int | None = None
    employee_user_id: int | None = None
    duration_minutes: int | None = None
    status: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)
