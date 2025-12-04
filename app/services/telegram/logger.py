import json
import re
from typing import Any, AsyncGenerator, Dict, List, Tuple, Union
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
import httpx
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Chat, ChatFullInfo, Message, Update, User as UpdateUser

from app.core.crypto import crypto
from app.core.settings import settings
from app.core.constants import (
    BOT_TOKEN_REGEX,
    EDITED_MESSAGE_RETURNED_METHODS,
    MESSAGE_RETURNED_METHODS,
)
from app.core.enums import ChatType, CryptoInfo
from app.core.utils import remove_fields
from app.core.logger import logger
from app.db.models.telegram.bot import Bot
from app.db.models.telegram.bot_message import BotMessage
from app.db.models.telegram.chat import TelegramChat
from app.db.models.telegram.message import TelegramMessage
from app.db.models.telegram.user import TelegramUser
from app.services.telegram.bots import get_telegram_bot
from app.services.telegram.entity_logger import (
    CHAT_EXCLUDED_FIELDS,
    insert_chat_photo_if_not_exist,
    log_object,
    make_message_db_object,
    update_message,
)


async def log_me(db: AsyncSession, user: UpdateUser) -> None:
    await db.execute(
        update(TelegramUser)
        .where(TelegramUser.id == user.id)
        .values(
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            language_code=user.language_code,
        )
    )


async def log_user(db: AsyncSession, user: UpdateUser) -> None:
    existing_user_q = await db.execute(
        select(TelegramUser).where(TelegramUser.id == user.id)
    )
    existing_user = existing_user_q.scalar_one_or_none()
    if existing_user is None:
        db.add(
            TelegramUser(
                id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                username=user.username,
                language_code=user.language_code,
                is_bot=user.is_bot,
                is_premium=bool(user.is_premium),
            )
        )

        return

    existing_user.first_name = user.first_name
    existing_user.last_name = user.last_name
    existing_user.username = user.username
    existing_user.language_code = user.language_code
    existing_user.is_bot = user.is_bot
    existing_user.is_premium = bool(user.is_premium)


async def log_users(db: AsyncSession, users: Dict[int, UpdateUser]) -> None:
    existing_users_q = await db.execute(
        select(TelegramUser).where(TelegramUser.id.in_(users.keys()))
    )
    existing_users = {u.id: u for u in existing_users_q.scalars().all()}

    for user in users.values():
        u = existing_users.get(user.id)
        if u is None:
            db.add(
                TelegramUser(
                    id=user.id,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    username=user.username,
                    language_code=user.language_code,
                    is_bot=user.is_bot,
                    is_premium=bool(user.is_premium),
                )
            )
        else:
            u.first_name = user.first_name
            u.last_name = user.last_name
            u.username = user.username

            u.language_code = user.language_code
            u.is_bot = user.is_bot
            u.is_premium = bool(user.is_premium)


async def log_chats(db: AsyncSession, chats: Dict[int, Chat]) -> None:
    existing_chats_q = await db.execute(
        select(TelegramChat).where(TelegramChat.id.in_(chats.keys()))
    )
    existing_chats = {c.id: c for c in existing_chats_q.scalars().all()}

    for chat in chats.values():
        chat_type = ChatType(chat.type)
        c = existing_chats.get(chat.id)
        if c is None:
            db.add(
                TelegramChat(
                    id=chat.id,
                    type=chat_type,
                    title=chat.title,
                    username=chat.username,
                    first_name=chat.first_name,
                    last_name=chat.last_name,
                    is_forum=bool(chat.is_forum),
                    is_direct_messages=bool(chat.is_direct_messages),
                )
            )
        else:
            c.type = chat_type
            c.title = chat.title
            c.username = chat.username
            c.first_name = chat.first_name
            c.last_name = chat.last_name

            c.is_forum = bool(chat.is_forum)
            c.is_direct_messages = bool(chat.is_direct_messages)


