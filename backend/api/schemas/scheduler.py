from pydantic import BaseModel, Field


class ScheduledPostCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., min_length=1)
    channel: str = Field(..., min_length=1, max_length=60)
    media_url: str | None = Field(default=None, max_length=1000)
    scheduled_at: str | None = Field(default=None, max_length=40)


class ScheduledPostUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1)
    channel: str | None = Field(default=None, min_length=1, max_length=60)
    media_url: str | None = Field(default=None, max_length=1000)
    scheduled_at: str | None = Field(default=None, max_length=40)
    # Optimistic-concurrency token: the `updated_at` value the client last
    # loaded. When supplied, the update is rejected with 409 if the stored
    # record has since changed. Not a post field -- the route pops it
    # before the update.
    expected_updated_at: str | None = Field(default=None, max_length=64)


class ScheduledPostStatusRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=20)
    expected_updated_at: str | None = Field(default=None, max_length=64)
