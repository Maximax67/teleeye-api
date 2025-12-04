from typing import TYPE_CHECKING, List
from sqlalchemy import Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.enums import UserRole
from app.db.base import Base
from app.db.mixins import TimestampMixin
from app.db.models.telegram.read_messages import ReadMessages

if TYPE_CHECKING:
    from app.db.models.session import Session
    from app.db.models.user_bot import UserBot
    from app.db.models.otp_code import OtpCode


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(unique=True, index=True, nullable=False)

    is_banned: Mapped[bool] = mapped_column(default=False, nullable=False)
    password_hash: Mapped[str] = mapped_column(nullable=False)
    email_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), nullable=False, default=UserRole.USER
    )

    sessions: Mapped[List["Session"]] = relationship(
        back_populates="user", passive_deletes=True
    )
    bots: Mapped[List["UserBot"]] = relationship(
        back_populates="user", passive_deletes=True
    )
    otp_codes: Mapped[List["OtpCode"]] = relationship(
        back_populates="user", passive_deletes=True
    )
    read_messages: Mapped[List["ReadMessages"]] = relationship(
        back_populates="user", passive_deletes=True
    )
