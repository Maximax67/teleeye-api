from typing import TYPE_CHECKING, Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import BigInteger, ForeignKey, LargeBinary
from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.db.models.telegram.bot import Bot


class BotWebhook(Base, TimestampMixin):
    __tablename__ = "bot_webhooks"

    bot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bots.id", ondelete="CASCADE"),
        primary_key=True,
    )

    secret_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    redirect_url: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    redirect_token: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    bot: Mapped["Bot"] = relationship(back_populates="webhook", uselist=False)
