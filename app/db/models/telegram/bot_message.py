from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import BigInteger, ForeignKey, ForeignKeyConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.telegram.bot import Bot
    from app.db.models.telegram.message import TelegramMessage


class BotMessage(Base):
    __tablename__ = "bot_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chat_id", "message_id"],
            ["telegram_messages.chat_id", "telegram_messages.id"],
            ondelete="CASCADE",
        ),
    )

    bot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bots.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )

    bot: Mapped["Bot"] = relationship(back_populates="messages")
    message: Mapped["TelegramMessage"] = relationship(back_populates="bots")
