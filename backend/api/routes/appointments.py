from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.schemas.appointments import (
    AppointmentCreateRequest,
    AppointmentUpdateRequest,
)
from backend.services.appointment_service import (
    AppointmentConflictError,
    AppointmentOverlapError,
    AppointmentValidationError,
    appointment_service,
)
from backend.services.auth_service import auth_service, get_current_user


router = APIRouter(prefix="/api/appointments", tags=["Appointments"])


def current_context(current_user: dict[str, Any] = Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


# RBAC notes: two dedicated permission codes are seeded in database.py for
# this module -- "appointments.view" (list/read) and "appointments.manage"
# (create/edit/delete). Both are granted automatically to the built-in
# "owner" role (auth_service.has_permission special-cases role code
# 'owner' to always allow, the same way every other permission code in
# this codebase is wired to it) and can be attached to any other role
# from the Roles & Permissions admin screen like any other permission
# code.
def _require_appointment_access(
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
            detail="You do not have appointment management access.",
        )


@router.get("")
def list_appointments(
    status_filter: str | None = Query(default=None, alias="status"),
    assignee_user_id: int | None = Query(default=None),
    starts_after: str | None = Query(default=None),
    starts_before: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_appointment_access(current_user, company_id, "appointments.view")

    return appointment_service.list_appointments(
        company_id=company_id,
        status=status_filter,
        assignee_user_id=assignee_user_id,
        starts_after=starts_after,
        starts_before=starts_before,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/assignable-users")
def list_assignable_users(context=Depends(current_context)):
    current_user, company_id = context
    _require_appointment_access(current_user, company_id, "appointments.view")

    return {"items": appointment_service.list_assignable_users(company_id=company_id)}


@router.get("/{appointment_id}")
def get_appointment(appointment_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_appointment_access(current_user, company_id, "appointments.view")

    try:
        return appointment_service.get_appointment(
            company_id=company_id, appointment_id=appointment_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_appointment(
    payload: AppointmentCreateRequest, context=Depends(current_context)
):
    current_user, company_id = context
    _require_appointment_access(current_user, company_id, "appointments.manage")

    values = payload.model_dump(exclude_unset=True)
    try:
        return appointment_service.create_appointment(
            company_id=company_id,
            values=values,
            actor_user_id=current_user.get("id"),
        )
    except AppointmentOverlapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AppointmentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{appointment_id}")
def update_appointment(
    appointment_id: int,
    payload: AppointmentUpdateRequest,
    context=Depends(current_context),
):
    current_user, company_id = context
    _require_appointment_access(current_user, company_id, "appointments.manage")

    values = payload.model_dump(exclude_unset=True)
    # The concurrency token is not an appointment field -- pull it out
    # before the service filters the remaining editable fields.
    expected_updated_at = values.pop("expected_updated_at", None)

    try:
        return appointment_service.update_appointment(
            company_id=company_id,
            appointment_id=appointment_id,
            values=values,
            expected_updated_at=expected_updated_at,
        )
    except AppointmentConflictError as exc:
        # Mirror TasksPage's 409 contract: a structured detail the UI can
        # act on, carrying the current record so it can offer a reload.
        try:
            current = appointment_service.get_appointment(
                company_id=company_id, appointment_id=appointment_id
            )
        except KeyError:
            current = None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "current": current},
        ) from exc
    except AppointmentOverlapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AppointmentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{appointment_id}")
def delete_appointment(appointment_id: int, context=Depends(current_context)):
    current_user, company_id = context
    _require_appointment_access(current_user, company_id, "appointments.manage")

    deleted = appointment_service.delete_appointment(
        company_id=company_id, appointment_id=appointment_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Appointment not found")

    return {"message": "Appointment deleted"}
