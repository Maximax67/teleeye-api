from typing import TYPE_CHECKING
from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user import User
    from app.db.models.telegram.chat import TelegramChat


class ReadMessages(Base, TimestampMixin):
    __tablename__ = "read_messages"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="CASCADE"),
        primary_key=True,
    )

    message_thread_id: Mapped[int] = mapped_column(
        BigInteger, default=1, nullable=False
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    user: Mapped["User"] = relationship(back_populates="read_messages")
    chat: Mapped["TelegramChat"] = relationship(back_populates="read_messages")
