from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot as TelegramBot

from app.core.crypto import crypto
from app.core.settings import settings
from app.core.enums import CryptoInfo, UserBotRole, UserRole
from app.db.models.telegram.bot import Bot
from app.db.models.telegram.bot_message import BotMessage
from app.db.models.telegram.bot_file import BotFile
from app.db.models.user_bot import UserBot
from app.schemas.auth import AuthorizedUser
from app.schemas.telegram.bot import BotResponse


def get_telegram_bot(token: str) -> TelegramBot:
    return TelegramBot(
        token,
        base_url=str(settings.TELEGRAM_API_URL),
        base_file_url=str(settings.TELEGRAM_API_FILE_URL),
    )


def get_telegram_bot_from_encrypted(bot_id: int, token: bytes) -> TelegramBot:
    bot_token_stripped = crypto.decrypt_data(token, CryptoInfo.BOT_TOKEN)
    bot_token = f"{bot_id}:{bot_token_stripped}"
    telegram_bot = get_telegram_bot(bot_token)

    return telegram_bot


async def get_user_bot(
    bot_id: int,
    current_user: AuthorizedUser,
    db: AsyncSession,
    preload_webhook: bool = False,
    preload_telegram_user: bool = False,
) -> Tuple[Bot, UserBotRole]:
    stmt = (
        select(Bot, UserBot.role)
        .outerjoin(Bot.users.and_(UserBot.user_id == current_user.id))
        .where(Bot.id == bot_id)
    )

    options = []
    if preload_webhook:
        options.append(selectinload(Bot.webhook))
    if preload_telegram_user:
        options.append(selectinload(Bot.telegram_user))

    if options:
        stmt = stmt.options(*options)

    row = (await db.execute(stmt)).one_or_none()

    if not row:
        if current_user.role in (UserRole.ADMIN, UserRole.GOD):
            raise HTTPException(status_code=404, detail="Bot not found")

        raise HTTPException(status_code=403, detail="Forbidden")

    bot, user_role = row
    if not user_role and current_user.role not in (UserRole.ADMIN, UserRole.GOD):
        raise HTTPException(status_code=403, detail="Forbidden")

    return (bot, user_role)


async def get_userbot_mapping(
    session: AsyncSession, bot_id: int, user_id: int
) -> Optional[UserBot]:
    stmt = select(UserBot).where(UserBot.bot_id == bot_id, UserBot.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


def make_bot_response(bot: Bot, role: Optional[UserBotRole]) -> BotResponse:
    return BotResponse(
        id=bot.id,
        first_name=bot.telegram_user.first_name,
        last_name=bot.telegram_user.last_name,
        username=bot.telegram_user.username,  # type: ignore
        can_join_groups=bot.can_join_groups,
        can_read_all_group_messages=bot.can_read_all_group_messages,
        supports_inline_queries=bot.supports_inline_queries,
        can_connect_to_business=bot.can_connect_to_business,
        has_main_web_app=bot.has_main_web_app,
        role=role,
    )


async def get_user_bots_count(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count(UserBot.bot_id)).where(UserBot.user_id == user_id)
    return (await session.execute(stmt)).scalar_one()


async def get_bot_users_count(session: AsyncSession, bot_id: int) -> int:
    stmt = select(func.count(UserBot.user_id)).where(UserBot.bot_id == bot_id)
    return (await session.execute(stmt)).scalar_one()


async def remove_extra_bot_links(
    session: AsyncSession, bot_id: int, limit: int
) -> None:
    subq = (
        select(UserBot.user_id, UserBot.bot_id)
        .where(UserBot.bot_id == bot_id)
        .order_by(UserBot.updated_at.desc())
        .offset(limit)
    )
    await session.execute(
        delete(UserBot).where(tuple_(UserBot.user_id, UserBot.bot_id).in_(subq))
    )


async def get_bot_by_id(
    db: AsyncSession, bot_id: int, current_user: AuthorizedUser
) -> TelegramBot:
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)

    if is_admin:
        q = select(Bot.id, Bot.token).where(Bot.id == bot_id).limit(1)

        result = await db.execute(q)
        row = result.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Bot not found")
    else:
        q = (
            select(Bot.id, Bot.token)
            .join(UserBot, UserBot.bot_id == Bot.id)
            .where(Bot.id == bot_id, UserBot.user_id == current_user.id)
            .limit(1)
        )

        result = await db.execute(q)
        row = result.fetchone()

        if row is None:
            raise HTTPException(status_code=403, detail="Forbidden")

    bot_id, token = row
    telegram_bot = get_telegram_bot_from_encrypted(bot_id, token)

    return telegram_bot


async def get_bot_by_chat(
    db: AsyncSession, chat_id: int, current_user: AuthorizedUser
) -> Tuple[int, TelegramBot]:
    if current_user.role in (UserRole.ADMIN, UserRole.GOD):
        q = (
            select(Bot.id, Bot.token)
            .join(BotMessage, Bot.id == BotMessage.bot_id)
            .where(BotMessage.chat_id == chat_id)
            .order_by(BotMessage.timestamp.desc())
            .limit(1)
        )
    else:
        q = (
            select(Bot.id, Bot.token)
            .join(BotMessage, Bot.id == BotMessage.bot_id)
            .join(UserBot, UserBot.bot_id == Bot.id)
            .where(BotMessage.chat_id == chat_id, UserBot.user_id == current_user.id)
            .order_by(BotMessage.timestamp.desc())
            .limit(1)
        )

    result = await db.execute(q)
    row = result.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail="Chat not found or no accessible messages"
        )

    bot_id, token = row
    telegram_bot = get_telegram_bot_from_encrypted(bot_id, token)

    return (bot_id, telegram_bot)


async def get_file_and_bot_token(
    db: AsyncSession,
    file_unique_id: str,
    current_user: AuthorizedUser,
    bot_id: Optional[int] = None,
    preload_file: Optional[bool] = False,
) -> Tuple[BotFile, bytes]:
    if current_user.role in (UserRole.ADMIN, UserRole.GOD):
        q = (
            select(Bot.token, BotFile)
            .join(BotFile, Bot.id == BotFile.bot_id)
            .where(BotFile.file_unique_id == file_unique_id)
            .order_by(BotFile.timestamp.desc())
            .limit(1)
        )
    else:
        q = (
            select(Bot.token, BotFile)
            .join(BotFile, Bot.id == BotFile.bot_id)
            .join(UserBot, UserBot.bot_id == Bot.id)
            .where(
                BotFile.file_unique_id == file_unique_id,
                UserBot.user_id == current_user.id,
            )
            .order_by(BotFile.timestamp.desc())
            .limit(1)
        )

    if bot_id:
        q = q.where(Bot.id == bot_id)

    if preload_file:
        q = q.options(joinedload(BotFile.file))

    result = await db.execute(q)
    row = result.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail="File not found or bot does not have access to it"
        )

    token: bytes = row[0]
    bot_file: BotFile = row[1]

    return (bot_file, token)
