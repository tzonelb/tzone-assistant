from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    company: str = Field(
        min_length=2,
        max_length=120,
    )

    email: str = Field(
        min_length=3,
        max_length=254,
    )

    password: str = Field(
        min_length=8,
        max_length=200,
    )


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class CurrentUserResponse(BaseModel):
    user: dict
    companies: list[dict]
    # Effective permission codes for the caller's active company. "*" means
    # all-access (owner role or super admin). Lets the frontend hide/disable
    # UI the same way the backend gates routes.
    permissions: list[str] = []


class LogoutResponse(BaseModel):
    success: bool
    message: str