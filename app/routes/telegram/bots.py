import json
import secrets
from urllib.parse import urljoin
from typing import Dict, Any, Union, List, Optional

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Header,
    Query,
    Request,
    Response,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Message, Update
from telegram.error import TelegramError

from app.core.crypto import crypto
from app.core.settings import settings
from app.core.limiter import limiter
from app.core.logger import logger
from app.db.models.telegram.bot import Bot
from app.db.models.telegram.bot_webhook import BotWebhook
from app.db.models.telegram.user import TelegramUser
from app.db.models.user import User
from app.db.models.user_bot import UserBot
from app.db.session import get_db
from app.core.enums import CryptoInfo, UserBotRole, UserRole
from app.schemas.common_responses import DetailResponse
from app.schemas.telegram.bot import (
    BotListResponse,
    BotTokenRequest,
    BotResponse,
    BotUserResponse,
    BotUsersResponse,
    UserBotUpdateRequest,
    WebhookCreateRequest,
)
from app.core.dependencies import require_authorization
from app.schemas.auth import AuthorizedUser
from app.services.telegram.bots import (
    get_bot_users_count,
    get_user_bot,
    get_user_bots_count,
    get_userbot_mapping,
    get_telegram_bot,
    make_bot_response,
    remove_extra_bot_links,
)
from app.services.telegram.entity_logger import log_object, update_message
from app.services.telegram.logger import proxy_file_request, proxy_request

router = APIRouter(prefix="/bots", tags=["telegram-bots"])

common_responses: Dict[Union[int, str], Dict[str, Any]] = {
    409: {
        "description": "User is already in relationship with bot",
        "content": {
            "application/json": {
                "example": {"detail": "User is already in relationship with bot"}
            }
        },
    },
    404: {
        "description": "Bot not found",
        "content": {"application/json": {"example": {"detail": "Bot not found"}}},
    },
    403: {
        "description": "Forbidden",
        "content": {"application/json": {"example": {"detail": "Forbidden"}}},
    },
    401: {
        "description": "Unauthorized",
        "content": {"application/json": {"example": {"detail": "Invalid token"}}},
    },
    400: {
        "descripton": "Invalid bot token or Telegram API error",
        "content": {
            "application/json": {
                "example": {"detail": "Invalid bot token or Telegram API error"}
            }
        },
    },
}

user_op_responses: Dict[Union[int, str], Dict[str, Any]] = {
    401: common_responses[401],
    403: common_responses[403],
    404: common_responses[404],
    409: common_responses[409],
}


webhook_not_found_response = {
    "description": "Bot or webhook not found",
    "content": {
        "application/json": {
            "examples": {
                "bot_not_found": {
                    "summary": "Bot not found",
                    "value": {"detail": "Bot not found"},
                },
                "webhook_not_found": {
                    "summary": "Webhook not found",
                    "value": {"detail": "Webhook not found"},
                },
            }
        }
    },
}


