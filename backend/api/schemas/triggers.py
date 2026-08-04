from pydantic import BaseModel, Field


class TriggerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    trigger_type: str = Field(..., min_length=1, max_length=40)
    enabled: bool = True
    delay_minutes: int | None = Field(default=None, ge=1, le=20160)
    channel: str | None = Field(default=None, max_length=40)
    message_text: str | None = Field(default=None, max_length=4000)
    notify_team: bool = True


class TriggerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    trigger_type: str | None = Field(default=None, min_length=1, max_length=40)
    enabled: bool | None = None
    delay_minutes: int | None = Field(default=None, ge=1, le=20160)
    channel: str | None = Field(default=None, max_length=40)
    message_text: str | None = Field(default=None, max_length=4000)
    notify_team: bool | None = None
