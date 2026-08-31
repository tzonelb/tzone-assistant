from typing import Any

from pydantic import BaseModel, Field


class NotificationResponse(BaseModel):
    id: int
    company_id: int
    recipient_user_id: int | None = None
    notification_type: str
    title: str
    body: str | None = None
    channel: str | None = None
    external_user_id: str | None = None
    conversation_id: int | None = None
    actor_user_id: int | None = None
    severity: str
    data: dict[str, Any] = Field(default_factory=dict)
    grouped_count: int = 1
    is_read: bool
    read_at: str | None = None
    created_at: str


class NotificationSummaryResponse(BaseModel):
    total: int
    unread: int
    read: int


class NotificationReadStateRequest(BaseModel):
    notification_ids: list[int] = Field(default_factory=list, max_length=1000)


class NotificationClearRequest(BaseModel):
    notification_ids: list[int] = Field(default_factory=list, max_length=1000)
