from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    # Company + email + password are the sign-in credentials. The workspace code
    # is no longer part of login (the company key is also wrapped by the server
    # master key, so the code was only a second factor); an employee who wants a
    # second factor turns on TOTP instead.
    company: str = Field(min_length=2, max_length=120)
    email: EmailStr
    # 10, matching AuthService.MIN_PASSWORD_LENGTH. It used to be 8 here, so
    # this schema accepted a length no account could ever have been created
    # with — the mismatch could only ever produce a confusing rejection later.
    password: str = Field(min_length=10, max_length=200)
    # Optional: two-factor authentication is the employee's own choice on a
    # company account. The endpoint requires it only when the account has it
    # turned on.
    totp_code: str | None = Field(default=None, max_length=32)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict
    permissions: list[str] = []
    # The double-submit partner to the session cookie. Returned in the body so
    # a client does not have to parse cookies to find it; the cookie copy is
    # what makes the comparison possible.
    csrf_token: str | None = None


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


class PasswordForgotRequest(BaseModel):
    """Asking for a reset link. Only an email; the endpoint answers the same way
    whether or not it matches an account, so nothing here reveals who exists."""

    email: EmailStr


class PasswordResetRequest(BaseModel):
    """Spending a reset link. The token is the credential; there is no email
    field, because accepting one would let a caller aim a valid token at a
    different account."""

    new_password: str = Field(min_length=10, max_length=200)


class PasswordChangeResponse(BaseModel):
    success: bool
    message: str
