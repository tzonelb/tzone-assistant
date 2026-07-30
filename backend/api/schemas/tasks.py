from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    title: str = Field(max_length=200)
    description: str | None = None
    priority: str = Field(default="normal", max_length=20)
    assigned_user_id: int | None = None
    customer_id: int | None = None
    due_at: str | None = Field(default=None, max_length=40)


class TaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    priority: str | None = Field(default=None, max_length=20)
    assigned_user_id: int | None = None
    customer_id: int | None = None
    due_at: str | None = Field(default=None, max_length=40)
    status: str | None = None
