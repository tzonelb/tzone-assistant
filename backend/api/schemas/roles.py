from pydantic import BaseModel, Field


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    code: str = Field(min_length=2, max_length=60, pattern=r"^[a-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=300)
    permission_codes: list[str] = []


class RoleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    permission_codes: list[str] | None = None


class UserCreateRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    role_id: int
    branch_id: int | None = None
    departments: list[str] = []


class UserAssignmentRequest(BaseModel):
    role_id: int
    branch_id: int | None = None
    status: str = Field(default="active", pattern=r"^(active|disabled)$")
    departments: list[str] = []


class PermissionOverrideItem(BaseModel):
    permission_code: str
    allowed: bool


class PermissionOverridesUpdateRequest(BaseModel):
    overrides: list[PermissionOverrideItem] = []
