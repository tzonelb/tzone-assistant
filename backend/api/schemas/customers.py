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
