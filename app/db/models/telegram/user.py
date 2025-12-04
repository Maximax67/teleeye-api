from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.mixins import TimestampMixin
from app.db.models.telegram.message import TelegramMessage

if TYPE_CHECKING:
    from app.db.models.telegram.bot import Bot


class TelegramUser(Base, TimestampMixin):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    first_name: Mapped[str] = mapped_column(nullable=False)
    last_name: Mapped[Optional[str]] = mapped_column(nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    language_code: Mapped[Optional[str]] = mapped_column(nullable=True)
    is_premium: Mapped[Optional[bool]] = mapped_column(nullable=False)
    is_bot: Mapped[bool] = mapped_column(nullable=False)

    bot: Mapped[Optional["Bot"]] = relationship(
        back_populates="telegram_user", uselist=False
    )
    messages: Mapped[List["TelegramMessage"]] = relationship(
        foreign_keys=[TelegramMessage.from_user_id], back_populates="from_user"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "is_bot": self.is_bot,
            "is_premium": self.is_premium,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "username": self.username,
            "language_code": self.language_code,
        }
