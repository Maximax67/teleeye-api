from typing import TYPE_CHECKING
from sqlalchemy import BigInteger, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.enums import UserBotRole
from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.db.models.telegram.bot import Bot
    from app.db.models.user import User


class UserBot(Base, TimestampMixin):
    __tablename__ = "user_bots"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    bot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bots.id", ondelete="CASCADE"), primary_key=True
    )

    role: Mapped[UserBotRole] = mapped_column(
        Enum(UserBotRole, name="user_bot_role"),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="bots")
    bot: Mapped["Bot"] = relationship(back_populates="users")
