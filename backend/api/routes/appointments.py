"""The appointment calendar.

Two rules run through every handler here.

The company is taken from the caller's session, never from the request. Any
handler that trusted a client-supplied `company_id` would hand one company the
key to another company's calendar, which is the same hole the tickets router
once had.

Employee names are resolved once per response through
`auth_service.user_display_names`. Appointments live in the company's own
encrypted database and users live in the control-plane database — two separate
SQLite files — so there is no join available and no per-row lookup worth making.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status as http_status,
)

from backend.api.schemas.appointments import (
    AppointmentCancelRequest,
    AppointmentCreateRequest,
    AppointmentRescheduleRequest,
    AppointmentStatusRequest,
    AvailabilityRuleCreateRequest,
    AvailabilityRuleUpdateRequest,
)
from backend.services.appointment_service import (
    ALLOWED_STATUS,
    WEEKDAY_NAMES,
    AppointmentNotFound,
    SlotConflict,
    appointment_service,
)
from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/appointments", tags=["Appointments"])


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("appointments.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("appointments.manage"))):
    return _context(current_user)


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------


def _with_staff_names(company_id: int, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach employee names to a whole page in one control-plane query."""
    ids: list[int] = []

    for item in items:
        for key in ("staff_user_id", "created_by_user_id"):
            value = item.get(key)
            if value is not None:
                ids.append(int(value))

    names = auth_service.user_display_names(company_id, ids) if ids else {}

    for item in items:
        staff_id = item.get("staff_user_id")
        creator_id = item.get("created_by_user_id")

        item["staff_name"] = (
            names.get(int(staff_id), f"User {staff_id}") if staff_id else None
        )
        item["created_by_name"] = (
            names.get(int(creator_id), f"User {creator_id}") if creator_id else None
        )

    return items


def _single(company_id: int, appointment: dict[str, Any]) -> dict[str, Any]:
    return _with_staff_names(company_id, [appointment])[0]


def _record(
    current_user: dict[str, Any],
    request: Request,
    *,
    company_id: int,
    action: str,
    appointment: dict[str, Any],
    summary: str,
) -> None:
    """File one calendar change.

    The slot, the staff member and the customer's id — never `notes`, which is
    free text an employee typed about a customer and often carries the phone
    number or address the booking was arranged on. The appointment itself keeps
    it; the log records that the booking moved and who moved it.
    """
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=action,
        category="appointments",
        target_type="appointment",
        target_id=appointment.get("id"),
        summary=summary,
        after={
            "title": appointment.get("title"),
            "starts_at": appointment.get("starts_at"),
            "ends_at": appointment.get("ends_at"),
            "status": appointment.get("status"),
            "staff_user_id": appointment.get("staff_user_id"),
            "customer_id": appointment.get("customer_id"),
        },
        ip_address=client_ip(request),
    )


def _handle(call):
    """Run a service call, mapping its refusals onto HTTP status codes.

    A booking that loses the race is a 409, not a 500: the client can act on it
    by picking another slot, and the calendar screen says so.
    """
    try:
        return call()
    except SlotConflict as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except AppointmentNotFound as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


# ----------------------------------------------------------------------
# Options, availability and free slots
#
# Declared before "/{appointment_id}" so these literal paths are not swallowed
# by the numeric route.
# ----------------------------------------------------------------------


@router.get("/options")
def appointment_options(context=Depends(view_context)):
    """Everything the booking form needs to render without a second round trip."""
    _, company_id = context

    staff = [
        {
            "id": int(employee["id"]),
            "name": employee["display_name"],
            "role": employee.get("role_name"),
        }
        for employee in auth_service.company_employees(company_id)
    ]

    return {
        "staff": staff,
        "customers": appointment_service.customer_options(company_id),
        "statuses": list(ALLOWED_STATUS),
        "weekdays": [
            {"value": index, "name": name} for index, name in enumerate(WEEKDAY_NAMES)
        ],
    }


@router.get("/availability")
def list_availability_rules(
    staff_user_id: int | None = Query(default=None, ge=1),
    weekday: int | None = Query(default=None, ge=0, le=6),
    active_only: bool = Query(default=False),
    context=Depends(view_context),
):
    _, company_id = context

    rules = _handle(
        lambda: appointment_service.list_rules(
            company_id=company_id,
            staff_user_id=staff_user_id,
            weekday=weekday,
            active_only=active_only,
        )
    )

    return {"items": _with_staff_names(company_id, rules), "total": len(rules)}


@router.post("/availability", status_code=http_status.HTTP_201_CREATED)
def create_availability_rule(
    payload: AvailabilityRuleCreateRequest,
    context=Depends(manage_context),
):
    _, company_id = context

    rule = _handle(
        lambda: appointment_service.create_rule(
            company_id=company_id,
            **payload.model_dump(),
        )
    )

    return _single(company_id, rule)


