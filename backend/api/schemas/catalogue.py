from pydantic import BaseModel, Field


class ProductCreateRequest(BaseModel):
    name: str = Field(max_length=200)
    sku: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=80)
    price_cents: int = 0
    stock_quantity: int = 0
    image_url: str | None = Field(default=None, max_length=2000)


class ProductUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    sku: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=80)
    price_cents: int | None = None
    stock_quantity: int | None = None
    image_url: str | None = Field(default=None, max_length=2000)
    status: str | None = None