async def log_new_chat_full_info(
    db: AsyncSession, chat: ChatFullInfo, bot_id: int
) -> None:
    chat_dict = chat.to_dict()
    other_data = remove_fields(chat_dict, CHAT_EXCLUDED_FIELDS)

    if chat.photo:
        await insert_chat_photo_if_not_exist(db, chat.photo, bot_id)

    db.add(
        TelegramChat(
            id=chat.id,
            type=ChatType(chat.type),
            title=chat.title,
            username=chat.username,
            first_name=chat.first_name,
            last_name=chat.last_name,
            is_forum=bool(chat.is_forum),
            is_direct_messages=bool(chat.is_direct_messages),
            personal_chat_id=chat.personal_chat.id if chat.personal_chat else None,
            parent_chat_id=chat.parent_chat.id if chat.parent_chat else None,
            pinned_message_id=chat.pinned_message.id if chat.pinned_message else None,
            photo_small_id=chat.photo.small_file_unique_id if chat.photo else None,
            photo_big_id=chat.photo.big_file_unique_id if chat.photo else None,
            other_data=other_data,
        )
    )


async def log_chat_full_info(db: AsyncSession, chat: ChatFullInfo, bot_id: int) -> None:
    existing_chat_q = await db.execute(
        select(TelegramChat).where(TelegramChat.id == chat.id)
    )
    existing_chat = existing_chat_q.scalar_one_or_none()

    if existing_chat is None:
        await log_new_chat_full_info(db, chat, bot_id)
        return

    if chat.photo:
        await insert_chat_photo_if_not_exist(db, chat.photo, bot_id)

    chat_dict = chat.to_dict()
    other_data = remove_fields(chat_dict, CHAT_EXCLUDED_FIELDS)

    existing_chat.type = ChatType(chat.type)
    existing_chat.title = chat.title
    existing_chat.username = chat.username
    existing_chat.first_name = chat.first_name
    existing_chat.last_name = chat.last_name
    existing_chat.is_forum = bool(chat.is_forum)
    existing_chat.is_direct_messages = bool(chat.is_direct_messages)
    existing_chat.personal_chat_id = (
        chat.personal_chat.id if chat.personal_chat else None
    )
    existing_chat.parent_chat_id = chat.parent_chat.id if chat.parent_chat else None
    existing_chat.pinned_message_id = (
        chat.pinned_message.id if chat.pinned_message else None
    )
    existing_chat.photo_small_id = (
        chat.photo.small_file_unique_id if chat.photo else None
    )
    existing_chat.photo_big_id = chat.photo.big_file_unique_id if chat.photo else None
    existing_chat.other_data = other_data


async def fetch_new_chat_info(
    db: AsyncSession, chat_id: Union[int, str], token: str, bot_id: int
) -> ChatFullInfo:
    bot = get_telegram_bot(token)
    chat_info = await bot.get_chat(chat_id)
    await log_new_chat_full_info(db, chat_info, bot_id)

    return chat_info


