"""Request bodies for the orders API.

None of these carry a ``company_id``: the company is resolved from the
caller's token in the router, the same reason ``backend/api/schemas/
catalogue.py`` gives for the same omission.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Mirrors order_service.SETTABLE_STATUS -- partially_fulfilled and fulfilled
# are not here on purpose: they follow from fulfilling rather than being a
# status anyone sets, the same reason purchasing's schema leaves out its own
# two receiving states.
SettableOrderStatus = Literal["draft", "confirmed", "cancelled"]

MAX_REFERENCE = 100
MAX_NOTES = 2000
MAX_QUANTITY = 1_000_000
MAX_UNIT_PRICE = 1_000_000_000


class OrderItemCreate(BaseModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=MAX_QUANTITY)
    unit_price: float = Field(default=0, ge=0, le=MAX_UNIT_PRICE)


class OrderCreate(BaseModel):
    customer_id: int = Field(ge=1)
    items: list[OrderItemCreate] = Field(min_length=1)
    quote_id: int | None = Field(default=None, ge=1)
    conversation_id: int | None = Field(default=None, ge=1)
    reference: str | None = Field(default=None, max_length=MAX_REFERENCE)
    notes: str | None = Field(default=None, max_length=MAX_NOTES)


class OrderStatusUpdate(BaseModel):
    status: SettableOrderStatus


class OrderFulfilItem(BaseModel):
    item_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=MAX_QUANTITY)


class OrderFulfil(BaseModel):
    items: list[OrderFulfilItem] = Field(min_length=1)
