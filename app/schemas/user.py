from typing import Optional
from pydantic import BaseModel, ConfigDict

from app.core.enums import UserRole


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    is_banned: bool
    email_verified: bool
    role: UserRole

    model_config = ConfigDict(from_attributes=True)


class UserUpdateRequest(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    is_banned: Optional[bool] = None
    email_verified: Optional[bool] = None
    role: Optional[UserRole] = None
