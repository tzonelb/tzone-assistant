from pydantic import BaseModel, Field


class BroadcastCreateRequest(BaseModel):
    name: str = Field(max_length=200)
    message_text: str = Field(max_length=4000)
    channel: str = Field(max_length=40)
    segment_id: int | None = None
    lifecycle_stage: str | None = Field(default=None, max_length=40)
    tag: str | None = Field(default=None, max_length=80)
    numbers: list[str] | None = None
