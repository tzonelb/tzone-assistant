"""Request bodies for the AI TEACHING API.

None of these carry a ``company_id``: the company is resolved from the caller's
token in the router, so a client cannot name a company it does not belong to.

The length limits are real limits, not decoration. Every field here is
serialized into the system prompt on every customer message, so unbounded text
is both a cost problem and a way to push the safety rules out of the model's
attention.
"""

from __future__ import annotations

from typing import Any, Literal

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
from backend.services.ai_teaching_chat_service import MAX_TEACHING_MESSAGE
from backend.services.business_department_service import (
    MAX_BUTTON,
    MAX_CODE,
    MAX_NAME as MAX_DEPARTMENT_NAME,
)


Language = Literal["ar", "en"]
ProfileStatus = Literal["active", "disabled"]

# Kept in step with ``bot_profile_service.PREVIEW_CHANNELS``; spelled out here
# so FastAPI can document the accepted values.
# `website_chat` was here and is gone: it has no routing field, no webhook and
# no sender, so accepting it meant the API documented a preview it could never
# give. Written out rather than derived because `Literal` needs constants at
# class-definition time; `tests/test_channel_catalogue.py` compares it to the
# one catalogue so the two cannot drift.
PreviewChannel = Literal[
    "messenger",
    "instagram",
    "whatsapp",
    "telegram",
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


class TeachingMessageCreate(BaseModel):
    """One thing a manager says to the assistant in the training chat.

    Bounded for the same reason every field above is: whatever the model
    extracts from this is appended to the system prompt and then travels with
    every customer message for as long as it is stored.
    """

    text: str = Field(min_length=1, max_length=MAX_TEACHING_MESSAGE)


# ----------------------------------------------------------------------
# Business departments — the sections a company offers its customers
# ----------------------------------------------------------------------


class BusinessDepartmentCreate(BaseModel):
    """One section of this company's business.

    ``code`` is normalised by the service to lowercase ascii: it is what the
    assistant routes on and what the session stores, so it is not free text
    even though the names above it are.
    """

    code: str = Field(min_length=1, max_length=MAX_CODE)
    name_ar: str | None = Field(default=None, max_length=MAX_DEPARTMENT_NAME)
    name_en: str | None = Field(default=None, max_length=MAX_DEPARTMENT_NAME)
    button_ar: str | None = Field(default=None, max_length=MAX_BUTTON)
    button_en: str | None = Field(default=None, max_length=MAX_BUTTON)
    enabled: bool = True
    sort_order: int | None = Field(default=None, ge=0, le=9999)


class BusinessDepartmentUpdate(BaseModel):
    """Every field optional: the screen sends only what the row changed."""

    code: str | None = Field(default=None, min_length=1, max_length=MAX_CODE)
    name_ar: str | None = Field(default=None, max_length=MAX_DEPARTMENT_NAME)
    name_en: str | None = Field(default=None, max_length=MAX_DEPARTMENT_NAME)
    button_ar: str | None = Field(default=None, max_length=MAX_BUTTON)
    button_en: str | None = Field(default=None, max_length=MAX_BUTTON)
    enabled: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=9999)


class BusinessDepartmentReorder(BaseModel):
    """The ids in the order the menu should be shown in."""

    department_ids: list[int] = Field(default_factory=list, max_length=200)


# ----------------------------------------------------------------------
# The reply policy — how this company answers, per channel
# ----------------------------------------------------------------------


class ReplyPolicyUpdate(BaseModel):
    """Set some settings on one scope, clear others back to inheriting.

    The values are typed ``Any`` on purpose: what a key may hold is decided by
    ``reply_policy_service`` — which is also the gate on
    ``/api/company-settings/reply_policy`` — so the rules live in one place
    rather than being restated here and drifting. A key it does not recognise,
    a mode that is not a real mode and a confidence outside 0..1 are refused
    with a message that says which, rather than stored.

    ``clear`` is what makes an override removable. Without it a channel row
    could show a value with no way back to inheriting, which is a control that
    looks like a decision and is really a copy.
    """

    values: dict[str, Any] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list, max_length=32)
