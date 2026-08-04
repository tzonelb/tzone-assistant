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
    permissions: list[str] = []


class LogoutResponse(BaseModel):
    success: bool
    message: str