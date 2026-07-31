from pydantic import BaseModel, Field


class ScheduledPostCreateRequest(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    channel_account_ids: list[int] = Field(default_factory=list)
    media_urls: list[str] = Field(default_factory=list)
    media_type: str | None = Field(default=None, max_length=20)
    content_overrides: dict[str, str] = Field(default_factory=dict)
    channel_post_types: dict[str, str] = Field(default_factory=dict)
    status: str = Field(default="draft", max_length=20)
    scheduled_at: str | None = Field(default=None, max_length=40)


class ScheduledPostUpdateRequest(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    channel_account_ids: list[int] | None = None
    media_urls: list[str] | None = None
    media_type: str | None = Field(default=None, max_length=20)
    content_overrides: dict[str, str] | None = None
    channel_post_types: dict[str, str] | None = None
    status: str | None = Field(default=None, max_length=20)
    scheduled_at: str | None = Field(default=None, max_length=40)
