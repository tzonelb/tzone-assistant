"""Request bodies for the AI TEACHING API.

None of these carry a ``company_id``: the company is resolved from the caller's
token in the router, so a client cannot name a company it does not belong to.

The length limits are real limits, not decoration. Every field here is
serialized into the system prompt on every customer message, so unbounded text
is both a cost problem and a way to push the safety rules out of the model's
attention.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.services.bot_profile_service import (
    MAX_EXAMPLE_FIELD,
    MAX_EXAMPLES,
    MAX_MODEL,
    MAX_NAME,
    MAX_SYSTEM_PROMPT,
    MAX_TONE,
    MAX_WELCOME,
)


Language = Literal["ar", "en"]
ProfileStatus = Literal["active", "disabled"]

# Kept in step with ``bot_profile_service.PREVIEW_CHANNELS``; spelled out here
# so FastAPI can document the accepted values.
PreviewChannel = Literal[
    "messenger",
    "instagram",
    "whatsapp",
    "telegram",
    "website_chat",
]

MAX_TEST_MESSAGE = 2000


class TeachingExample(BaseModel):
    """One "when a customer says this, answer like this" pair."""

    customer: str = Field(min_length=1, max_length=MAX_EXAMPLE_FIELD)
    reply: str = Field(min_length=1, max_length=MAX_EXAMPLE_FIELD)


class BotProfileUpdate(BaseModel):
    """Every field optional: the screen sends only what the form changed.

    The router serializes these with ``exclude_unset``, so a field left out of
    the request keeps its stored value instead of being blanked to ``None``.
    """

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    tone: str | None = Field(default=None, max_length=MAX_TONE)
    default_language: Language | None = None
    system_prompt: str | None = Field(default=None, max_length=MAX_SYSTEM_PROMPT)
    welcome_enabled: bool | None = None
    welcome_message_ar: str | None = Field(default=None, max_length=MAX_WELCOME)
    welcome_message_en: str | None = Field(default=None, max_length=MAX_WELCOME)
    examples: list[TeachingExample] | None = Field(default=None, max_length=MAX_EXAMPLES)
    ai_enabled: bool | None = None
    ai_model: str | None = Field(default=None, max_length=MAX_MODEL)
    memory_enabled: bool | None = None
    human_handover_enabled: bool | None = None
    status: ProfileStatus | None = None


class BotProfileCreate(BotProfileUpdate):
    """An additional profile, usually bound to one connected channel account."""

    name: str = Field(min_length=1, max_length=MAX_NAME)
    channel_account_id: int | None = Field(default=None, ge=1)


class BotProfileBindingUpdate(BotProfileUpdate):
    """Updating a non-default profile may also move its channel binding."""

    channel_account_id: int | None = Field(default=None, ge=1)


class DryRunRequest(BaseModel):
    """A message to try against the assistant without any customer involved."""

    message: str = Field(min_length=1, max_length=MAX_TEST_MESSAGE)
    channel: PreviewChannel = "messenger"
    language: Language | None = None
