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

import asyncio

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.schemas.ai_teaching import (
    BotProfileBindingUpdate,
    BotProfileCreate,
    BotProfileUpdate,
    BusinessDepartmentCreate,
    BusinessDepartmentReorder,
    BusinessDepartmentUpdate,
    DryRunRequest,
    ReplyPolicyUpdate,
    TeachingMessageCreate,
)
from backend.services.activity_service import Action, activity_service
from backend.services.ai_teaching_chat_service import (
    AITeachingChatError,
    ai_teaching_chat_service,
)
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
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
from config.settings import config


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

    ``user`` is the whole session user rather than only its id, because the
    activity log copies the actor's display name in at write time — see
    ``activity_service`` for why it cannot be joined back afterwards.
    """
    return {
        "company_id": _company_id(current_user),
        "actor_user_id": int(current_user["id"]),
        "user": current_user,
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
    request: Request,
    actor: dict[str, Any] = Depends(manage_actor),
):
    company_id = actor["company_id"]
    values = _payload(payload)

    try:
        profile = bot_profile_service.update_default(
            company_id=company_id,
            values=values,
        )
    except BotProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # The field names, not the instructions themselves. The prompt, the welcome
    # messages and the taught examples are the profile's own record and are
    # readable on this screen; copying them into every audit row would put a
    # full history of the assistant's script in a table with different
    # retention.
    activity_service.record_for(
        actor["user"],
        company_id=company_id,
        action=Action.BOT_PROFILE_UPDATED,
        category="ai_teaching",
        target_type="bot_profile",
        target_id=profile.get("id"),
        summary=f"Changed the assistant: {', '.join(sorted(values)) or 'no fields'}",
        after={
            "changed_fields": sorted(values),
            "ai_enabled": profile.get("ai_enabled"),
            "ai_model": profile.get("ai_model"),
            "status": profile.get("status"),
        },
        ip_address=client_ip(request),
    )

    return {"profile": profile}


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
    request: Request,
    actor: dict[str, Any] = Depends(manage_actor),
):
    company_id = actor["company_id"]
    values = _payload(payload)

    try:
        profile = bot_profile_service.update_profile(
            company_id=company_id,
            profile_id=profile_id,
            values=values,
        )
    except BotProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Recorded under the same action as the default profile: an owner asking
    # who changed the assistant means whichever profile actually answers their
    # customers, and the bound one does so on its own channel account.
    activity_service.record_for(
        actor["user"],
        company_id=company_id,
        action=Action.BOT_PROFILE_UPDATED,
        category="ai_teaching",
        target_type="bot_profile",
        target_id=profile_id,
        summary=(
            f"Changed the assistant profile {profile.get('name')}: "
            f"{', '.join(sorted(values)) or 'no fields'}"
        ),
        after={
            "changed_fields": sorted(values),
            "name": profile.get("name"),
            "channel_account_id": profile.get("channel_account_id"),
            "status": profile.get("status"),
        },
        ip_address=client_ip(request),
    )

    return {"profile": profile}


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
    request: Request,
    actor: dict[str, Any] = Depends(manage_actor),
):
    company_id = actor["company_id"]

    try:
        department = business_department_service.create_department(
            company_id=company_id,
            data=payload.model_dump(exclude_unset=True),
        )
    except BusinessDepartmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    activity_service.record_for(
        actor["user"],
        company_id=company_id,
        action=Action.DEPARTMENT_CREATED,
        category="departments",
        target_type="business_department",
        target_id=department.get("id"),
        summary=f"Added the section {department.get('code')}",
        after={
            "code": department.get("code"),
            "name_en": department.get("name_en"),
            "name_ar": department.get("name_ar"),
            "enabled": department.get("enabled"),
        },
        ip_address=client_ip(request),
    )

    return {"department": department}


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
    request: Request,
    actor: dict[str, Any] = Depends(manage_actor),
):
    company_id = actor["company_id"]

    previous = business_department_service.get_department(
        company_id=company_id, department_id=department_id
    )

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

    activity_service.record_for(
        actor["user"],
        company_id=company_id,
        action=Action.DEPARTMENT_UPDATED,
        category="departments",
        target_type="business_department",
        target_id=department_id,
        summary=f"Edited the section {department.get('code')}",
        before={
            "code": (previous or {}).get("code"),
            "name_en": (previous or {}).get("name_en"),
            "name_ar": (previous or {}).get("name_ar"),
            "enabled": (previous or {}).get("enabled"),
        },
        after={
            "code": department.get("code"),
            "name_en": department.get("name_en"),
            "name_ar": department.get("name_ar"),
            "enabled": department.get("enabled"),
        },
        ip_address=client_ip(request),
    )

    return {"department": department}


@router.delete("/departments/{department_id}")
def delete_department(
    department_id: int,
    request: Request,
    actor: dict[str, Any] = Depends(manage_actor),
):
    company_id = actor["company_id"]

    # Read before the delete: afterwards there is nothing left to name the
    # section by, and an entry saying only that department 7 was removed does
    # not tell an owner which menu their customers stopped being offered.
    previous = business_department_service.get_department(
        company_id=company_id, department_id=department_id
    )

    if not business_department_service.delete_department(
        company_id=company_id, department_id=department_id
    ):
        raise HTTPException(status_code=404, detail="Department not found.")

    activity_service.record_for(
        actor["user"],
        company_id=company_id,
        action=Action.DEPARTMENT_DELETED,
        category="departments",
        target_type="business_department",
        target_id=department_id,
        summary=(
            f"Removed the section "
            f"{(previous or {}).get('code') or department_id}"
        ),
        before={
            "code": (previous or {}).get("code"),
            "name_en": (previous or {}).get("name_en"),
            "name_ar": (previous or {}).get("name_ar"),
        },
        ip_address=client_ip(request),
    )

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


def _record_policy_change(
    actor: dict[str, Any],
    request: Request,
    *,
    scope: str,
    summary: str,
    changed: dict[str, Any],
) -> None:
    """One entry for whichever of the three policy writes was made.

    The values are recorded here, unlike the settings section this is stored
    in: a reply policy is a small set of named decisions about how the company
    answers — whether the assistant replies at all, how fast, when it hands
    over — and "who turned the assistant off on WhatsApp" is unanswerable
    without them. The service has already refused any key that is not one of
    those, so nothing arbitrary reaches this row.
    """
    activity_service.record_for(
        actor["user"],
        company_id=actor["company_id"],
        action=Action.REPLY_POLICY_UPDATED,
        category="ai_teaching",
        target_type="reply_policy",
        target_id=scope,
        summary=summary,
        after=changed,
        ip_address=client_ip(request),
    )


@router.put("/reply-policy")
def update_reply_policy_default(
    payload: ReplyPolicyUpdate,
    request: Request,
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

    _record_policy_change(
        actor,
        request,
        scope="company_default",
        summary="Changed the company's default reply policy",
        changed={"set": payload.values, "cleared": payload.clear},
    )

    return _policy_view(actor["company_id"])


@router.put("/reply-policy/channels/{channel}")
def update_reply_policy_channel(
    channel: str,
    payload: ReplyPolicyUpdate,
    request: Request,
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

    _record_policy_change(
        actor,
        request,
        scope=channel,
        summary=f"Changed the reply policy on {channel}",
        changed={"set": payload.values, "cleared": payload.clear},
    )

    return _policy_view(actor["company_id"])


@router.delete("/reply-policy/channels/{channel}")
def clear_reply_policy_channel(
    channel: str,
    request: Request,
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

    _record_policy_change(
        actor,
        request,
        scope=channel,
        summary=f"Cleared the reply policy override on {channel}",
        changed={"inherits_company_default": True},
    )

    return _policy_view(actor["company_id"])


# ----------------------------------------------------------------------
# Dry run
# ----------------------------------------------------------------------


# How many previews are in flight right now, platform-wide. The event loop is
# single-threaded, so the check-and-increment below is atomic without a lock.
_preview_inflight = 0


@router.post("/dry-run")
async def dry_run(
    payload: DryRunRequest,
    company_id: int = Depends(manage_context),
):
    """Run the real assistant on a typed message and return what it would say.

    Nothing is delivered to a channel provider, no message or conversation is
    stored, no reply is queued, and no live conversation state is touched — see
    ``bot_profile_service.preview_reply`` for how each of those is guaranteed.

    The preview runs the real model behind a blocking call. It is offloaded to a
    worker thread and capped at ``AI_PREVIEW_MAX_CONCURRENCY`` in flight: run as
    a plain ``def`` on Starlette's shared pool with no ceiling, a burst of
    previews would hold every thread for the model's round trip and freeze login
    and the inbox for every company. Excess previews are refused, not queued.
    """
    global _preview_inflight

    if _preview_inflight >= config.AI_PREVIEW_MAX_CONCURRENCY:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many previews are running right now; try again in a moment.",
        )

    _preview_inflight += 1

    try:
        return await asyncio.to_thread(
            bot_profile_service.preview_reply,
            company_id=company_id,
            message=payload.message,
            channel=payload.channel,
            language=payload.language,
        )
    except BotProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _preview_inflight -= 1


# ----------------------------------------------------------------------
# Train — the manager's teaching chat
# ----------------------------------------------------------------------
#
# The other half of the same screen as the dry run above. A manager talks to
# the assistant, and an instruction they gave is appended to the assistant's
# standing instructions — the default profile's `system_prompt`, which is the
# one string every customer reply is built from.
#
# Gated like the rest of this file rather than like the design it came from:
# reading the transcript is `settings.view`, adding to it is `settings.manage`,
# because a message here both changes how the assistant answers customers and
# spends a model call. The design's route was open to any signed-in employee,
# which on this platform would be a way for anyone to edit the company's
# assistant and spend its model budget.


@router.get("/teaching-chat")
def list_teaching_chat(company_id: int = Depends(view_context)):
    return {"items": ai_teaching_chat_service.list_messages(company_id=company_id)}


@router.post("/teaching-chat", status_code=status.HTTP_201_CREATED)
async def send_teaching_chat(
    payload: TeachingMessageCreate,
    actor: dict[str, Any] = Depends(manage_actor),
):
    """Say one thing to the assistant and get its answer back.

    Offloaded to a worker thread for the reason the dry run is: the model call
    blocks, and a handful of them on Starlette's shared pool would hold up
    login and the inbox for every company on the platform.
    """
    try:
        return await asyncio.to_thread(
            ai_teaching_chat_service.send_message,
            company_id=actor["company_id"],
            actor_user_id=actor["actor_user_id"],
            text=payload.text,
        )
    except AITeachingChatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