async def log_telegram_request(
    db: AsyncSession,
    req: Dict[str, Any],
    data: Union[Dict[str, Any], List[Dict[str, Any]], bool],
    method: str,
    bot_id: int,
    token: str,
) -> None:
    if isinstance(data, bool):
        return

    if isinstance(data, list):
        if method == "getUpdates":
            updated_messages: List[Message] = []
            updates: List[Update] = []
            for update_dict in data:
                update = Update.de_json(update_dict)
                updates.append(update)

                if update.edited_message:
                    updated_messages.append(update.edited_message)
                elif update.edited_channel_post:
                    updated_messages.append(update.edited_channel_post)
                elif update.edited_business_message:
                    updated_messages.append(update.edited_business_message)

            await log_object(db, updates, bot_id)
            for message in updated_messages:
                await update_message(db, message, bot_id, skip_log=True)

        elif method == "sendMediaGroup":
            messages: List[Message] = []
            for message_dict in data:
                message = Message.de_json(message_dict)
                messages.append(message)

            await log_object(db, messages, bot_id)

        elif method == "copyMessages" or method == "forwardMessages":
            chat_id = req.get("chat_id")
            from_chat_id = req.get("from_chat_id")
            original_message_ids = req.get("message_ids", [])
            copied_ids = [msg_id_obj["message_id"] for msg_id_obj in data]

            if (
                not chat_id
                or not from_chat_id
                or not original_message_ids
                or not copied_ids
                or len(original_message_ids) != len(copied_ids)
            ):
                return

            if isinstance(chat_id, str):
                stmt = (
                    select(
                        TelegramMessage,
                        TelegramChat.id,
                    )
                    .join(
                        TelegramChat,
                        TelegramChat.username == chat_id.lstrip("@"),
                        isouter=True,
                    )
                    .where(
                        TelegramMessage.chat_id == from_chat_id,
                        TelegramMessage.id.in_(original_message_ids),
                    )
                )

                result = await db.execute(stmt)
                rows = result.all()
                original_db_msgs = [row[0] for row in rows]
                if not original_db_msgs or not rows:
                    return

                chat_exists = bool(rows[0][1])
                if chat_exists:
                    chat_id = rows[0][1]
            else:
                stmt2 = select(
                    TelegramMessage,
                    exists().where(TelegramChat.id == chat_id),
                ).where(
                    TelegramMessage.chat_id == from_chat_id,
                    TelegramMessage.id.in_(original_message_ids),
                )
                result = await db.execute(stmt2)
                rows = result.all()

                original_db_msgs = [row[0] for row in rows]
                if not original_db_msgs:
                    return

                chat_exists = rows[0][1] if rows else False

            if not chat_exists:
                chat_id = (await fetch_new_chat_info(db, chat_id, token, bot_id)).id

            msg_by_id = {msg.id: msg for msg in original_db_msgs}
            copied_messages: List[Tuple[TelegramMessage, int]] = []

            for orig_id, copied_id in zip(original_message_ids, copied_ids):
                msg = msg_by_id.get(orig_id)
                if msg:
                    copied_messages.append((msg, copied_id))

            if not copied_messages:
                return

            new_messages: List[Message] = []
            for original, new_id in copied_messages:
                msg_dict = original.to_dict()

                del msg_dict["edit_date"]
                msg_dict["message_id"] = new_id
                msg_dict["chat"]["id"] = chat_id

                if req.get("message_thread_id"):
                    msg_dict["message_thread_id"] = req["message_thread_id"]

                if req.get("remove_caption") and method == "copyMessages":
                    msg_dict["caption"] = None

                if req.get("protect_content"):
                    msg_dict["has_protected_content"] = True

                new_msg = Message.de_json(msg_dict)
                new_messages.append(new_msg)

            await log_object(db, new_messages, bot_id)

        return

    if method == "getChatFullInfo":
        chat_info = ChatFullInfo.de_json(data)

        chats_to_log: Dict[int, Chat] = {}
        if chat_info.personal_chat:
            chats_to_log[chat_info.personal_chat.id] = chat_info.personal_chat

        if chat_info.parent_chat:
            chats_to_log[chat_info.parent_chat.id] = chat_info.parent_chat

        if chats_to_log:
            await log_chats(db, chats_to_log)

        await log_chat_full_info(db, chat_info, bot_id)
        return

    if method == "getMe":
        user = UpdateUser.de_json(data)
        await log_me(db, user)
        return

    if method in MESSAGE_RETURNED_METHODS:
        message = Message.de_json(data)
        await log_object(db, message, bot_id)
        return

    if method in EDITED_MESSAGE_RETURNED_METHODS:
        message = Message.de_json(data)
        await update_message(db, message, bot_id)
        return

    if method == "copyMessage":
        chat_id = req.get("chat_id")
        from_chat_id = req.get("from_chat_id")
        original_message_id = req.get("message_id")
        copied_id = data.get("message_id")

        if not chat_id or not from_chat_id or not original_message_id or not copied_id:
            return

        if isinstance(chat_id, str):
            stmt3 = (
                select(
                    TelegramMessage,
                    TelegramChat.id,
                )
                .join(
                    TelegramChat,
                    TelegramChat.username == chat_id.lstrip("@"),
                    isouter=True,
                )
                .where(
                    TelegramMessage.id == original_message_id,
                    TelegramMessage.chat_id == from_chat_id,
                )
            )

            result = await db.execute(stmt3)
            rows = result.all()
            if not rows:
                return

            original_msg = rows[0][0]
            chat_exists = bool(rows[0][1])
            if chat_exists:
                chat_id = rows[0][1]
        else:
            stmt4 = select(
                TelegramMessage,
                exists().where(TelegramChat.id == chat_id),
            ).where(
                TelegramMessage.id == original_message_id,
                TelegramMessage.chat_id == from_chat_id,
            )
            result = await db.execute(stmt4)
            rows = result.all()

            if not rows:
                return

            original_msg = rows[0][0]
            chat_exists = rows[0][1]

        if not chat_exists:
            chat_id = (await fetch_new_chat_info(db, chat_id, token, bot_id)).id

        msg_dict = original_msg.to_dict()

        del msg_dict["edit_date"]
        msg_dict["message_id"] = copied_id
        msg_dict["chat"]["id"] = chat_id

        if req.get("message_thread_id"):
            msg_dict["message_thread_id"] = req["message_thread_id"]

        if req.get("protect_content"):
            msg_dict["has_protected_content"] = True

        if req.get("caption"):
            msg_dict["caption"] = req["caption"]

        if req.get("caption_entities"):
            msg_dict["caption_entities"] = req["caption_entities"]

        if req.get("show_caption_above_media"):
            msg_dict = req["show_caption_above_media"]

        if req.get("reply_markup"):
            msg_dict = req["reply_markup"]

        json_msg = Message.de_json(msg_dict)
        db_msg = make_message_db_object(json_msg)

        db.add(db_msg)
        db.add(BotMessage(bot_id=bot_id, chat_id=chat_id, message_id=copied_id))
        await db.commit()
        return


