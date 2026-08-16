from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    # The workspace code unseals the company's database key, so it is a
    # credential and is validated like one.
    workspace_code: str = Field(min_length=4, max_length=64)
    company: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict
    permissions: list[str] = []


class CurrentUserResponse(BaseModel):
    user: dict
    companies: list[dict]
    permissions: list[str] = []


class LogoutResponse(BaseModel):
    success: bool
    message: str
