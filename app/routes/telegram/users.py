from typing import Any, Dict, Union
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.core.logger import logger
from app.core.dependencies import require_authorization
from app.db.models.telegram.bot import Bot
from app.db.models.telegram.bot_message import BotMessage
from app.db.models.telegram.message import TelegramMessage
from app.db.models.user_bot import UserBot
from app.db.session import get_db
from app.schemas.auth import AuthorizedUser
from app.services.telegram.bots import get_telegram_bot_from_encrypted
from app.core.enums import UserRole

router = APIRouter(prefix="/users", tags=["telegram-users"])

common_responses: Dict[Union[int, str], Dict[str, Any]] = {
    404: {
        "description": "User not found or avatar not available",
        "content": {"application/json": {"example": {"detail": "Avatar not found"}}},
    },
    403: {"description": "Forbidden", "content": {"application/json": {"example": {"detail": "Forbidden"}}}},
    401: {"description": "Unauthorized", "content": {"application/json": {"example": {"detail": "Invalid token"}}}},
    502: {"description": "Telegram API Error", "content": {"application/json": {"example": {"detail": "Telegram API Error"}}}},
}


@router.get("/{user_id}/avatar", responses=common_responses)
@limiter.limit("15/minute")
async def get_user_avatar(
    user_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Get a Telegram user's profile photo by finding any bot that has seen messages from them.
    """
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)

    # Find a bot that has seen messages from this user
    if is_admin:
        q = (
            select(Bot.id, Bot.token)
            .join(BotMessage, Bot.id == BotMessage.bot_id)
            .join(
                TelegramMessage,
                (TelegramMessage.id == BotMessage.message_id)
                & (TelegramMessage.chat_id == BotMessage.chat_id),
            )
            .where(TelegramMessage.from_user_id == user_id)
            .order_by(BotMessage.timestamp.desc())
            .limit(1)
        )
    else:
        q = (
            select(Bot.id, Bot.token)
            .join(BotMessage, Bot.id == BotMessage.bot_id)
            .join(
                TelegramMessage,
                (TelegramMessage.id == BotMessage.message_id)
                & (TelegramMessage.chat_id == BotMessage.chat_id),
            )
            .join(UserBot, UserBot.bot_id == Bot.id)
            .where(
                TelegramMessage.from_user_id == user_id,
                UserBot.user_id == current_user.id,
            )
            .order_by(BotMessage.timestamp.desc())
            .limit(1)
        )

    result = await db.execute(q)
    row = result.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="User not found or not accessible")

    bot_id, token = row
    telegram_bot = get_telegram_bot_from_encrypted(bot_id, token)

    try:
        # get_chat on a user_id returns private chat info including profile photo
        chat_info = await telegram_bot.get_chat(user_id)
    except Exception as e:
        logger.error(f"Failed to get chat info for user {user_id}: {e}")
        raise HTTPException(status_code=502, detail="Telegram API Error")

    if not chat_info.photo:
        raise HTTPException(status_code=404, detail="Avatar not found")

    try:
        avatar_file = await chat_info.photo.get_small_file()
        avatar_bytes = await avatar_file.download_as_bytearray()
    except Exception as e:
        logger.error(f"Failed to download avatar for user {user_id}: {e}")
        raise HTTPException(status_code=502, detail="Telegram API Error")

    return Response(content=bytes(avatar_bytes), media_type="image/jpeg")
