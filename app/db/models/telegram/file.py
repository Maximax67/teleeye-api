from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import JSON, BigInteger, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.enums import FileType
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.telegram.bot_file import BotFile


class TelegramFile(Base):
    __tablename__ = "telegram_files"

    file_unique_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )

    file_type: Mapped[FileType] = mapped_column(
        Enum(FileType, name="telegram_file_type"),
        nullable=False,
    )
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(nullable=True)
    other_data: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    bots: Mapped[List["BotFile"]] = relationship(
        back_populates="file", passive_deletes=True
    )

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "file_unique_id": self.file_unique_id,
            "file_type": self.file_type.value,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
        }

        if self.other_data:
            data.update(self.other_data)

        return data
