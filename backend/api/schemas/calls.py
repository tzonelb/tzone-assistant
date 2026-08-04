from pydantic import BaseModel, Field


class CallCreateRequest(BaseModel):
    customer_id: int | None = None
    phone_number: str | None = Field(default=None, max_length=40)
    direction: str | None = Field(default=None, max_length=20)
    outcome: str | None = Field(default=None, max_length=20)
    duration_seconds: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)
    called_at: str = Field(..., min_length=1, max_length=40)


class CallUpdateRequest(BaseModel):
    customer_id: int | None = None
    phone_number: str | None = Field(default=None, max_length=40)
    direction: str | None = Field(default=None, max_length=20)
    outcome: str | None = Field(default=None, max_length=20)
    duration_seconds: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)
    called_at: str | None = Field(default=None, min_length=1, max_length=40)
    # Optimistic-concurrency token: the `updated_at` value the client last
    # loaded. When supplied, the update is rejected with 409 if the stored
    # record has since changed. Not a call field -- the route pops it
    # before the update.
    expected_updated_at: str | None = Field(default=None, max_length=64)