@router.put("/availability/{rule_id}")
def update_availability_rule(
    rule_id: int,
    payload: AvailabilityRuleUpdateRequest,
    context=Depends(manage_context),
):
    _, company_id = context

    rule = _handle(
        lambda: appointment_service.update_rule(
            company_id=company_id,
            rule_id=rule_id,
            **payload.model_dump(exclude_unset=True),
        )
    )

    return _single(company_id, rule)


@router.delete("/availability/{rule_id}")
def delete_availability_rule(rule_id: int, context=Depends(manage_context)):
    _, company_id = context

    _handle(
        lambda: appointment_service.delete_rule(
            company_id=company_id, rule_id=rule_id
        )
    )

    return {"success": True}


@router.get("/slots")
def available_slots(
    staff_user_id: int = Query(ge=1),
    date: str = Query(min_length=8, max_length=32),
    duration_minutes: int | None = Query(default=None, ge=5, le=480),
    context=Depends(view_context),
):
    """Free slots for one staff member on one day."""
    _, company_id = context

    return _handle(
        lambda: appointment_service.available_slots(
            company_id,
            staff_user_id,
            date,
            duration_minutes=duration_minutes,
        )
    )


# ----------------------------------------------------------------------
# Appointments
# ----------------------------------------------------------------------


@router.get("")
def list_appointments(
    start_date: str | None = Query(default=None, max_length=32),
    end_date: str | None = Query(default=None, max_length=32),
    staff_user_id: int | None = Query(default=None, ge=1),
    customer_id: int | None = Query(default=None, ge=1),
    status_filter: str | None = Query(default=None, alias="status", max_length=40),
    include_cancelled: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context

    result = _handle(
        lambda: appointment_service.list(
            company_id=company_id,
            start_date=start_date,
            end_date=end_date,
            staff_user_id=staff_user_id,
            customer_id=customer_id,
            status=status_filter,
            include_cancelled=include_cancelled,
            limit=limit,
            offset=offset,
        )
    )

    result["items"] = _with_staff_names(company_id, result["items"])
    return result


@router.post("", status_code=http_status.HTTP_201_CREATED)
def create_appointment(
    payload: AppointmentCreateRequest,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    appointment = _handle(
        lambda: appointment_service.create(
            company_id=company_id,
            created_by_user_id=current_user.get("id"),
            **payload.model_dump(),
        )
    )

    _record(
        current_user,
        request,
        company_id=company_id,
        action=Action.APPOINTMENT_CREATED,
        appointment=appointment,
        summary=(
            f"Booked {appointment.get('title')} at {appointment.get('starts_at')}"
        ),
    )

    return _single(company_id, appointment)


@router.get("/{appointment_id}")
def get_appointment(appointment_id: int, context=Depends(view_context)):
    _, company_id = context

    appointment = appointment_service.get(
        company_id=company_id, appointment_id=appointment_id
    )

    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    return _single(company_id, appointment)


@router.patch("/{appointment_id}/reschedule")
def reschedule_appointment(
    appointment_id: int,
    payload: AppointmentRescheduleRequest,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    appointment = _handle(
        lambda: appointment_service.reschedule(
            company_id=company_id,
            appointment_id=appointment_id,
            **payload.model_dump(),
        )
    )

    _record(
        current_user,
        request,
        company_id=company_id,
        action=Action.APPOINTMENT_UPDATED,
        appointment=appointment,
        summary=(
            f"Moved {appointment.get('title')} to {appointment.get('starts_at')}"
        ),
    )

    return _single(company_id, appointment)


@router.post("/{appointment_id}/cancel")
def cancel_appointment(
    appointment_id: int,
    payload: AppointmentCancelRequest,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    appointment = _handle(
        lambda: appointment_service.cancel(
            company_id=company_id,
            appointment_id=appointment_id,
            reason=payload.reason,
        )
    )

    # The reason is left out of the entry for the same reason `notes` is: it is
    # free text about a customer, and the appointment already holds it.
    _record(
        current_user,
        request,
        company_id=company_id,
        action=Action.APPOINTMENT_UPDATED,
        appointment=appointment,
        summary=f"Cancelled {appointment.get('title')}",
    )

    return _single(company_id, appointment)


@router.patch("/{appointment_id}/status")
def update_appointment_status(
    appointment_id: int,
    payload: AppointmentStatusRequest,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    appointment = _handle(
        lambda: appointment_service.set_status(
            company_id=company_id,
            appointment_id=appointment_id,
            status=payload.status,
        )
    )

    _record(
        current_user,
        request,
        company_id=company_id,
        action=Action.APPOINTMENT_UPDATED,
        appointment=appointment,
        summary=f"Marked {appointment.get('title')} as {payload.status}",
    )

    return _single(company_id, appointment)
