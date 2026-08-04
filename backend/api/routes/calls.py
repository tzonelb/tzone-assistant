from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.calls import CallCreateRequest, CallUpdateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.call_log_service import (
    CallLogConflictError,
    CallLogValidationError,
    call_log_service,
)


router = APIRouter(prefix="/api/calls", tags=["Calls"])


def current_context(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


# RBAC notes: two dedicated permission codes are seeded in database.py for
# this module -- "calls.view" (list/read) and "calls.manage"
# (create/edit/delete). Both are granted automatically to the built-in
# "owner" role (auth_service.has_permission special-cases role code
# 'owner' to always allow, the same way every other permission code in
# this codebase is wired to it) and can be attached to any other role
# from the Roles & Permissions admin screen.
def _require_calls_access(
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
            detail="You do not have call log access.",
        )


@router.get("")
def list_calls(
    direction: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_calls_access(current_user, company_id, "calls.view")

    return call_log_service.list_calls(
        company_id=company_id,
        direction=direction,
        outcome=outcome,
        customer_id=customer_id,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/{call_id}")
def get_call(call_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_calls_access(current_user, company_id, "calls.view")

    try:
        return call_log_service.get_call(company_id=company_id, call_id=call_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_call(payload: CallCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    _require_calls_access(current_user, company_id, "calls.manage")

    values = payload.model_dump(exclude_unset=True)
    try:
        return call_log_service.create_call(
            company_id=company_id,
            values=values,
            actor_user_id=current_user.get("id"),
        )
    except CallLogValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{call_id}")
def update_call(
    call_id: int,
    payload: CallUpdateRequest,
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_calls_access(current_user, company_id, "calls.manage")

    values = payload.model_dump(exclude_unset=True)
    # The concurrency token is not a call field -- pull it out before the
    # service filters the remaining editable fields.
    expected_updated_at = values.pop("expected_updated_at", None)

    try:
        return call_log_service.update_call(
            company_id=company_id,
            call_id=call_id,
            values=values,
            expected_updated_at=expected_updated_at,
        )
    except CallLogConflictError as exc:
        try:
            current = call_log_service.get_call(
                company_id=company_id, call_id=call_id
            )
        except KeyError:
            current = None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "current": current},
        ) from exc
    except CallLogValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{call_id}")
def delete_call(call_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_calls_access(current_user, company_id, "calls.manage")

    deleted = call_log_service.delete_call(company_id=company_id, call_id=call_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Call not found")

    return {"message": "Call deleted"}
