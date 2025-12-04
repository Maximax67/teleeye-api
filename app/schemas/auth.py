import re
from typing import List, Optional, Annotated
from datetime import datetime
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.core.settings import settings
from app.core.enums import UserRole
from app.core.constants import PASSWORD_REGEX, USERNAME_REGEX
from app.db.models.user import User


class RegisterRequest(BaseModel):
    email: EmailStr
    username: Annotated[str, Field(min_length=4, max_length=16)]
    password: Annotated[str, Field(min_length=8, max_length=32)]

    @field_validator("username", mode="before")
    def strip_whitespace(cls: "RegisterRequest", v: Optional[str]) -> Optional[str]:
        return v.strip() if isinstance(v, str) else v

    @field_validator("username")
    def validate_username(cls: "RegisterRequest", v: Optional[str]) -> str:
        if not v or not re.fullmatch(USERNAME_REGEX, v):
            raise ValueError("Invalid format")

        return v

    @field_validator("password")
    def validate_password(cls: "RegisterRequest", v: str) -> str:
        if not re.fullmatch(PASSWORD_REGEX, v):
            raise ValueError(
                "Password must contain at least 1 uppercase letter, "
                "1 lowercase letter, 1 digit, and be 8-32 characters long."
            )

        return v


class LoginRequest(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[Annotated[str, Field(min_length=4, max_length=16)]] = None
    password: Annotated[str, Field(min_length=8, max_length=32)]

    @field_validator("username", mode="before")
    def strip_whitespace(cls: "LoginRequest", v: Optional[str]) -> Optional[str]:
        return v.strip() if isinstance(v, str) else v

    @field_validator("username")
    def validate_username(cls: "LoginRequest", v: Optional[str]) -> Optional[str]:
        if v and not re.fullmatch(USERNAME_REGEX, v):
            raise ValueError("Invalid format")

        return v

    @field_validator("password")
    def validate_password(cls: "LoginRequest", v: str) -> str:
        if not re.fullmatch(PASSWORD_REGEX, v):
            raise ValueError(
                "Password must contain at least 1 uppercase letter, "
                "1 lowercase letter, 1 digit, and be 8-32 characters long."
            )

        return v

    @model_validator(mode="after")
    def check_email_or_username(self) -> "LoginRequest":
        if not self.email and not self.username:
            raise ValueError("Either 'email' or 'username' must be provided.")

        if self.email and self.username:
            raise ValueError("'email' and 'username' cannot both be provided.")

        return self


class TokensResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int


class PasswordForgotRequest(BaseModel):
    email: EmailStr


class PasswordChangeRequest(BaseModel):
    email: EmailStr
    old_password: Annotated[str, Field(min_length=8, max_length=32)]
    new_password: Annotated[str, Field(min_length=8, max_length=32)]


class PasswordResetRequest(BaseModel):
    otp: Annotated[
        str, Field(min_length=settings.OTP_LENGTH, max_length=settings.OTP_LENGTH)
    ]
    email: EmailStr
    new_password: Annotated[str, Field(min_length=8, max_length=32)]


class EmailChangeRequest(BaseModel):
    new_email: EmailStr


class EmailVerifyRequest(BaseModel):
    otp: Annotated[
        str, Field(min_length=settings.OTP_LENGTH, max_length=settings.OTP_LENGTH)
    ]
    user_id: Annotated[int, Field(ge=1)]


class SessionInfo(BaseModel):
    id: int
    name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_current: bool


class SessionListResponse(BaseModel):
    sessions: List[SessionInfo]
    limit: int


class AuthorizedUser(BaseModel):
    id: int
    role: UserRole
    is_email_verified: bool
    jti: str


class AuthorizedUserDb(BaseModel):
    user: User
    jti: str

    model_config = ConfigDict(arbitrary_types_allowed=True)
