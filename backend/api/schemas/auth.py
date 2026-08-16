from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    # The workspace code unseals the company's database key, so it is a
    # credential and is validated like one.
    workspace_code: str = Field(min_length=4, max_length=64)
    company: str = Field(min_length=2, max_length=120)
    email: EmailStr
    # 10, matching AuthService.MIN_PASSWORD_LENGTH. It used to be 8 here, so
    # this schema accepted a length no account could ever have been created
    # with — the mismatch could only ever produce a confusing rejection later.
    password: str = Field(min_length=10, max_length=200)


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


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


class PasswordResetRequest(BaseModel):
    """Spending a reset link. The token is the credential; there is no email
    field, because accepting one would let a caller aim a valid token at a
    different account."""

    new_password: str = Field(min_length=10, max_length=200)


class PasswordChangeResponse(BaseModel):
    success: bool
    message: str
