"""Request bodies for the knowledge API.

None of these carry a ``company_id``. The company is resolved from the caller's
token in the router, so a client cannot name a company it does not belong to.

The length limits are real limits, not decoration: every field here is
serialized into an OpenAI prompt on every customer message, so unbounded text is
both a cost problem and a way to push the real instructions out of the model's
attention.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


KnowledgeStatus = Literal["active", "draft", "archived"]

MAX_CONTENT = 8000
MAX_TITLE = 200
MAX_KEYWORDS = 1000
MAX_DEPARTMENT = 60
MAX_EXTERNAL_ID = 120
MAX_CATEGORY_NAME = 120


class KnowledgeItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    content_ar: str | None = Field(default=None, max_length=MAX_CONTENT)
    content_en: str | None = Field(default=None, max_length=MAX_CONTENT)
    department: str | None = Field(default=None, max_length=MAX_DEPARTMENT)
    keywords: str | None = Field(default=None, max_length=MAX_KEYWORDS)
    category_id: int | None = Field(default=None, ge=1)
    external_id: str | None = Field(default=None, max_length=MAX_EXTERNAL_ID)
    status: KnowledgeStatus = "active"

    @model_validator(mode="after")
    def require_content(self) -> "KnowledgeItemCreate":
        if not (self.content_ar or "").strip() and not (self.content_en or "").strip():
            raise ValueError(
                "Add Arabic or English content. An item with neither teaches the "
                "assistant nothing."
            )

        return self


class KnowledgeItemUpdate(BaseModel):
    """Every field optional: the router sends only what the form changed."""

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    content_ar: str | None = Field(default=None, max_length=MAX_CONTENT)
    content_en: str | None = Field(default=None, max_length=MAX_CONTENT)
    department: str | None = Field(default=None, max_length=MAX_DEPARTMENT)
    keywords: str | None = Field(default=None, max_length=MAX_KEYWORDS)
    category_id: int | None = Field(default=None, ge=1)
    external_id: str | None = Field(default=None, max_length=MAX_EXTERNAL_ID)
    status: KnowledgeStatus | None = None


class KnowledgeCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_CATEGORY_NAME)
    department: str | None = Field(default=None, max_length=MAX_DEPARTMENT)
