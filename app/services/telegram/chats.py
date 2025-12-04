from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.telegram.message import TelegramMessage
from app.db.models.user_bot import UserBot


async def parse_bot_param(bot: Optional[str]) -> Optional[Set[int]]:
    if not bot:
        return None
    try:
        return {int(x.strip()) for x in bot.split(",") if x.strip()}
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid bot parameter; must be comma-separated integers",
        )


async def check_bot_access(
    db: AsyncSession, user_id: int, requested_bot_ids: Optional[Set[int]], is_admin: bool
) -> None:
    if not requested_bot_ids or is_admin:
        return

    q = await db.execute(
        select(func.count(UserBot.bot_id.distinct())).where(
            UserBot.user_id == user_id,
            UserBot.bot_id.in_(requested_bot_ids),
        )
    )
    owned_count = q.scalar_one()
    if owned_count != len(requested_bot_ids):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: you don't have access to one or more requested bots",
        )


def serialize_message(message: TelegramMessage) -> Dict[str, Any]:
    data = message.to_dict()
    data.pop("chat", None)

    if message.from_user:
        data["from"] = message.from_user.to_dict()
    if message.sender_chat:
        data["sender_chat"] = message.sender_chat.to_dict()
    if message.sender_business_bot:
        data["sender_business_bot"] = message.sender_business_bot.to_dict()

    return data


def get_message_options() -> List[Any]:
    return [
        joinedload(TelegramMessage.from_user),
        joinedload(TelegramMessage.sender_chat),
        joinedload(TelegramMessage.sender_business_bot),
    ]
