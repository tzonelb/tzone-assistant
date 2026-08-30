"""Request and response bodies for the team chat API.

None of these carry a ``company_id`` or an author id. Both are resolved from the
caller's token in the router, so a client cannot post as someone else or into
another company's channel by editing a payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


MAX_CHANNEL_NAME = 60
MAX_TOPIC = 300
MAX_BODY = 8000
MAX_CHANNEL_MEMBERS = 1000


class ChannelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_CHANNEL_NAME)
    topic: str | None = Field(default=None, max_length=MAX_TOPIC)
    is_private: bool = False
    member_user_ids: list[int] = Field(
        default_factory=list, max_length=MAX_CHANNEL_MEMBERS
    )


class ChannelMemberRequest(BaseModel):
    user_id: int = Field(ge=1)


class MessageCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_BODY)
    linked_conversation_id: int | None = Field(default=None, ge=1)


class MessageEditRequest(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_BODY)


class StreamMessageCreateRequest(BaseModel):
    """A message written in the composer, with whatever it carries.

    ``text`` may be empty when a file is attached — a photo with no caption is
    a message. ``mentioned_user_ids`` are the colleagues the composer's picker
    resolved as they were chosen; they are checked against this company's own
    directory before anything is stored or anybody is notified, so an id typed
    into the payload by hand names nobody.

    ``attachment_url`` must be a file this workspace already uploaded through
    ``/api/media/upload``. The router checks the prefix names this company, the
    same check ``manual_messages`` makes, so an employee cannot point a message
    at another company's upload path or at an arbitrary address.
    """

    text: str = Field(default="", max_length=MAX_BODY)
    mentioned_user_ids: list[int] = Field(
        default_factory=list, max_length=MAX_CHANNEL_MEMBERS
    )
    attachment_url: str | None = Field(default=None, max_length=500)
    attachment_type: str | None = Field(default=None, max_length=40)
    attachment_filename: str | None = Field(default=None, max_length=255)


class CreateDmRequest(BaseModel):
    user_id: int = Field(ge=1)


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_CHANNEL_NAME)
    member_user_ids: list[int] = Field(
        default_factory=list, max_length=MAX_CHANNEL_MEMBERS
    )
    department: str | None = Field(default=None, max_length=60)


class ChannelResponse(BaseModel):
    id: int
    company_id: int
    name: str
    topic: str | None = None
    is_private: bool
    is_member: bool
    created_by_user_id: int | None = None
    member_count: int = 0
    message_count: int = 0
    unread_count: int = 0
    last_message_at: str | None = None
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    id: int
    company_id: int
    channel_id: int
    author_user_id: int
    author_name: str | None = None
    body: str
    mentions: list[int] = Field(default_factory=list)
    mention_names: dict[str, str] = Field(default_factory=dict)
    linked_conversation_id: int | None = None
    edited_at: str | None = None
    created_at: str


class MessagePageResponse(BaseModel):
    items: list[MessageResponse] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False
    next_before_id: int | None = None


class ChannelMemberResponse(BaseModel):
    user_id: int
    display_name: str | None = None
    joined_at: str
    last_read_at: str | None = None


class UnreadSummaryResponse(BaseModel):
    channels: dict[str, int] = Field(default_factory=dict)
    total: int = 0


class DirectoryEntry(BaseModel):
    id: int
    display_name: str | None = None
    email: str | None = None
    role_name: str | None = None


class ReadReceiptResponse(BaseModel):
    channel_id: int
    last_read_at: str
    unread_count: int = 0


class TeamChatOverviewResponse(BaseModel):
    channels: list[ChannelResponse] = Field(default_factory=list)
    directory: list[DirectoryEntry] = Field(default_factory=list)
    current_user_id: int
    unread_total: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)