@router.post(
    "",
    response_model=BotResponse,
    responses={400: common_responses[400], 401: common_responses[401]},
)
@limiter.limit("5/minute")
async def create_or_transfer_bot(
    body: BotTokenRequest,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> BotResponse:
    telegram_bot = get_telegram_bot(body.token)

    try:
        me = await telegram_bot.get_me()
    except TelegramError as e:
        logger.error(e)
        raise HTTPException(status_code=400, detail="Telegram API error")

    token_stripped = body.token.split(":", 1)[1]
    token_encrypted = crypto.encrypt_data(token_stripped, CryptoInfo.BOT_TOKEN)
    bot_username: str = me.username  # type: ignore

    q = await db.execute(
        select(TelegramUser)
        .options(selectinload(TelegramUser.bot))
        .where(TelegramUser.id == me.id)
    )
    existing_user: Optional[TelegramUser] = q.scalar_one_or_none()

    if existing_user is None:
        user_bots_count = await get_user_bots_count(db, current_user.id)
        if user_bots_count >= settings.MAX_USER_BOTS:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden. You already have max amount of bots: {settings.MAX_USER_BOTS}",
            )

        new_user = TelegramUser(
            id=me.id,
            first_name=me.first_name,
            last_name=me.last_name,
            username=str(me.username),
            is_premium=bool(me.is_premium),
            is_bot=me.is_bot,
        )
        db.add(new_user)

        new_bot = Bot(
            id=me.id,
            token=token_encrypted,
            can_join_groups=bool(me.can_join_groups),
            can_read_all_group_messages=bool(me.can_read_all_group_messages),
            supports_inline_queries=bool(me.supports_inline_queries),
            can_connect_to_business=bool(me.can_connect_to_business),
            has_main_web_app=bool(me.has_main_web_app),
        )
        db.add(new_bot)

        await remove_extra_bot_links(db, me.id, settings.MAX_USER_BOT_LINKS - 1)

        owner_link = UserBot(
            user_id=current_user.id, bot_id=new_bot.id, role=UserBotRole.OWNER
        )
        db.add(owner_link)

        await db.commit()

        return BotResponse(
            id=me.id,
            first_name=me.first_name,
            last_name=me.last_name,
            username=bot_username,
            can_join_groups=bool(me.can_join_groups),
            can_read_all_group_messages=bool(me.can_read_all_group_messages),
            supports_inline_queries=bool(me.supports_inline_queries),
            can_connect_to_business=bool(me.can_connect_to_business),
            has_main_web_app=bool(me.has_main_web_app),
            role=UserBotRole.OWNER,
        )

    q2 = await db.execute(select(UserBot).where(UserBot.bot_id == me.id))
    userbot_rows = q2.scalars().all()

    my_link_exist = False

    for ub in userbot_rows:
        if ub.user_id == current_user.id:
            ub.role = UserBotRole.OWNER
            my_link_exist = True
            continue

        if ub.role == UserBotRole.OWNER:
            ub.role = UserBotRole.VIEWER

    if not my_link_exist:
        user_bots_count = await get_user_bots_count(db, current_user.id)

        if user_bots_count >= settings.MAX_USER_BOTS:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden. You already have max amount of bots: {settings.MAX_USER_BOTS}",
            )

        await remove_extra_bot_links(db, me.id, settings.MAX_USER_BOT_LINKS - 1)

        db.add(
            UserBot(
                user_id=current_user.id,
                bot_id=me.id,
                role=UserBotRole.OWNER,
            )
        )

    if not existing_user.bot:
        new_bot = Bot(
            id=me.id,
            token=token_encrypted,
            can_join_groups=bool(me.can_join_groups),
            can_read_all_group_messages=bool(me.can_read_all_group_messages),
            supports_inline_queries=bool(me.supports_inline_queries),
            can_connect_to_business=bool(me.can_connect_to_business),
            has_main_web_app=bool(me.has_main_web_app),
        )
        db.add(new_bot)
    else:
        existing_user.bot.can_join_groups = bool(me.can_join_groups)
        existing_user.bot.can_read_all_group_messages = bool(
            me.can_read_all_group_messages
        )
        existing_user.bot.supports_inline_queries = bool(me.supports_inline_queries)
        existing_user.bot.can_connect_to_business = bool(me.can_connect_to_business)
        existing_user.bot.has_main_web_app = bool(me.has_main_web_app)
        existing_user.bot.token = token_encrypted

    existing_user.first_name = me.first_name
    existing_user.last_name = me.last_name
    existing_user.username = bot_username

    await db.commit()

    return BotResponse(
        id=me.id,
        first_name=me.first_name,
        last_name=me.last_name,
        username=bot_username,
        can_join_groups=bool(me.can_join_groups),
        can_read_all_group_messages=bool(me.can_read_all_group_messages),
        supports_inline_queries=bool(me.supports_inline_queries),
        can_connect_to_business=bool(me.can_connect_to_business),
        has_main_web_app=bool(me.has_main_web_app),
        role=UserBotRole.OWNER,
    )


