"""AI TEACHING — what this company's assistant is told, and a way to try it.

Reads require ``settings.view``; every write requires ``settings.manage``. The
dry run is also behind ``settings.manage``: it is not a write, but it runs the
whole assistant and can spend money on a model call, so it belongs with the
people who are allowed to change the assistant rather than with everyone who
can look at it.

The company is never taken from the request body or the query string — it comes
from the caller's token, so a profile can only ever be read or edited by the
company that owns it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.schemas.ai_teaching import (
    BotProfileBindingUpdate,
    BotProfileCreate,
    BotProfileUpdate,
    DryRunRequest,
)
from backend.services.auth_service import auth_service, require_permission
from backend.services.bot_profile_service import (
    PREVIEW_CHANNELS,
    SUGGESTED_TONES,
    BotProfileError,
    bot_profile_service,
)


router = APIRouter(prefix="/api/ai-teaching", tags=["AI Teaching"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


def view_context(current_user=Depends(require_permission("settings.view"))) -> int:
    return _company_id(current_user)


def manage_context(current_user=Depends(require_permission("settings.manage"))) -> int:
    return _company_id(current_user)


def _payload(model: BotProfileUpdate) -> dict[str, Any]:
    """Only the fields the form actually sent.

    ``exclude_unset`` matters: without it every untouched field would arrive as
    ``None`` and wipe the stored value.
    """
    data = model.model_dump(exclude_unset=True)

    if "examples" in data and data["examples"] is not None:
        data["examples"] = [
            {"customer": item["customer"], "reply": item["reply"]}
            for item in data["examples"]
        ]

    return data


# ----------------------------------------------------------------------
# The default profile — what the screen opens on
# ----------------------------------------------------------------------


@router.get("/profile")
def get_profile(company_id: int = Depends(view_context)):
    """The company's default profile, created with working defaults if absent."""
    return {
        "profile": bot_profile_service.get_default(company_id),
        "tones": list(SUGGESTED_TONES),
        "channels": list(PREVIEW_CHANNELS),
    }


@router.put("/profile")
def update_profile(
    payload: BotProfileUpdate,
    company_id: int = Depends(manage_context),
):
    try:
        return {
            "profile": bot_profile_service.update_default(
                company_id=company_id,
                values=_payload(payload),
            )
        }
    except BotProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/profile/prompt")
def get_composed_prompt(
    channel: str = "messenger",
    company_id: int = Depends(view_context),
):
    """The exact system prompt this company's assistant is given.

    Shown in the screen so an owner can see that their instructions really are
    what the assistant is told, rather than trusting that they are.
    """
    from core.prompt_builder import prompt_builder

    return {
        "channel": channel,
        "prompt": prompt_builder.build_system_prompt(channel, company_id=company_id),
    }


# ----------------------------------------------------------------------
# Additional profiles, bound to a connected channel account
# ----------------------------------------------------------------------


@router.get("/profiles")
def list_profiles(company_id: int = Depends(view_context)):
    return {"items": bot_profile_service.list_profiles(company_id)}


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: BotProfileCreate,
    company_id: int = Depends(manage_context),
):
    try:
        return {
            "profile": bot_profile_service.create_profile(
                company_id=company_id,
                values=_payload(payload),
            )
        }
    except BotProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/profiles/{profile_id}")
def get_one_profile(profile_id: int, company_id: int = Depends(view_context)):
    profile = bot_profile_service.get_profile(company_id, profile_id)

    if not profile:
        raise HTTPException(status_code=404, detail="Assistant profile not found.")

    return {"profile": profile}


@router.put("/profiles/{profile_id}")
def update_one_profile(
    profile_id: int,
    payload: BotProfileBindingUpdate,
    company_id: int = Depends(manage_context),
):
    try:
        return {
            "profile": bot_profile_service.update_profile(
                company_id=company_id,
                profile_id=profile_id,
                values=_payload(payload),
            )
        }
    except BotProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/profiles/{profile_id}")
def delete_one_profile(
    profile_id: int,
    company_id: int = Depends(manage_context),
):
    try:
        deleted = bot_profile_service.delete_profile(company_id, profile_id)
    except BotProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Assistant profile not found.")

    return {"success": True}


# ----------------------------------------------------------------------
# Dry run
# ----------------------------------------------------------------------


@router.post("/dry-run")
def dry_run(
    payload: DryRunRequest,
    company_id: int = Depends(manage_context),
):
    """Run the real assistant on a typed message and return what it would say.

    Nothing is delivered to a channel provider, no message or conversation is
    stored, no reply is queued, and no live conversation state is touched — see
    ``bot_profile_service.preview_reply`` for how each of those is guaranteed.
    """
    try:
        return bot_profile_service.preview_reply(
            company_id=company_id,
            message=payload.message,
            channel=payload.channel,
            language=payload.language,
        )
    except BotProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
