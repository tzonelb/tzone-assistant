"""Request bodies for the invoices API.

None of these carry a ``company_id``: the company is resolved from the
caller's token in the router, the same reason ``backend/api/schemas/
catalogue.py`` gives for the same omission.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Mirrors invoice_service.SETTABLE_STATUS -- partially_paid and paid are not
# here on purpose: they follow from recording a payment rather than being a
# status anyone sets, the same reason orders' and purchasing's own schemas
# leave out their own two derived states.
SettableInvoiceStatus = Literal["draft", "sent", "void"]
PaymentMethod = Literal["cash", "card", "transfer", "other"]

MAX_DESCRIPTION = 500
MAX_REFERENCE = 100
MAX_NOTES = 2000
MAX_QUANTITY = 1_000_000
MAX_UNIT_PRICE = 1_000_000_000
MAX_AMOUNT = 1_000_000_000


class InvoiceItemCreate(BaseModel):
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION)
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY)
    unit_price: float = Field(default=0, ge=0, le=MAX_UNIT_PRICE)
    product_id: int | None = Field(default=None, ge=1)


class InvoiceCreate(BaseModel):
    customer_id: int = Field(ge=1)
    items: list[InvoiceItemCreate] = Field(min_length=1)
    order_id: int | None = Field(default=None, ge=1)
    due_date: str | None = Field(default=None, max_length=40)
    reference: str | None = Field(default=None, max_length=MAX_REFERENCE)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)


class InvoiceStatusUpdate(BaseModel):
    status: SettableInvoiceStatus


class PaymentCreate(BaseModel):
    amount: float = Field(gt=0, le=MAX_AMOUNT)
    method: PaymentMethod = "cash"
    reference: str | None = Field(default=None, max_length=MAX_REFERENCE)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
