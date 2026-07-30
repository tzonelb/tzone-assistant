from pydantic import BaseModel, Field


class CallLogCreateRequest(BaseModel):
    direction: str = Field(max_length=20)
    phone_number: str | None = Field(default=None, max_length=80)
    customer_id: int | None = None
    duration_seconds: int = 0
    status: str = Field(default="completed", max_length=20)
    notes: str | None = Field(default=None, max_length=2000)