async def verify_token(db: AsyncSession, token: str) -> int:
    if not token or not re.match(BOT_TOKEN_REGEX, token):
        raise HTTPException(status_code=400, detail="Invalid Telegram bot token format")

    bot_id = int(token.split(":")[0])

    q = await db.execute(select(Bot.token).where(Bot.id == bot_id))
    token_db = q.scalar_one_or_none()
    if token_db is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    token_stripped = token.split(":", 1)[1]
    token_decrypted = crypto.decrypt_data(token_db, CryptoInfo.BOT_TOKEN)

    if token_stripped != token_decrypted:
        raise HTTPException(status_code=404, detail="Bot not found")

    return bot_id


async def proxy_request(
    token: str,
    method: str,
    request: Request,
    db: AsyncSession,
) -> Response:
    bot_id = await verify_token(db, token)
    method = method.rstrip("/")
    telegram_url = str(settings.TELEGRAM_API_URL) + f"{token}/{method}"
    query_params = dict(request.query_params)

    body = await request.body()

    try:
        body_dict = json.loads(body) if body else {}
    except Exception:
        raise HTTPException(
            status_code=404,
            detail="JSON request expected",
        )

    merged_request: Dict[str, Any] = query_params | body_dict

    async with httpx.AsyncClient(
        timeout=settings.TELEGRAM_API_REDIRECT_TIMEOUT
    ) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=telegram_url,
                params=query_params,
                json=body_dict,
            )

            if resp.status_code == 200:
                try:
                    json_response: Dict[str, Any] = resp.json()
                    if json_response.get("ok"):
                        result: Union[Dict[str, Any], List[Dict[str, Any]], bool] = (
                            json_response.get("result", {})
                        )
                        await log_telegram_request(
                            db, merged_request, result, method, bot_id, token
                        )
                        await db.commit()
                except Exception as e:
                    logger.error(e)

            headers = {
                k: v
                for k, v in resp.headers.items()
                if k.lower()
                not in ["content-encoding", "transfer-encoding", "content-length"]
            }
            return Response(
                content=resp.content, status_code=resp.status_code, headers=headers
            )

        except httpx.RequestError as e:
            logger.error(e)
            raise HTTPException(
                status_code=502, detail="Failed to reach Telegram API"
            )


async def proxy_file_request(
    token: str,
    file_path: str,
    db: AsyncSession,
) -> Response:
    await verify_token(db, token)

    telegram_file_url = (
        f"{settings.TELEGRAM_API_FILE_URL}{token}/" f"{file_path.lstrip('/')}"
    )

    async with httpx.AsyncClient(
        timeout=settings.TELEGRAM_API_REDIRECT_TIMEOUT
    ) as client:
        try:
            resp = await client.get(
                telegram_file_url, follow_redirects=True
            )

            if resp.status_code != 200:
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type"),
                )

            headers = {
                k: v
                for k, v in resp.headers.items()
                if k.lower() not in ["content-encoding", "transfer-encoding"]
            }

            async def file_generator() -> AsyncGenerator[bytes, None]:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    yield chunk

            return StreamingResponse(
                file_generator(),
                media_type=resp.headers.get("content-type", "application/octet-stream"),
                headers=headers,
                status_code=resp.status_code,
            )

        except httpx.RequestError as e:
            logger.error(e)
            raise HTTPException(
                status_code=502, detail="Failed to reach Telegram API"
            )
