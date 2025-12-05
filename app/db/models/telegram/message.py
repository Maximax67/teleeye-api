from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.enums import MessageType
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.telegram.bot_message import BotMessage
    from app.db.models.telegram.chat import TelegramChat
    from app.db.models.telegram.user import TelegramUser


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    __table_args__ = (Index("ix_chat_id_message_type", "chat_id", "message_type"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
        nullable=False,
    )
    chat: Mapped["TelegramChat"] = relationship(foreign_keys=[chat_id], uselist=False)

    message_type: Mapped[MessageType] = mapped_column(
        Enum(MessageType, name="message_type"), nullable=False
    )

    message_thread_id: Mapped[int] = mapped_column(
        nullable=True,
        index=True,
    )

    text: Mapped[str] = mapped_column(nullable=True)
    caption: Mapped[str] = mapped_column(nullable=True)

    from_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    from_user: Mapped[Optional["TelegramUser"]] = relationship(
        back_populates="messages", foreign_keys=[from_user_id], uselist=False
    )

    sender_chat_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("telegram_chats.id", ondelete="SET NULL"),
        nullable=True,
    )
    sender_chat: Mapped[Optional["TelegramChat"]] = relationship(
        foreign_keys=[sender_chat_id],
        uselist=False,
    )

    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edit_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sender_boost_count: Mapped[Optional[int]] = mapped_column(nullable=True)

    sender_business_bot_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    sender_business_bot: Mapped[Optional["TelegramUser"]] = relationship(
        foreign_keys=[sender_business_bot_id],
        uselist=False,
    )

    business_connection_id: Mapped[Optional[str]] = mapped_column(nullable=True)

    is_topic_message: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_automatic_forward: Mapped[bool] = mapped_column(default=False, nullable=False)

    has_media_spoiler: Mapped[bool] = mapped_column(default=False, nullable=False)
    has_protected_content: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_from_offline: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_paid_post: Mapped[bool] = mapped_column(default=False, nullable=False)

    author_signature: Mapped[Optional[str]] = mapped_column(nullable=True)
    paid_star_count: Mapped[Optional[int]] = mapped_column(nullable=True)

    other_data: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=True)

    bots: Mapped[List["BotMessage"]] = relationship(
        back_populates="message", passive_deletes=True
    )

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "message_id": self.id,
            "chat": {"id": self.chat_id, "type": ""},
            "message_thread_id": self.message_thread_id,
            "message_type": self.message_type.value,
            "text": self.text,
            "caption": self.caption,
            "from": (
                {"id": self.from_user_id, "first_name": "", "is_bot": False}
                if self.from_user_id
                else None
            ),
            "sender_chat": (
                {"id": self.sender_chat_id, "first_name": "", "is_bot": False}
                if self.sender_chat_id
                else None
            ),
            "sender_boost_count": self.sender_boost_count,
            "sender_business_bot": (
                {"id": self.sender_business_bot_id, "first_name": "", "is_bot": True}
                if self.sender_business_bot_id
                else None
            ),
            "date": self.date.timestamp(),
            "edit_date": self.edit_date.timestamp() if self.edit_date else None,
            "business_connection_id": self.business_connection_id,
            "is_topic_message": self.is_topic_message,
            "is_automatic_forward": self.is_automatic_forward,
            "has_media_spoiler": self.has_media_spoiler,
            "has_protected_content": self.has_protected_content,
            "is_from_offline": self.is_from_offline,
            "is_paid_post": self.is_paid_post,
            "author_signature": self.author_signature,
            "paid_star_count": self.paid_star_count,
        }

        if self.other_data:
            data.update(self.other_data)

        return data
