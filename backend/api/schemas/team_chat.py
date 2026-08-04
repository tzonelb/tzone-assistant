from pydantic import BaseModel, Field


class RoomCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=300)


class MessagePostRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
