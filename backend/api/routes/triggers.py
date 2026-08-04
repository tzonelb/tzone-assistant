from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.triggers import TriggerCreateRequest, TriggerUpdateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.trigger_service import (
    TRIGGER_TYPES,
    TriggerValidationError,
    trigger_service,
)


router = APIRouter(prefix="/api/triggers", tags=["Bot Triggers"])


def current_context(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


# RBAC notes: two dedicated permission codes are seeded in database.py for
# this module -- "triggers.view" (list/read) and "triggers.manage"
# (create/edit/delete). Both are granted automatically to the built-in
# "owner" role (auth_service.has_permission special-cases role code
# 'owner' to always allow, the same way every other permission code in
# this codebase is wired to it) and can be attached to any other role
# from the Roles & Permissions admin screen.
def _require_triggers_access(
    current_user: dict[str, Any],
    company_id: int,
    permission_code: str,
) -> None:
    allowed = auth_service.has_permission(
        user_id=current_user["id"],
        company_id=company_id,
        permission_code=permission_code,
        is_super_admin=bool(current_user.get("is_super_admin")),
    )

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have bot trigger access.",
        )


@router.get("/types")
def list_trigger_types(context=Depends(current_context)):
    current_user, company_id = context
    _require_triggers_access(current_user, company_id, "triggers.view")

    return {
        "items": [
            {
                "type": type_key,
                "kind": meta["kind"],
                "needs_delay": meta["needs_delay"],
                "label": meta["label"],
            }
            for type_key, meta in TRIGGER_TYPES.items()
        ]
    }


@router.get("/firings")
def list_firings(
    trigger_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_triggers_access(current_user, company_id, "triggers.view")

    return trigger_service.list_firings(
        company_id=company_id,
        trigger_id=trigger_id,
        limit=limit,
        offset=offset,
    )


@router.get("")
def list_triggers(context=Depends(current_context)):
    current_user, company_id = context
    _require_triggers_access(current_user, company_id, "triggers.view")

    return {"items": trigger_service.list_triggers(company_id=company_id)}


@router.get("/{trigger_id}")
def get_trigger(trigger_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_triggers_access(current_user, company_id, "triggers.view")

    try:
        return trigger_service.get_trigger(
            company_id=company_id, trigger_id=trigger_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_trigger(payload: TriggerCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    _require_triggers_access(current_user, company_id, "triggers.manage")

    values = payload.model_dump(exclude_unset=True)
    try:
        return trigger_service.create_trigger(
            company_id=company_id,
            values=values,
            actor_user_id=current_user.get("id"),
        )
    except TriggerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{trigger_id}")
def update_trigger(
    trigger_id: int,
    payload: TriggerUpdateRequest,
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_triggers_access(current_user, company_id, "triggers.manage")

    values = payload.model_dump(exclude_unset=True)
    try:
        return trigger_service.update_trigger(
            company_id=company_id,
            trigger_id=trigger_id,
            values=values,
        )
    except TriggerValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{trigger_id}")
def delete_trigger(trigger_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_triggers_access(current_user, company_id, "triggers.manage")

    deleted = trigger_service.delete_trigger(
        company_id=company_id, trigger_id=trigger_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Trigger not found")

    return {"message": "Trigger deleted"}
