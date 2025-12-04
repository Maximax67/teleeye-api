from typing import TYPE_CHECKING, Optional
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class Session(Base, TimestampMixin):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    refresh_jti: Mapped[str] = mapped_column(unique=True, nullable=False)
    access_jti: Mapped[str] = mapped_column(nullable=False)
    name: Mapped[Optional[str]] = mapped_column(nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")
