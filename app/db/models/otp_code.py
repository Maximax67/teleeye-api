from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.enums import OtpCodeType
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.user import User


class OtpCode(Base):
    __tablename__ = "otp_codes"
    __table_args__ = (UniqueConstraint("user_id", "type", name="uq_otp_user_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(nullable=False)
    type: Mapped[OtpCodeType] = mapped_column(
        Enum(OtpCodeType, name="otp_code_type"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="otp_codes")
