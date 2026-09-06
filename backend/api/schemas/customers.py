from pydantic import BaseModel, Field


class CustomerUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    internal_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=320)
    language: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)
    notes: str | None = None
    lifecycle_stage: str | None = Field(default=None, max_length=40)
    tags: list[str] | None = None
    assigned_user_id: int | None = None
    custom_fields: dict[str, str] | None = None
    documents: list[dict[str, str]] | None = None


class CustomerCreateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=320)


class CustomerBulkUpdateRequest(BaseModel):
    # Bounded for the same reason every other id list on this platform is, and
    # at the same number `GET /api/customers` will return in one page: the bulk
    # bar acts on a selection made from a page, so 500 is already more than the
    # screen can select, and an unbounded list is a request that does unbounded
    # work per element.
    customer_ids: list[int] = Field(max_length=500)
    lifecycle_stage: str | None = Field(default=None, max_length=40)
    add_tag: str | None = Field(default=None, max_length=80)


class SegmentFilters(BaseModel):
    # Bounded like every other search term the platform accepts — the value is
    # replayed into the customer list's LIKE pattern every time the segment is
    # applied, so it inherits `GET /api/customers`'s own limit rather than
    # slipping past it by being stored first.
    search: str | None = Field(default=None, max_length=200)
    lifecycle_stage: str | None = Field(default=None, max_length=40)
    tag: str | None = Field(default=None, max_length=80)
    channel: str | None = Field(default=None, max_length=40)
    assigned_user_id: int | None = None


class SegmentCreateRequest(BaseModel):
    name: str = Field(max_length=120)
    filters: SegmentFilters = Field(default_factory=SegmentFilters)