@router.get(
    "",
    response_model=BotListResponse,
    responses={401: common_responses[401]},
)
@limiter.limit("10/minute")
async def list_bots(
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> BotListResponse:
    bots: List[BotResponse] = []
    if current_user.role not in (UserRole.ADMIN, UserRole.GOD):
        q = await db.execute(
            select(UserBot)
            .options(selectinload(UserBot.bot).selectinload(Bot.telegram_user))
            .where(UserBot.user_id == current_user.id),
        )

        result = q.scalars().all()

        for link in result:
            bots.append(make_bot_response(link.bot, link.role))

        return BotListResponse(
            bots=bots,
            limit=settings.MAX_USER_BOTS,
        )

    q = await db.execute(
        select(Bot, Bot.telegram_user, UserBot.role).outerjoin(
            UserBot, (UserBot.bot_id == Bot.id) & (UserBot.user_id == current_user.id)
        )
    )

    for bot, telegram_user, role in q.all():
        bots.append(
            BotResponse(
                id=bot.id,
                first_name=telegram_user.first_name,
                last_name=telegram_user.last_name,
                username=telegram_user.username,
                can_join_groups=bot.can_join_groups,
                can_read_all_group_messages=bot.can_read_all_group_messages,
                supports_inline_queries=bot.supports_inline_queries,
                can_connect_to_business=bot.can_connect_to_business,
                has_main_web_app=bot.has_main_web_app,
                role=role,
            )
        )

    return BotListResponse(
        bots=bots,
        limit=settings.MAX_USER_BOTS,
    )


@router.get(
    "/{bot_id}",
    response_model=BotResponse,
    responses={
        404: common_responses[404],
        403: common_responses[403],
        401: common_responses[401],
    },
)
@limiter.limit("10/minute")
async def get_bot(
    bot_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> BotResponse:
    bot, role = await get_user_bot(bot_id, current_user, db, preload_telegram_user=True)

    return make_bot_response(bot, role)


@router.delete(
    "/{bot_id}",
    status_code=204,
    responses={
        404: common_responses[404],
        403: common_responses[403],
        401: common_responses[401],
    },
)
@limiter.limit("5/minute")
async def delete_bot(
    bot_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    bot, role = await get_user_bot(bot_id, current_user, db)

    if role != UserBotRole.OWNER and current_user.role not in (
        UserRole.ADMIN,
        UserRole.GOD,
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    await db.delete(bot)
    await db.commit()

    return Response(status_code=204)


@router.get(
    "/{bot_id}/users",
    response_model=BotUsersResponse,
    responses={
        404: common_responses[404],
        403: common_responses[403],
        401: common_responses[401],
    },
)
@limiter.limit("5/minute")
async def get_bot_users(
    bot_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> BotUsersResponse:
    stmt = (
        select(Bot)
        .options(selectinload(Bot.users).selectinload(UserBot.user))
        .where(Bot.id == bot_id)
    )
    bot = (await db.execute(stmt)).scalar_one_or_none()

    if not bot:
        if current_user.role in (UserRole.ADMIN, UserRole.GOD):
            raise HTTPException(status_code=404, detail="Bot not found")

        raise HTTPException(status_code=403, detail="Forbidden")

    if current_user.role not in (UserRole.ADMIN, UserRole.GOD):
        user_role = None
        for bot_user in bot.users:
            if bot_user.user_id == current_user.id:
                user_role = bot_user.role
                break

        if user_role != UserBotRole.OWNER:
            raise HTTPException(status_code=403, detail="Forbidden")

    users: List[BotUserResponse] = []
    for bot_user in bot.users:
        user = bot_user.user
        users.append(
            BotUserResponse(
                id=user.id,
                username=user.username,
                is_banned=user.is_banned,
                bot_role=bot_user.role,
            )
        )

    return BotUsersResponse(users=users, limit=settings.MAX_USER_BOTS)


@router.get(
    "/{bot_id}/users/{user_id}",
    response_model=BotUserResponse,
    responses=user_op_responses,
)
@limiter.limit("10/minute")
async def get_bot_user(
    bot_id: int,
    user_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> BotUserResponse:
    stmt = (
        select(Bot)
        .options(selectinload(Bot.users).selectinload(UserBot.user))
        .where(Bot.id == bot_id)
    )
    bot = (await db.execute(stmt)).scalar_one_or_none()

    if not bot:
        if current_user.role in (UserRole.ADMIN, UserRole.GOD):
            raise HTTPException(status_code=404, detail="Bot not found")

        raise HTTPException(status_code=403, detail="Forbidden")

    if current_user.role not in (UserRole.ADMIN, UserRole.GOD):
        current_user_mapping = next(
            (ub for ub in bot.users if ub.user_id == current_user.id), None
        )
        if not current_user_mapping or current_user_mapping.role != UserBotRole.OWNER:
            raise HTTPException(status_code=403, detail="Forbidden")

    target_mapping = next((ub for ub in bot.users if ub.user_id == user_id), None)
    if not target_mapping:
        raise HTTPException(status_code=404, detail="User not found")

    u = target_mapping.user

    return BotUserResponse(
        id=u.id,
        username=u.username,
        is_banned=u.is_banned,
        bot_role=target_mapping.role,
    )


@router.put(
    "/{bot_id}/users",
    response_model=BotUserResponse,
    responses=user_op_responses,
)
@limiter.limit("5/minute")
async def add_bot_user(
    bot_id: int,
    body: UserBotUpdateRequest,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> BotUserResponse:
    stmt = (
        select(Bot)
        .options(selectinload(Bot.users).selectinload(UserBot.user))
        .where(Bot.id == bot_id)
    )
    bot = (await db.execute(stmt)).scalar_one_or_none()

    if not bot:
        if current_user.role in (UserRole.ADMIN, UserRole.GOD):
            raise HTTPException(status_code=404, detail="Bot not found")

        raise HTTPException(status_code=403, detail="Forbidden")

    is_owner = any(
        ub.user_id == current_user.id and ub.role == UserBotRole.OWNER
        for ub in bot.users
    )
    if current_user.role not in (UserRole.ADMIN, UserRole.GOD) and not is_owner:
        raise HTTPException(status_code=403, detail="Forbidden")

    field = User.email if body.email else User.username
    value = body.email or body.username

    q = await db.execute(select(User).where(field == value))
    target_user = q.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if current_user.id == target_user.id and current_user.role not in (
        UserRole.ADMIN,
        UserRole.GOD,
    ):
        raise HTTPException(
            status_code=400, detail="You cannot modify your own bot membership/role"
        )

    if target_user.is_banned:
        raise HTTPException(status_code=400, detail="Target user is banned")

    existing_mapping = await get_userbot_mapping(db, bot_id, target_user.id)

    if existing_mapping:
        if existing_mapping.role == body.role:
            raise HTTPException(status_code=409, detail="User already has that role")

        existing_mapping.role = body.role
    else:
        user_bots_count = await get_user_bots_count(db, target_user.id)
        if user_bots_count >= settings.MAX_USER_BOTS:
            raise HTTPException(
                status_code=403, detail="Forbidden. Already have max amount of bots."
            )

        bot_users = await get_bot_users_count(db, bot_id)
        if bot_users >= settings.MAX_USER_BOT_LINKS:
            raise HTTPException(
                status_code=403,
                detail="Forbidden. Bot already have max amount of users.",
            )

        new_mapping = UserBot(user_id=target_user.id, bot_id=bot_id, role=body.role)
        db.add(new_mapping)

    if body.role == UserBotRole.OWNER and existing_mapping:
        q2 = await db.execute(
            select(UserBot).where(
                UserBot.bot_id == bot_id,
                UserBot.role == UserBotRole.OWNER,
                UserBot.user_id != existing_mapping.user_id,
            )
        )
        owner_mapping = q2.scalar_one_or_none()
        if owner_mapping:
            owner_mapping.role = UserBotRole.VIEWER

    await db.commit()

    return BotUserResponse(
        id=target_user.id,
        username=target_user.username,
        is_banned=target_user.is_banned,
        bot_role=body.role,
    )


@router.delete(
    "/{bot_id}/users/{user_id}",
    status_code=204,
    responses=user_op_responses,
)
@limiter.limit("5/minute")
async def delete_bot_user(
    bot_id: int,
    user_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user_bot = await get_userbot_mapping(db, bot_id, user_id)

    if not user_bot:
        if current_user.role in (UserRole.ADMIN, UserRole.GOD):
            raise HTTPException(
                status_code=404, detail="User to bot relationship not found"
            )

        raise HTTPException(status_code=403, detail="Forbidden")

    if user_bot.role == UserBotRole.OWNER:
        if current_user.id != user_id and current_user.role not in (
            UserRole.ADMIN,
            UserRole.GOD,
        ):
            raise HTTPException(status_code=403, detail="Forbidden")

        raise HTTPException(
            status_code=409,
            detail="Owner can not be removed. Transfer ownership or delete bot.",
        )

    if user_id != current_user.id and current_user.role not in (
        UserRole.ADMIN,
        UserRole.GOD,
    ):
        current_user_relation = await get_userbot_mapping(db, bot_id, current_user.id)
        if not current_user_relation or current_user_relation.role != UserBotRole.OWNER:
            raise HTTPException(status_code=403, detail="Forbidden")

    await db.delete(user_bot)
    await db.commit()

    return Response(status_code=204)


@router.put(
    "/{bot_id}/webhook",
    response_model=DetailResponse,
    responses={
        404: webhook_not_found_response,
        403: common_responses[403],
        401: common_responses[401],
        400: common_responses[400],
    },
)
@limiter.limit("5/minute")
async def set_webhook(
    bot_id: int,
    body: WebhookCreateRequest,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    bot, role = await get_user_bot(bot_id, current_user, db)

    if role != UserBotRole.OWNER and current_user.role not in (
        UserRole.ADMIN,
        UserRole.GOD,
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    bot_token_stripped = crypto.decrypt_data(bot.token, CryptoInfo.BOT_TOKEN)
    bot_token = f"{bot.id}:{bot_token_stripped}"
    telegram_bot = get_telegram_bot(bot_token)

    secret_token = secrets.token_urlsafe(32)
    proxy_url = urljoin(
        str(settings.API_URL), f"{settings.API_PREFIX}/telegram/bots/{bot.id}/webhook"
    )

    try:
        await telegram_bot.set_webhook(
            url=proxy_url,
            max_connections=body.max_connections,
            allowed_updates=body.allowed_updates,
            drop_pending_updates=body.drop_pending_updates,
            secret_token=secret_token,
        )
    except TelegramError as e:
        logger.error(e)
        raise HTTPException(status_code=400, detail="Telegram API error")

    secret_token_encrypted = crypto.encrypt_data(secret_token, CryptoInfo.WEBHOOK_TOKEN)
    redirect_url_encrypted = (
        crypto.encrypt_data(str(body.url), CryptoInfo.WEBHOOK_URL) if body.url else None
    )
    orig_token_encrypted = (
        crypto.encrypt_data(body.secret_token, CryptoInfo.WEBHOOK_REDIRECT_TOKEN)
        if body.secret_token
        else None
    )

    q = await db.execute(select(BotWebhook).where(BotWebhook.bot_id == bot.id))
    existing_webhook: Optional[BotWebhook] = q.scalar_one_or_none()
    if existing_webhook is None:
        db.add(
            BotWebhook(
                bot_id=bot.id,
                secret_token=secret_token_encrypted,
                redirect_url=redirect_url_encrypted,
                redirect_token=orig_token_encrypted,
            )
        )
    else:
        existing_webhook.secret_token = secret_token_encrypted
        existing_webhook.redirect_url = redirect_url_encrypted
        existing_webhook.redirect_token = orig_token_encrypted
        db.add(existing_webhook)

    await db.commit()

    return DetailResponse(detail="Webhook set successfully")


@router.get(
    "/{bot_id}/webhook",
    response_model=Dict[str, Any],
    responses={
        404: webhook_not_found_response,
        403: common_responses[403],
        401: common_responses[401],
        400: common_responses[400],
    },
)
@limiter.limit("5/minute")
async def get_webhook_info(
    bot_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    bot, role = await get_user_bot(bot_id, current_user, db, preload_webhook=True)

    if role != UserBotRole.OWNER and current_user.role not in (
        UserRole.ADMIN,
        UserRole.GOD,
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    if bot.webhook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")

    token_decrypted = crypto.decrypt_data(bot.token, CryptoInfo.BOT_TOKEN)
    token = f"{bot.id}:{token_decrypted}"

    telegram_bot = get_telegram_bot(token)
    info = await telegram_bot.get_webhook_info()
    info_dict = info.to_dict()
    info_dict["url"] = (
        crypto.decrypt_data(bot.webhook.redirect_url, CryptoInfo.WEBHOOK_URL)
        if bot.webhook.redirect_url
        else None
    )

    if "ip_address" in info_dict:
        del info_dict["ip_address"]

    return info_dict


@router.delete(
    "/{bot_id}/webhook",
    status_code=204,
    responses={
        404: webhook_not_found_response,
        403: common_responses[403],
        401: common_responses[401],
        400: common_responses[400],
    },
)
@limiter.limit("5/minute")
async def delete_webhook(
    bot_id: int,
    request: Request,
    response: Response,
    drop_pending_updates: bool = Query(True),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    bot, role = await get_user_bot(bot_id, current_user, db, preload_webhook=True)

    if role != UserBotRole.OWNER and current_user.role not in (
        UserRole.ADMIN,
        UserRole.GOD,
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    if bot.webhook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")

    token_decrypted = crypto.decrypt_data(bot.token, CryptoInfo.BOT_TOKEN)
    token = f"{bot.id}:{token_decrypted}"

    telegram_bot = get_telegram_bot(token)
    try:
        await telegram_bot.delete_webhook(drop_pending_updates)
    except TelegramError as e:
        logger.error(e)
        raise HTTPException(status_code=400, detail="Telegram API error")

    bot, _ = await get_user_bot(bot_id, current_user, db, preload_webhook=True)
    await db.delete(bot.webhook)
    await db.commit()

    return Response(status_code=204)


@router.post(
    "/{bot_id}/webhook",
    responses={
        401: common_responses[401],
    },
)
@limiter.limit("60/minute")
async def handle_update(
    bot_id: int,
    request: Request,
    response: Response,
    x_telegram_token: str = Header(..., alias="X-Telegram-Bot-Api-Secret-Token"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    q = await db.execute(select(BotWebhook).where(BotWebhook.bot_id == bot_id))
    webhook = q.scalar_one_or_none()

    if webhook is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    token = crypto.decrypt_data(webhook.secret_token, CryptoInfo.WEBHOOK_TOKEN)
    if x_telegram_token != token:
        raise HTTPException(status_code=401, detail="Invalid token")

    body = await request.body()
    body_dict = json.loads(body)

    try:
        updated_message: Optional[Message] = None
        update = Update.de_json(body_dict)
        if update.edited_message:
            updated_message = update.edited_message
        elif update.edited_channel_post:
            updated_message = update.edited_channel_post
        elif update.edited_business_message:
            updated_message = update.edited_business_message

        await log_object(db, update, bot_id)

        if updated_message:
            await update_message(db, updated_message, bot_id, skip_log=True)

        await db.commit()
    except Exception as e:
        logger.error(e)

    if webhook.redirect_url:
        url = crypto.decrypt_data(webhook.redirect_url, CryptoInfo.WEBHOOK_URL)
        headers = {
            "Content-Type": request.headers.get("Content-Type", "application/json")
        }

        if webhook.redirect_token:
            headers["X-Telegram-Bot-Api-Secret-Token"] = crypto.decrypt_data(
                webhook.redirect_token, CryptoInfo.WEBHOOK_REDIRECT_TOKEN
            )

        try:
            async with httpx.AsyncClient(
                timeout=settings.WEBHOOK_REDIRECT_TIMEOUT
            ) as client:
                await client.post(
                    url,
                    content=body,
                    headers=headers,
                )
        except Exception as e:
            logger.error(e)

    return Response(status_code=200)


@router.get("/bot{token}/{method}", responses={404: common_responses[404]})
@limiter.limit("20/minute")
async def bot_proxy_get(
    token: str,
    method: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await proxy_request(token, method, request, db)


@router.post("/bot{token}/{method}", responses={404: common_responses[404]})
@limiter.limit("20/minute")
async def bot_proxy_post(
    token: str,
    method: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await proxy_request(token, method, request, db)


@router.get("/file/bot{token}/{file_path:path}", responses={404: common_responses[404]})
@limiter.limit("5/minute")
async def bot_proxy_file_get(
    token: str,
    file_path: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await proxy_file_request(token, file_path, db)


@router.post(
    "/file/bot{token}/{file_path:path}", responses={404: common_responses[404]}
)
@limiter.limit("5/minute")
async def bot_proxy_file_post(
    token: str,
    file_path: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Response:
    return await proxy_file_request(token, file_path, db)
