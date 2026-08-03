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


class SuperAdminLoginRequest(BaseModel):
    email: str = Field(
        min_length=3,
        max_length=254,
    )

    password: str = Field(
        min_length=8,
        max_length=200,
    )


class LoginResponse(BaseModel):
    # When 2FA is required, access_token/user are omitted and
    # twofa_required + pending_token are returned instead.
    access_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None
    user: dict | None = None
    twofa_required: bool = False
    pending_token: str | None = None


class TwoFactorVerifyRequest(BaseModel):
    pending_token: str
    code: str = Field(min_length=6, max_length=6)


class TwoFactorEnrollConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class TwoFactorDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=6)


class CurrentUserResponse(BaseModel):
    user: dict
    companies: list[dict]


class LogoutResponse(BaseModel):
    success: bool
    message: str