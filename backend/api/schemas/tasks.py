from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    title: str = Field(max_length=200)
    description: str | None = None
    task_type: str = Field(default="other", max_length=20)
    priority: str = Field(default="normal", max_length=20)
    assigned_user_id: int | None = None
    customer_id: int | None = None
    conversation_id: int | None = None
    due_at: str | None = Field(default=None, max_length=40)


class TaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    task_type: str | None = Field(default=None, max_length=20)
    priority: str | None = Field(default=None, max_length=20)
    assigned_user_id: int | None = None
    customer_id: int | None = None
    due_at: str | None = Field(default=None, max_length=40)
    status: str | None = None
