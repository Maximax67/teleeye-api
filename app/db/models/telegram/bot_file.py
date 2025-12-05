from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.telegram.bot import Bot
    from app.db.models.telegram.file import TelegramFile


class BotFile(Base):
    __tablename__ = "bot_files"

    bot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bots.id", ondelete="CASCADE"),
        primary_key=True,
    )
    file_unique_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("telegram_files.file_unique_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    file_id: Mapped[str] = mapped_column(String, nullable=False)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    bot: Mapped["Bot"] = relationship(back_populates="files")
    file: Mapped["TelegramFile"] = relationship(back_populates="bots")
