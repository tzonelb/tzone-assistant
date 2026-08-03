from pydantic import BaseModel, Field


class TeamMessageCreateRequest(BaseModel):
    text: str = Field(default="", max_length=4000)
    mentioned_user_ids: list[int] = Field(default_factory=list)
    attachment_url: str | None = None
    attachment_type: str | None = None
    attachment_filename: str | None = None


class CreateDmRequest(BaseModel):
    user_id: int


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    member_user_ids: list[int] = Field(default_factory=list)
    department: str | None = None


class RoomMessageCreateRequest(BaseModel):
    text: str = Field(default="", max_length=4000)
    mentioned_user_ids: list[int] = Field(default_factory=list)
    attachment_url: str | None = None
    attachment_type: str | None = None
    attachment_filename: str | None = None
