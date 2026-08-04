from pydantic import BaseModel, Field


class CustomerUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    internal_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=320)
    language: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)
    notes: str | None = None
    # Optimistic-concurrency token: the `updated_at` value the client last
    # loaded. When supplied, the update is rejected with 409 if the stored
    # record has since changed, so two editors can't silently overwrite each
    # other. Not a customer field -- the route pops it before the update.
    expected_updated_at: str | None = Field(default=None, max_length=64)
