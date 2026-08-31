"""Request bodies for the catalogue API.

None of these carry a ``company_id``. The company is resolved from the caller's
token in the router, so a client cannot write into — or read out of — a company
it does not belong to.

The limits are real limits. Product rows are the assistant's verified facts and
are serialized into an OpenAI prompt on matching customer messages, so unbounded
description text is both a cost problem and a way to push the real instructions
out of the model's attention.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CatalogueStatus = Literal["active", "draft", "archived"]

MAX_NAME = 200
MAX_SKU = 80
MAX_BRAND = 120
MAX_DESCRIPTION = 4000
MAX_URL = 1000
MAX_CURRENCY = 8
MAX_PRICE = 1_000_000_000
MAX_QUANTITY = 1_000_000


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    name_en: str | None = Field(default=None, max_length=MAX_NAME)
    sku: str | None = Field(default=None, max_length=MAX_SKU)
    brand: str | None = Field(default=None, max_length=MAX_BRAND)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    category_id: int | None = Field(default=None, ge=1)
    price: float | None = Field(default=None, ge=0, le=MAX_PRICE)
    sale_price: float | None = Field(default=None, ge=0, le=MAX_PRICE)
    currency: str | None = Field(default=None, max_length=MAX_CURRENCY)
    stock_quantity: int | None = Field(default=None, ge=0, le=MAX_QUANTITY)
    in_stock: bool = True
    image_url: str | None = Field(default=None, max_length=MAX_URL)
    attributes: dict[str, Any] | None = None
    status: CatalogueStatus = "active"


class ProductUpdate(BaseModel):
    """Every field optional: the screen sends only what the form changed."""

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    name_en: str | None = Field(default=None, max_length=MAX_NAME)
    sku: str | None = Field(default=None, max_length=MAX_SKU)
    brand: str | None = Field(default=None, max_length=MAX_BRAND)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    category_id: int | None = Field(default=None, ge=1)
    price: float | None = Field(default=None, ge=0, le=MAX_PRICE)
    sale_price: float | None = Field(default=None, ge=0, le=MAX_PRICE)
    currency: str | None = Field(default=None, max_length=MAX_CURRENCY)
    stock_quantity: int | None = Field(default=None, ge=0, le=MAX_QUANTITY)
    in_stock: bool | None = None
    image_url: str | None = Field(default=None, max_length=MAX_URL)
    attributes: dict[str, Any] | None = None
    status: CatalogueStatus | None = None


class ProductCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    parent_id: int | None = Field(default=None, ge=1)
    sort_order: int = Field(default=0, ge=0, le=10_000)
    status: CatalogueStatus = "active"


class ProductCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    parent_id: int | None = Field(default=None, ge=1)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    status: CatalogueStatus | None = None
