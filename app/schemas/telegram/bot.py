import re
from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)
from typing import Annotated, List, Optional

from telegram import Update

from app.core.constants import BOT_TOKEN_REGEX, USERNAME_REGEX, WEBHOOK_SECRET_REGEX
from app.core.enums import UserBotRole


class BotTokenRequest(BaseModel):
    token: str = Field(..., min_length=44, max_length=46)

    @field_validator("token")
    def validate_telegram_token(cls: "BotTokenRequest", v: Optional[str]) -> str:
        if not v or not re.match(BOT_TOKEN_REGEX, v):
            raise ValueError("Invalid Telegram bot token format")

        return v


class BotResponse(BaseModel):
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: str
    can_join_groups: bool
    can_read_all_group_messages: bool
    supports_inline_queries: bool
    can_connect_to_business: bool
    has_main_web_app: bool
    role: Optional[UserBotRole] = None


class BotUserResponse(BaseModel):
    id: int
    username: str
    is_banned: bool
    bot_role: UserBotRole


class BotUsersResponse(BaseModel):
    users: List[BotUserResponse]
    limit: int


class BotListResponse(BaseModel):
    bots: List[BotResponse]
    limit: int


class UserBotUpdateRequest(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[Annotated[str, Field(min_length=4, max_length=16)]] = None
    role: UserBotRole

    @field_validator("username", mode="before")
    def strip_whitespace(
        cls: "UserBotUpdateRequest", v: Optional[str]
    ) -> Optional[str]:
        return v.strip() if isinstance(v, str) else v

    @field_validator("username")
    def validate_username(
        cls: "UserBotUpdateRequest", v: Optional[str]
    ) -> Optional[str]:
        if v and not re.fullmatch(USERNAME_REGEX, v):
            raise ValueError("Invalid format")

        return v

    @model_validator(mode="after")
    def check_email_or_username(self) -> "UserBotUpdateRequest":
        if not self.email and not self.username:
            raise ValueError("Either 'email' or 'username' must be provided.")

        if self.email and self.username:
            raise ValueError("'email' and 'username' cannot both be provided.")

        return self


class WebhookCreateRequest(BaseModel):
    url: Optional[HttpUrl] = None
    max_connections: Optional[int] = Field(None, ge=1, le=100)
    allowed_updates: Optional[List[str]] = None
    drop_pending_updates: Optional[bool] = None
    secret_token: Optional[str] = Field(None, min_length=1, max_length=256)

    @field_validator("secret_token")
    def validate_secret_token(
        cls: "WebhookCreateRequest", v: Optional[str]
    ) -> Optional[str]:
        if v is not None and not re.match(WEBHOOK_SECRET_REGEX, v):
            raise ValueError("Invalid secret token format")

        return v

    @field_validator("allowed_updates")
    def validate_allowed_updates(
        cls: "WebhookCreateRequest", v: Optional[List[str]]
    ) -> Optional[List[str]]:
        if v is None:
            return v

        if len(v) != len(set(v)):
            raise ValueError("allowed_updates must not contain duplicate values")

        invalid = [item for item in v if item not in Update.ALL_TYPES]
        if invalid:
            raise ValueError(
                f"Invalid update type{'s' if len(invalid) > 1 else ''}: {', '.join(invalid)}."
            )

        return v
