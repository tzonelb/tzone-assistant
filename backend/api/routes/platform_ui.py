"""What the customer workspace is allowed to show.

The Super Admin decides which modules a company sees, how the workspace is
branded and how the shell is laid out. That decision is stored in the control
plane; this endpoint is how the customer application reads its own copy of it.

Two things it deliberately is not:

* It is not the enforcement. ``backend/services/module_access.require_module``
  is, and it guards the module routers themselves. This endpoint only lets the
  interface avoid drawing a link that would open onto a 403.
* It is not the control-plane API. A company token reaches this and nothing
  else: it returns the caller's own configuration, never another company's, and
  it cannot change anything.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend.services.auth_service import (
    auth_service,
    client_ip,
    get_current_user,
    require_permission,
)
from backend.services.activity_service import Action, activity_service
from backend.services.platform_service import PlatformError, platform_service
from backend.services.platform_ui_service import (
    PlatformUiError,
    PlatformUiNotFound,
    platform_ui_service,
)
from database.manager import DatabaseError


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/platform-ui", tags=["Workspace Configuration"])


@router.get("/config")
async def get_workspace_config(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """The module, branding and layout configuration of the caller's company.

    The company is taken from the session, never from a parameter. An employee
    asking for a configuration is always asking for their own.
    """
    company_id = auth_service.resolve_company_id(current_user)

    try:
        config = platform_service.get_platform_config(company_id)
        # The published Theme Studio layers underneath this company's own
        # tokens: platform, then its plan, then a company-scope theme, then
        # whatever `PUT /theme` last wrote. Resolved here rather than inside
        # `platform_service` so that module keeps answering exactly one
        # question — what the operator decided about this company.
        resolved = platform_ui_service.resolve(company_id=company_id)
    except DatabaseError:
        logger.exception("Could not read workspace config for company %s.", company_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace configuration is temporarily unavailable.",
        ) from None

    branding = config["branding"] or {}

    return {
        "company_id": company_id,
        # The operator's gate, narrowed by anything a published theme hides.
        # Narrowed and never widened: a theme decides what is drawn, and a
        # module the operator switched off stays off however it is written.
        "modules": platform_ui_service.visible_modules(
            config["modules"], resolved["modules"]
        ),
        "branding": branding,
        "layout": config["layout"],
        # The design tokens the interface renders with, resolved over the
        # platform defaults so this is never partial. `modules` above stays the
        # operator's gate; `tokens` only decides how the interface looks.
        "tokens": resolved["tokens"],
        # Which published version the tokens above came from. 0 means nobody has
        # published a theme and the interface is on the bundled defaults.
        "version": resolved["version"],
        "brand": {
            "name": branding.get("brand_name") or "T-ZONE",
            "logoUrl": branding.get("logo_url") or "/tzone-logo.png",
        },
        "updated_at": config["updated_at"],
    }


@router.put("/theme")
async def update_workspace_theme(
    request: Request,
    theme: dict[str, Any] = Body(..., embed=False),
    current_user: dict[str, Any] = Depends(require_permission("settings.manage")),
) -> dict[str, Any]:
    """Publish this company's design tokens (Theme Studio).

    Scoped to the caller's own company, exactly like the read above: the company
    comes from the session and never from the payload. It writes tokens and
    nothing else, so styling a workspace can never switch a module on — that
    remains the platform operator's decision.
    """
    company_id = auth_service.resolve_company_id(current_user)

    try:
        resolved = platform_service.update_theme(
            company_id,
            theme,
            actor_user_id=int(current_user["id"]),
        )
    except PlatformError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except DatabaseError:
        logger.exception("Could not save the theme for company %s.", company_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The theme could not be saved.",
        ) from None

    activity_service.record(
        company_id=company_id,
        action=Action.SETTINGS_UPDATED,
        category="settings",
        kind="change",
        actor_user_id=int(current_user["id"]),
        summary="Workspace theme published",
        ip_address=client_ip(request),
    )

    return {"tokens": resolved}


# ---------------------------------------------------------------- Theme Studio
#
# The single write above publishes a company's tokens in one step: no history,
# and nothing between deciding and everybody seeing it. Theme Studio needs the
# step in between, so these five endpoints are the draft lifecycle
# `backend/services/platform_ui_service.py` describes — open a draft, edit it a
# control at a time, publish it as the next numbered version, restore an
# archived one.
#
# They are scoped rather than always company-scoped. A `platform` theme reaches
# every workspace, which is a platform decision and not a company's, so writing
# one requires a super admin. The one scope a company may write is its own, and
# only its owner may.


class ThemeScope(BaseModel):
    scope_type: str = Field(pattern="^(platform|plan|company)$")
    scope_id: str | None = None
    tokens: dict[str, Any] = Field(default_factory=dict)
    modules: dict[str, Any] = Field(default_factory=dict)


class ThemeDraftPatch(BaseModel):
    # `None` means "not mentioned in this request", which is not the same as an
    # empty object: Theme Studio saves one control at a time and has to be able
    # to touch tokens without also rewriting the module list.
    tokens: dict[str, Any] | None = None
    modules: dict[str, Any] | None = None


class ThemePublish(BaseModel):
    # Required, and required to say something. The audit row this writes is read
    # weeks later by somebody asking why the platform changed colour, and an
    # empty string is not an answer to that question.
    reason: str = Field(min_length=1, max_length=300)


def _check_scope_permission(
    current_user: dict[str, Any], scope_type: str, scope_id: str | None
) -> None:
    """Establish that this caller may write this scope.

    A super admin may write any of them. Everybody else may write exactly one:
    their own company's, and only if they own it. Deliberately narrower than the
    `settings.manage` that guards `PUT /theme` — a company-scope theme is a
    versioned object layered under the operator's own, and holding a settings
    permission is not the same as being entitled to publish into that stack.
    """
    if bool(current_user.get("is_super_admin")):
        return

    if scope_type == "company" and scope_id:
        owns_it = any(
            str(company.get("id")) == str(scope_id)
            and company.get("role_code") == "owner"
            for company in auth_service.get_user_companies(int(current_user["id"]))
        )

        if owns_it:
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Only a platform administrator — or, for their own company, its "
            "owner — can manage this theme scope."
        ),
    )


def _theme_or_404(theme_id: int) -> dict[str, Any]:
    try:
        return platform_ui_service.get_theme(theme_id=theme_id)
    except PlatformUiNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such theme."
        ) from None


def _refused(exc: PlatformUiError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/themes")
def list_themes(
    scope_type: str = Query(pattern="^(platform|plan|company)$"),
    scope_id: str | None = Query(default=None, max_length=200),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """One scope's open draft and its version history."""
    _check_scope_permission(current_user, scope_type, scope_id)

    try:
        themes = platform_ui_service.list_themes(
            scope_type=scope_type, scope_id=scope_id
        )
    except PlatformUiError as exc:
        raise _refused(exc) from exc
    except DatabaseError:
        logger.exception("Could not list the %s themes.", scope_type)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Theme Studio is temporarily unavailable.",
        ) from None

    return {"themes": themes}


