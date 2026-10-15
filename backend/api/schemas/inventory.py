"""Request bodies for the inventory API.

None of these carry a ``company_id`` or a resulting quantity: the company is
resolved from the caller's token in the router, and the balance a movement
produces is computed by ``inventory_service`` from the product's current
stock, never trusted from the client.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Mirrors inventory_service.MOVEMENT_TYPES. Duplicated rather than imported,
# the same way backend/api/schemas/catalogue.py keeps its own CatalogueStatus
# literal instead of importing catalogue_service.ALLOWED_STATUS — a schema
# describes the wire shape and must not reach into a service module to do it.
MovementType = Literal["receipt", "sale", "return", "adjustment", "write_off"]

MAX_REASON = 500
MAX_REFERENCE = 200
MAX_DELTA = 1_000_000


class StockMovementCreate(BaseModel):
    product_id: int = Field(ge=1)
    movement_type: MovementType
    # Signed: positive adds to stock on hand, negative takes from it. A plain
    # "quantity" field would leave the sign to be inferred from
    # `movement_type`, which breaks the moment a type legitimately goes either
    # way -- a stock count correction is sometimes up and sometimes down.
    quantity_delta: int = Field(ge=-MAX_DELTA, le=MAX_DELTA)
    reason: str | None = Field(default=None, max_length=MAX_REASON)
    reference: str | None = Field(default=None, max_length=MAX_REFERENCE)


class ReorderPointUpdate(BaseModel):
    # None clears the alert -- the product has no reorder threshold.
    reorder_point: int | None = Field(default=None, ge=0, le=1_000_000)
