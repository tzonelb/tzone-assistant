"""Invoices and the payments recorded against them.

Reads require ``invoices.view`` and writes require ``invoices.manage``, the
same split every other commerce module in this cluster uses. The company is
never taken from the request -- it is resolved from the caller's token.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.schemas.invoices import InvoiceCreate, InvoiceStatusUpdate, PaymentCreate
from backend.services.auth_service import (
    auth_service,
    client_ip,
    require_permission,
)
from backend.services.activity_service import Action, activity_service
from backend.services.invoice_service import invoice_service


router = APIRouter(prefix="/api/invoices", tags=["Invoices"])


def _context(current_user: dict[str, Any]) -> tuple[dict[str, Any], int]:
    company_id = auth_service.resolve_company_id(
        current_user=current_user, requested_company_id=None
    )
    return current_user, int(company_id)


def view_context(current_user=Depends(require_permission("invoices.view"))):
    return _context(current_user)


def manage_context(current_user=Depends(require_permission("invoices.manage"))):
    return _context(current_user)


@router.get("")
def list_invoices(
    status_filter: str | None = Query(default=None, alias="status", max_length=30),
    customer_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context=Depends(view_context),
):
    _, company_id = context

    try:
        return invoice_service.list_invoices(
            company_id=company_id, status=status_filter, customer_id=customer_id,
            limit=limit, offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_invoice(payload: InvoiceCreate, request: Request, context=Depends(manage_context)):
    current_user, company_id = context

    try:
        invoice = invoice_service.create_invoice(
            company_id=company_id,
            customer_id=payload.customer_id,
            items=[item.model_dump() for item in payload.items],
            order_id=payload.order_id,
            due_date=payload.due_date,
            reference=payload.reference,
            notes=payload.notes,
            user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.INVOICE_CREATED,
        category="invoices",
        target_type="invoice",
        target_id=invoice.get("id"),
        summary=f"Raised an invoice for {invoice.get('customer_name')}",
        after={"total": invoice.get("total")},
        ip_address=client_ip(request),
    )

    return invoice


@router.get("/{invoice_id}")
def get_invoice(invoice_id: int, context=Depends(view_context)):
    _, company_id = context
    invoice = invoice_service.get_invoice(company_id=company_id, invoice_id=invoice_id)

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    return invoice


@router.put("/{invoice_id}/status")
def update_invoice_status(
    invoice_id: int, payload: InvoiceStatusUpdate, request: Request,
    context=Depends(manage_context),
):
    current_user, company_id = context

    try:
        invoice = invoice_service.update_invoice_status(
            company_id=company_id, invoice_id=invoice_id, status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.INVOICE_STATUS_CHANGED,
        category="invoices",
        target_type="invoice",
        target_id=invoice_id,
        summary=f"Set invoice {invoice_id} to {invoice.get('status')}",
        after={"status": invoice.get("status")},
        ip_address=client_ip(request),
    )

    return invoice


@router.post("/{invoice_id}/payments")
def record_payment(
    invoice_id: int, payload: PaymentCreate, request: Request, context=Depends(manage_context)
):
    current_user, company_id = context

    try:
        invoice = invoice_service.record_payment(
            company_id=company_id,
            invoice_id=invoice_id,
            amount=payload.amount,
            method=payload.method,
            reference=payload.reference,
            notes=payload.notes,
            user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    activity_service.record_for(
        current_user,
        company_id=company_id,
        action=Action.PAYMENT_RECORDED,
        category="invoices",
        target_type="invoice",
        target_id=invoice_id,
        summary=f"Recorded a payment of {payload.amount} against invoice {invoice_id}",
        after={"status": invoice.get("status"), "amount_paid": invoice.get("amount_paid")},
        ip_address=client_ip(request),
    )

    return invoice
