from pydantic import BaseModel, Field


class ProductCreateRequest(BaseModel):
    sku: str | None = Field(default=None, max_length=100)
    name: str = Field(..., min_length=1, max_length=300)
    description: str | None = None
    category: str | None = Field(default=None, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    price: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    quantity: float | None = None
    availability_status: str | None = Field(default=None, max_length=20)
    status: str | None = Field(default=None, max_length=20)


class ProductUpdateRequest(BaseModel):
    sku: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    category: str | None = Field(default=None, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    price: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    quantity: float | None = None
    availability_status: str | None = Field(default=None, max_length=20)
    status: str | None = Field(default=None, max_length=20)
    # Optimistic-concurrency token: the `updated_at` value the client last
    # loaded. When supplied, the update is rejected with 409 if the stored
    # record has since changed, so two editors can't silently overwrite
    # each other. Not a product field -- the route pops it before update.
    expected_updated_at: str | None = Field(default=None, max_length=64)
