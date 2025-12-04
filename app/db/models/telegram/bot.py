from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import BigInteger, ForeignKey, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user_bot import UserBot
    from app.db.models.telegram.bot_message import BotMessage
    from app.db.models.telegram.bot_file import BotFile
    from app.db.models.telegram.bot_webhook import BotWebhook
    from app.db.models.telegram.user import TelegramUser


class Bot(Base, TimestampMixin):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    can_join_groups: Mapped[bool] = mapped_column(nullable=False)
    can_read_all_group_messages: Mapped[bool] = mapped_column(nullable=False)
    supports_inline_queries: Mapped[bool] = mapped_column(nullable=False)
    can_connect_to_business: Mapped[bool] = mapped_column(nullable=False)
    has_main_web_app: Mapped[bool] = mapped_column(nullable=False)

    users: Mapped[List["UserBot"]] = relationship(
        back_populates="bot", passive_deletes=True
    )
    webhook: Mapped[Optional["BotWebhook"]] = relationship(
        back_populates="bot", uselist=False, passive_deletes=True
    )
    telegram_user: Mapped["TelegramUser"] = relationship(
        back_populates="bot", uselist=False, passive_deletes=True
    )
    messages: Mapped[List["BotMessage"]] = relationship(
        back_populates="bot", passive_deletes=True
    )
    files: Mapped[List["BotFile"]] = relationship(
        back_populates="bot", passive_deletes=True
    )
