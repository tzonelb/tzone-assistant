from pydantic import BaseModel, Field


class TeamMessageCreateRequest(BaseModel):
    text: str = Field(max_length=4000)
    mentioned_user_ids: list[int] = Field(default_factory=list)
