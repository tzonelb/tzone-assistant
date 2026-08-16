"""AI TEACHING — what this company's assistant is told, and a way to try it.

Three things are edited here: the assistant's profile (tone, instructions,
welcome, taught examples), the company's business departments — the sections its
customers are offered as a menu and as quick-reply buttons — and the company's
reply policy, which is how it answers on each channel.

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
    BusinessDepartmentCreate,
    BusinessDepartmentReorder,
    BusinessDepartmentUpdate,
    DryRunRequest,
    ReplyPolicyUpdate,
)
from backend.services.auth_service import auth_service, require_permission
from backend.services.bot_profile_service import (
    PREVIEW_CHANNELS,
    SUGGESTED_TONES,
    BotProfileError,
    bot_profile_service,
)
from backend.services.business_department_service import (
    BusinessDepartmentError,
    business_department_service,
)
from backend.services.reply_policy_service import (
    ReplyPolicyError,
    reply_policy_service,
)
from core.response_policy import response_policy


router = APIRouter(prefix="/api/ai-teaching", tags=["AI Teaching"])


def _company_id(current_user: dict[str, Any]) -> int:
    return auth_service.resolve_company_id(current_user)


def view_context(current_user=Depends(require_permission("settings.view"))) -> int:
    return _company_id(current_user)


def manage_context(current_user=Depends(require_permission("settings.manage"))) -> int:
    return _company_id(current_user)


def manage_actor(
    current_user=Depends(require_permission("settings.manage")),
) -> dict[str, Any]:
    """The same gate as ``manage_context``, plus who is doing it.

    The reply policy is stored through ``company_settings_service``, which keeps
    a change audit; an audit row with no actor answers "what changed" and not
    "who changed it".
    """
    return {
        "company_id": _company_id(current_user),
        "actor_user_id": int(current_user["id"]),
    }


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
# Business departments — the sections this company offers its customers
#
# They live here rather than behind their own permission because they are edited
# on the AI TEACHING screen and they are part of what the assistant is taught:
# the menu it shows, the buttons it offers and the department list in its
# prompt. ``settings.view`` / ``settings.manage`` is the same gate the rest of
# this screen uses, so a user who may teach the assistant may define its
# sections, rather than meeting a half-working screen.
#
# ``/departments/reorder`` is declared before ``/departments/{department_id}``
# so the literal segment is never parsed as an id.
# ----------------------------------------------------------------------


@router.get("/departments")
def list_departments(company_id: int = Depends(view_context)):
    return {
        "items": business_department_service.list_departments(company_id=company_id)
    }


@router.post("/departments", status_code=status.HTTP_201_CREATED)
def create_department(
    payload: BusinessDepartmentCreate,
    company_id: int = Depends(manage_context),
):
    try:
        return {
            "department": business_department_service.create_department(
                company_id=company_id,
                data=payload.model_dump(exclude_unset=True),
            )
        }
    except BusinessDepartmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/departments/reorder")
def reorder_departments(
    payload: BusinessDepartmentReorder,
    company_id: int = Depends(manage_context),
):
    return {
        "items": business_department_service.reorder(
            company_id=company_id,
            department_ids=payload.department_ids,
        )
    }


@router.get("/departments/{department_id}")
def get_department(department_id: int, company_id: int = Depends(view_context)):
    department = business_department_service.get_department(
        company_id=company_id, department_id=department_id
    )

    if not department:
        raise HTTPException(status_code=404, detail="Department not found.")

    return {"department": department}


@router.put("/departments/{department_id}")
def update_department(
    department_id: int,
    payload: BusinessDepartmentUpdate,
    company_id: int = Depends(manage_context),
):
    try:
        department = business_department_service.update_department(
            company_id=company_id,
            department_id=department_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except BusinessDepartmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not department:
        raise HTTPException(status_code=404, detail="Department not found.")

    return {"department": department}


@router.delete("/departments/{department_id}")
def delete_department(
    department_id: int,
    company_id: int = Depends(manage_context),
):
    if not business_department_service.delete_department(
        company_id=company_id, department_id=department_id
    ):
        raise HTTPException(status_code=404, detail="Department not found.")

    return {"success": True}


# ----------------------------------------------------------------------
# The reply policy — how this company answers, per channel
#
# Same gate as the rest of this screen: ``settings.view`` to read,
# ``settings.manage`` to change. The company comes from the caller's token and
# never from the request, so one company can neither read nor write another's
# mechanism.
#
# Three routes rather than one, because clearing has to be as real as setting:
# ``PUT`` sets and clears named keys on a scope, and ``DELETE`` drops a
# channel's whole override so it inherits the company default again.
# ----------------------------------------------------------------------


def _policy_view(company_id: int) -> dict[str, Any]:
    return reply_policy_service.describe(company_id, response_policy.shipped_map())


@router.get("/reply-policy")
def get_reply_policy(company_id: int = Depends(view_context)):
    """What applies, what this company chose, and what it is inheriting."""
    return _policy_view(company_id)


@router.put("/reply-policy")
def update_reply_policy_default(
    payload: ReplyPolicyUpdate,
    actor: dict[str, Any] = Depends(manage_actor),
):
    """The company's own default, applied to every channel it has not overridden."""
    try:
        reply_policy_service.update_company_default(
            company_id=actor["company_id"],
            values=payload.values,
            clear=payload.clear,
            actor_user_id=actor["actor_user_id"],
        )
    except ReplyPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # A Super Admin lock on this section. 409 rather than 400: nothing the
        # operator typed is wrong, they are simply not the one who decides it.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _policy_view(actor["company_id"])


@router.put("/reply-policy/channels/{channel}")
def update_reply_policy_channel(
    channel: str,
    payload: ReplyPolicyUpdate,
    actor: dict[str, Any] = Depends(manage_actor),
):
    try:
        reply_policy_service.update_channel(
            company_id=actor["company_id"],
            channel=channel,
            values=payload.values,
            clear=payload.clear,
            actor_user_id=actor["actor_user_id"],
        )
    except ReplyPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # A Super Admin lock on this section. 409 rather than 400: nothing the
        # operator typed is wrong, they are simply not the one who decides it.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _policy_view(actor["company_id"])


@router.delete("/reply-policy/channels/{channel}")
def clear_reply_policy_channel(
    channel: str,
    actor: dict[str, Any] = Depends(manage_actor),
):
    """Back to inheriting the company default, with nothing frozen in place."""
    try:
        reply_policy_service.clear_channel(
            company_id=actor["company_id"],
            channel=channel,
            actor_user_id=actor["actor_user_id"],
        )
    except ReplyPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # A Super Admin lock on this section. 409 rather than 400: nothing the
        # operator typed is wrong, they are simply not the one who decides it.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _policy_view(actor["company_id"])


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
