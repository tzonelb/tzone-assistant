"""Request bodies for the purchasing API.

None of these carry a ``company_id``: the company is resolved from the
caller's token in the router, the same reason ``backend/api/schemas/
catalogue.py`` gives for the same omission.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SupplierStatus = Literal["active", "archived"]
# Mirrors purchasing_service.SETTABLE_ORDER_STATUS -- partially_received and
# received are not here on purpose, the same reason the service refuses them:
# they follow from receiving rather than being a status anyone sets.
SettableOrderStatus = Literal["draft", "sent", "cancelled"]

MAX_NAME = 200
MAX_CONTACT = 200
MAX_EMAIL = 200
MAX_PHONE = 40
MAX_ADDRESS = 500
MAX_NOTES = 2000
MAX_REFERENCE = 100
MAX_QUANTITY = 1_000_000
MAX_UNIT_COST = 1_000_000_000


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    contact_name: str | None = Field(default=None, max_length=MAX_CONTACT)
    email: str | None = Field(default=None, max_length=MAX_EMAIL)
    phone: str | None = Field(default=None, max_length=MAX_PHONE)
    address: str | None = Field(default=None, max_length=MAX_ADDRESS)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    status: SupplierStatus = "active"


class SupplierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    contact_name: str | None = Field(default=None, max_length=MAX_CONTACT)
    email: str | None = Field(default=None, max_length=MAX_EMAIL)
    phone: str | None = Field(default=None, max_length=MAX_PHONE)
    address: str | None = Field(default=None, max_length=MAX_ADDRESS)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    status: SupplierStatus | None = None


class PurchaseOrderItemCreate(BaseModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=MAX_QUANTITY)
    unit_cost: float = Field(default=0, ge=0, le=MAX_UNIT_COST)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int = Field(ge=1)
    items: list[PurchaseOrderItemCreate] = Field(min_length=1)
    reference: str | None = Field(default=None, max_length=MAX_REFERENCE)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    expected_date: str | None = Field(default=None, max_length=40)


class PurchaseOrderStatusUpdate(BaseModel):
    status: SettableOrderStatus


class PurchaseOrderReceiveItem(BaseModel):
    item_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=MAX_QUANTITY)


class PurchaseOrderReceive(BaseModel):
    items: list[PurchaseOrderReceiveItem] = Field(min_length=1)
