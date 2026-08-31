from typing import Literal

from pydantic import BaseModel, Field


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    code: str = Field(min_length=2, max_length=60, pattern=r"^[a-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=300)
    permission_codes: list[str] = Field(default_factory=list, max_length=200)


class RoleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    permission_codes: list[str] | None = Field(default=None, max_length=200)


class UserCreateRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    role_id: int
    branch_id: int | None = None


class UserAssignmentRequest(BaseModel):
    role_id: int
    branch_id: int | None = None
    status: str = Field(default="active", pattern=r"^(active|disabled)$")


class BranchCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=40)


class BranchUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=40)
    status: Literal["active", "disabled"] | None = None