@router.post("/themes", status_code=status.HTTP_201_CREATED)
def create_theme_draft(
    payload: ThemeScope,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Open a draft, starting from what this scope already has published."""
    _check_scope_permission(current_user, payload.scope_type, payload.scope_id)

    try:
        theme = platform_ui_service.create_draft(
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            tokens=payload.tokens,
            modules=payload.modules,
            created_by=int(current_user["id"]),
        )
    except PlatformUiError as exc:
        raise _refused(exc) from exc

    return platform_ui_service.serialize(theme)


@router.patch("/themes/{theme_id}")
def update_theme_draft(
    theme_id: int,
    payload: ThemeDraftPatch,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Merge one more change into an open draft. Nobody else sees it yet."""
    theme = _theme_or_404(theme_id)
    _check_scope_permission(current_user, theme["scope_type"], theme["scope_id"])

    try:
        updated = platform_ui_service.update_draft(
            theme_id=theme_id, tokens=payload.tokens, modules=payload.modules
        )
    except PlatformUiError as exc:
        raise _refused(exc) from exc

    return platform_ui_service.serialize(updated)


@router.post("/themes/{theme_id}/publish")
def publish_theme(
    request: Request,
    theme_id: int,
    payload: ThemePublish,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Make this draft the scope's next version, archiving the one it replaces."""
    theme = _theme_or_404(theme_id)
    _check_scope_permission(current_user, theme["scope_type"], theme["scope_id"])

    try:
        published = platform_ui_service.publish(
            theme_id=theme_id,
            actor_user_id=int(current_user["id"]),
            reason=payload.reason.strip(),
        )
    except PlatformUiError as exc:
        raise _refused(exc) from exc

    # In the company's own log as well as the platform audit, when the theme is
    # that company's: an owner reading their workspace's activity should see
    # that its appearance changed rather than have to ask the operator.
    if theme["scope_type"] == "company" and theme["scope_id"]:
        activity_service.record(
            company_id=int(theme["scope_id"]),
            action=Action.SETTINGS_UPDATED,
            category="settings",
            kind="change",
            actor_user_id=int(current_user["id"]),
            summary=f"Workspace theme published (v{published['version']})",
            ip_address=client_ip(request),
        )

    return platform_ui_service.serialize(published)


@router.post("/themes/{theme_id}/restore", status_code=status.HTTP_201_CREATED)
def restore_theme(
    theme_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Reopen an archived version as a new draft, leaving the record intact."""
    theme = _theme_or_404(theme_id)
    _check_scope_permission(current_user, theme["scope_type"], theme["scope_id"])

    try:
        restored = platform_ui_service.restore(
            theme_id=theme_id, created_by=int(current_user["id"])
        )
    except PlatformUiError as exc:
        raise _refused(exc) from exc

    return platform_ui_service.serialize(restored)
