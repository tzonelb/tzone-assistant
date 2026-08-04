from pydantic import BaseModel, Field


class AppointmentCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = None
    customer_id: int | None = None
    assignee_user_id: int | None = None
    starts_at: str = Field(..., min_length=1, max_length=40)
    ends_at: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=300)
    status: str | None = Field(default=None, max_length=20)


class AppointmentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    customer_id: int | None = None
    assignee_user_id: int | None = None
    starts_at: str | None = Field(default=None, min_length=1, max_length=40)
    ends_at: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=300)
    status: str | None = Field(default=None, max_length=20)
    # Optimistic-concurrency token: the `updated_at` value the client last
    # loaded. When supplied, the update is rejected with 409 if the stored
    # record has since changed. Not an appointment field -- the route
    # pops it before the update.
    expected_updated_at: str | None = Field(default=None, max_length=64)
