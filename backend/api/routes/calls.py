from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.schemas.calls import CallLogCreateRequest
from backend.services.auth_service import auth_service, get_current_user
from backend.services.call_log_service import DIRECTIONS, STATUSES, call_log_service


router = APIRouter(prefix="/api/calls", tags=["Calls"])


def current_context(current_user=Depends(get_current_user)):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


@router.get("/options")
def call_options():
    return {"directions": DIRECTIONS, "statuses": STATUSES}


@router.post("")
def create_call_log(payload: CallLogCreateRequest, context=Depends(current_context)):
    current_user, company_id = context
    try:
        return call_log_service.create_call_log(
            company_id=company_id,
            direction=payload.direction,
            phone_number=payload.phone_number,
            customer_id=payload.customer_id,
            duration_seconds=payload.duration_seconds,
            status=payload.status,
            notes=payload.notes,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def list_call_logs(
    customer_id: int | None = Query(default=None),
    direction: str | None = Query(default=None),
    status: str | None = Query(default=None),
    context=Depends(current_context),
):
    _, company_id = context
    return call_log_service.list_call_logs(
        company_id=company_id, customer_id=customer_id, direction=direction, status=status,
    )


@router.delete("/{call_id}")
def delete_call_log(call_id: int, context=Depends(current_context)):
    _, company_id = context
    try:
        call_log_service.delete_call_log(company_id=company_id, call_id=call_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}
