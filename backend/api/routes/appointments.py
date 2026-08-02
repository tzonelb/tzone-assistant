from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.routes.conversations import _company_employees
from backend.api.schemas.appointments import AppointmentCreateRequest, AppointmentUpdateRequest
from backend.services.appointment_service import STATUSES, appointment_service
from backend.services.auth_service import auth_service, get_current_user


router = APIRouter(prefix="/api/appointments", tags=["Appointments"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def _can_view_all(current_user, company_id: int) -> bool:
    """Owner/admin (or a super admin) see every employee's appointments
    for oversight; a regular employee only ever sees their own —
    "users.manage" is the existing admin-level permission code, reused
    here rather than inventing a new one just for this."""
    return auth_service.has_permission(
        user_id=current_user.get("id"),
        company_id=company_id,
        permission_code="users.manage",
        is_super_admin=bool(current_user.get("is_super_admin")),
    )


@router.get("/options")
def appointment_options(context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.appointments")
    return {
        "statuses": STATUSES,
        "employees": _company_employees(company_id),
    }


@router.post("")
def create_appointment(payload: AppointmentCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.appointments")
    try:
        return appointment_service.create_appointment(
            company_id=company_id,
            title=payload.title,
            scheduled_at=payload.scheduled_at,
            customer_id=payload.customer_id,
            employee_user_id=payload.employee_user_id,
            duration_minutes=payload.duration_minutes,
            status=payload.status,
            notes=payload.notes,
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("")
def list_appointments(
    status: str | None = Query(default=None),
    employee_user_id: int | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    context=Depends(current_context),
):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.appointments")
    if not _can_view_all(current_user, company_id):
        employee_user_id = current_user.get("id")
    return appointment_service.list_appointments(
        company_id=company_id,
        status=status,
        employee_user_id=employee_user_id,
        customer_id=customer_id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/{appointment_id}")
def get_appointment(appointment_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.appointments")
    try:
        appointment = appointment_service.get_appointment(company_id=company_id, appointment_id=appointment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _can_view_all(current_user, company_id) and appointment.get("employee_user_id") != current_user.get("id"):
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


@router.put("/{appointment_id}")
def update_appointment(appointment_id: int, payload: AppointmentUpdateRequest, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.appointments")
    try:
        existing = appointment_service.get_appointment(company_id=company_id, appointment_id=appointment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _can_view_all(current_user, company_id) and existing.get("employee_user_id") != current_user.get("id"):
        raise HTTPException(status_code=404, detail="Appointment not found")
    try:
        return appointment_service.update_appointment(
            company_id=company_id,
            appointment_id=appointment_id,
            values=payload.model_dump(exclude_unset=True),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{appointment_id}")
def delete_appointment(appointment_id: int, context=Depends(current_context)):
    current_user, company_id = context
    auth_service.require_permission(current_user, company_id, "modules.appointments")
    try:
        existing = appointment_service.get_appointment(company_id=company_id, appointment_id=appointment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not _can_view_all(current_user, company_id) and existing.get("employee_user_id") != current_user.get("id"):
        raise HTTPException(status_code=404, detail="Appointment not found")
    try:
        appointment_service.delete_appointment(company_id=company_id, appointment_id=appointment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}
