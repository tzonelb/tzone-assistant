from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = None
    status: str | None = Field(default=None, max_length=20)
    priority: str | None = Field(default=None, max_length=20)
    assignee_user_id: int | None = None
    due_date: str | None = Field(default=None, max_length=40)
    related_customer_id: int | None = None


class TaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: str | None = Field(default=None, max_length=20)
    priority: str | None = Field(default=None, max_length=20)
    assignee_user_id: int | None = None
    due_date: str | None = Field(default=None, max_length=40)
    related_customer_id: int | None = None
    # Optimistic-concurrency token: the `updated_at` value the client last
    # loaded. When supplied, the update is rejected with 409 if the stored
    # record has since changed, so two editors can't silently overwrite each
    # other. Not a task field -- the route pops it before the update.
    expected_updated_at: str | None = Field(default=None, max_length=64)
