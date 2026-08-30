from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.schemas.customers import (
    CustomerBulkUpdateRequest,
    CustomerCreateRequest,
    CustomerUpdateRequest,
    SegmentCreateRequest,
)
from backend.services.activity_service import Action, activity_service
from backend.services.auth_service import auth_service, client_ip, require_permission
from backend.services.customer_service import LIFECYCLE_STAGES, customer_service


router = APIRouter(prefix="/api/customers", tags=["Customers"])
# Segments are a Contacts feature but not a customer: they sit under their own
# prefix rather than under `/api/customers/{id}`, because a segment belongs to
# the company and not to any one contact. `main.py` puts both routers behind the
# same `customers` module gate.
segments_router = APIRouter(prefix="/api/customer-segments", tags=["Customer Segments"])


def _context(current_user):
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("customers.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("customers.manage"))):
    return _context(current_user)


def _require_settings_manage(current_user, company_id: int) -> None:
    """The second gate on the two actions that reach past one contact.

    A bulk edit changes rows the employee never opened, and deleting somebody
    else's segment removes a saved view from their screen. Both are the
    company's arrangement of its own work rather than day-to-day contact
    editing, which is why they sit behind `settings.manage` — the same
    permission the Contacts screen itself checks before it offers the controls.
    """
    if not auth_service.has_permission(
        user_id=int(current_user["id"]),
        company_id=company_id,
        permission_code="settings.manage",
        is_super_admin=bool(current_user.get("is_super_admin")),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the 'settings.manage' permission.",
        )


# Declared before `/{customer_id}`: FastAPI matches in declaration order, and
# `/api/customers/options` would otherwise be read as a customer id and answer
# 422 instead of the options the screen needs to draw its dropdowns.
@router.get("/options")
def customer_options(context=Depends(view_context)):
    """Reference data for the Contacts screen: the fixed lifecycle pipeline and
    the company's active employees, for the owner dropdown. Tags carry no
    options list — they are free-form and company-defined.
    """
    _, company_id = context
    return {
        "lifecycle_stages": LIFECYCLE_STAGES,
        "employees": auth_service.company_employees(company_id),
    }


@router.get("")
def list_customers(
    # Bounded like the seven other search parameters on the platform. No
    # exploit was demonstrated for the unbounded version -- SQLite matches
    # a pattern of twenty thousand wildcards against three thousand rows in
    # twelve milliseconds -- so this closes an inconsistency rather than a
    # hole. It is worth closing because the term is interpolated into a
    # LIKE pattern across seven columns, and the next thing to consume it
    # may not be as forgiving as LIKE.
    search: str | None = Query(default=None, max_length=200),
    lifecycle_stage: str | None = Query(default=None, max_length=40),
    tag: str | None = Query(default=None, max_length=80),
    assigned_user_id: int | None = Query(default=None),
    segment_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context
    try:
        return customer_service.list_customers(
            company_id=company_id,
            search=search,
            lifecycle_stage=lifecycle_stage,
            tag=tag,
            assigned_user_id=assigned_user_id,
            segment_id=segment_id,
            limit=limit,
            offset=offset,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("")
def create_customer(
    payload: CustomerCreateRequest,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        customer = customer_service.create_customer(
            company_id=company_id,
            display_name=payload.display_name,
            phone=payload.phone,
            email=payload.email,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Recorded for the same reason an edit is: a contact appearing in the
    # register is a change to the company's own records, and the owner is
    # entitled to know who put it there. Field names only, never the values.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.CUSTOMER_CREATED,
        category="customers",
        target_type="customer",
        target_id=int(customer["id"]),
        summary="Added a customer record by hand",
        ip_address=client_ip(request),
    )

    return customer


@router.post("/bulk-update")
def bulk_update_customers(
    payload: CustomerBulkUpdateRequest,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context
    _require_settings_manage(current_user, company_id)

    try:
        result = customer_service.bulk_update_customers(
            company_id=company_id,
            customer_ids=payload.customer_ids,
            lifecycle_stage=payload.lifecycle_stage,
            add_tag=payload.add_tag,
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.CUSTOMER_UPDATED,
        category="customers",
        target_type="customer",
        target_id=None,
        summary=f"Edited {result['updated']} customer records at once",
        after={"updated": result["updated"]},
        ip_address=client_ip(request),
    )

    return result


@router.get("/{customer_id}")
def get_customer(customer_id: int, request: Request, context=Depends(view_context)):
    current_user, company_id = context

    try:
        customer = customer_service.get_customer(
            company_id=company_id, customer_id=customer_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # After the record is found, so a probe for a customer id that does not
    # exist does not leave an entry saying somebody read it. A customer file
    # holds contact details the person gave this company and nobody else, so
    # who opened it is the owner's to see even though nothing changed.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.CUSTOMER_OPENED,
        category="customers",
        kind="read",
        target_type="customer",
        target_id=customer_id,
        summary="Opened a customer record",
        ip_address=client_ip(request),
    )

    return customer


@router.get("/{customer_id}/timeline")
def get_customer_timeline(customer_id: int, context=Depends(view_context)):
    """The client file's history. Not recorded as a read of its own: it is
    fetched by the same screen, at the same moment, as the record itself, and
    `CUSTOMER_OPENED` above already says who opened it.
    """
    _, company_id = context
    try:
        return {
            "items": customer_service.get_timeline(
                company_id=company_id, customer_id=customer_id
            )
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{customer_id}")
def update_customer(
    customer_id: int,
    payload: CustomerUpdateRequest,
    request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context
    values = payload.model_dump(exclude_unset=True)

    try:
        customer = customer_service.update_customer(
            company_id=company_id,
            customer_id=customer_id,
            values=values,
            actor_user_id=current_user.get("id"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # An unknown lifecycle stage, or an owner who does not work here. Both are
    # the caller naming something that does not exist rather than the record
    # being missing, so they answer 400 and say which value was refused.
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Opening a customer record was recorded before editing one was, which is
    # the wrong way round: the owner could see who looked at a phone number and
    # not who changed it. `customer_audit` did hold the change, and no endpoint
    # has ever read that table.
    #
    # Field names only. The values are the customer's own contact details, and
    # copying them here would put a second copy of every edit into a log with
    # its own retention, to no end — the customer record is what it currently
    # says, and this is the record of who made it say that.
    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.CUSTOMER_UPDATED,
        category="customers",
        target_type="customer",
        target_id=customer_id,
        summary=f"Edited a customer record: {', '.join(sorted(values)) or 'no fields'}",
        after={"changed_fields": sorted(values)},
        ip_address=client_ip(request),
    )

    return customer


@segments_router.get("")
def list_segments(context=Depends(view_context)):
    _, company_id = context
    return {"items": customer_service.list_segments(company_id=company_id)}


@segments_router.post("")
def create_segment(payload: SegmentCreateRequest, context=Depends(view_context)):
    """Saving the filters you are already looking at needs nothing more than
    being allowed to look at them. A segment holds no contact data of its own —
    it is a query, and it returns exactly what the caller could already see.
    """
    current_user, company_id = context
    try:
        return customer_service.create_segment(
            company_id=company_id,
            name=payload.name,
            filters=payload.filters.model_dump(exclude_none=True),
            actor_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@segments_router.delete("/{segment_id}")
def delete_segment(segment_id: int, context=Depends(view_context)):
    current_user, company_id = context

    try:
        segment = customer_service.get_segment(
            company_id=company_id, segment_id=segment_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Your own segment is yours to remove. Removing somebody else's takes away
    # a saved view from their screen, so it needs the same permission as the
    # other action that reaches past one employee's own work.
    if segment.get("created_by_user_id") != current_user.get("id"):
        _require_settings_manage(current_user, company_id)

    try:
        customer_service.delete_segment(company_id=company_id, segment_id=segment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"deleted": True}
