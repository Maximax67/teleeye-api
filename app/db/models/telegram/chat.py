from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import (
    JSON,
    BigInteger,
    Enum,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.enums import ChatType
from app.db.base import Base
from app.db.mixins import TimestampMixin
from app.db.models.telegram.message import TelegramMessage
from app.db.models.telegram.file import TelegramFile

if TYPE_CHECKING:
    from app.db.models.telegram.read_messages import ReadMessages


class TelegramChat(Base, TimestampMixin):
    __tablename__ = "telegram_chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[ChatType] = mapped_column(
        Enum(ChatType, name="telegram_chat_type"),
        nullable=False,
    )

    # Basic information
    title: Mapped[Optional[str]] = mapped_column(nullable=True)
    username: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )
    first_name: Mapped[Optional[str]] = mapped_column(nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(nullable=True)

    is_forum: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_direct_messages: Mapped[bool] = mapped_column(default=False, nullable=True)

    # Detailed information
    personal_chat_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="SET NULL"),
        nullable=True,
    )
    personal_chat: Mapped[Optional["TelegramChat"]] = relationship(
        foreign_keys=[personal_chat_id],
        uselist=False,
    )

    parent_chat_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_chat: Mapped[Optional["TelegramChat"]] = relationship(
        foreign_keys=[parent_chat_id],
        uselist=False,
    )

    pinned_message_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )

    photo_small_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("telegram_files.file_unique_id", ondelete="SET NULL"),
        nullable=True,
    )
    photo_big_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey("telegram_files.file_unique_id", ondelete="SET NULL"),
        nullable=True,
    )

    photo_small: Mapped[Optional["TelegramFile"]] = relationship(
        "TelegramFile",
        foreign_keys=[photo_small_id],
        uselist=False,
    )
    photo_big: Mapped[Optional["TelegramFile"]] = relationship(
        "TelegramFile",
        foreign_keys=[photo_big_id],
        uselist=False,
    )

    messages: Mapped[List["TelegramMessage"]] = relationship(
        back_populates="chat",
        foreign_keys=[TelegramMessage.chat_id],
    )

    other_data: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=True)

    read_messages: Mapped[List["ReadMessages"]] = relationship(
        back_populates="chat", passive_deletes=True
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "is_forum": self.is_forum,
            "is_direct_messages": self.is_direct_messages,
        }
