from collections import defaultdict
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, exists, insert, select, and_, func, update
from sqlalchemy.orm import joinedload, aliased
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_pagination import Page, Params

from app.core.enums import UserRole
from app.core.limiter import limiter
from app.core.logger import logger
from app.core.dependencies import require_authorization
from app.db.models.telegram.bot_message import BotMessage
from app.db.models.telegram.chat import TelegramChat
from app.db.models.telegram.message import TelegramMessage
from app.db.models.telegram.read_messages import ReadMessages
from app.db.models.user_bot import UserBot
from app.db.session import get_db
from app.schemas.auth import AuthorizedUser
from app.schemas.telegram.chat import ReadRequest
from app.services.telegram.bots import get_bot_by_chat, get_bot_by_id
from app.services.telegram.chats import (
    check_bot_access,
    parse_bot_param,
    serialize_message,
)
from app.services.telegram.logger import log_chat_full_info

router = APIRouter(prefix="/chats", tags=["telegram-chats"])

common_responses: Dict[Union[int, str], Dict[str, Any]] = {
    404: {
        "description": "Chat not found or no accessible messages",
        "content": {
            "application/json": {
                "example": {"detail": "Chat not found or no accessible messages"}
            }
        },
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
        "descripton": "Bad request",
        "content": {
            "application/json": {"example": {"detail": "Bad request error description"}}
        },
    },
}


@router.get(
    "",
    response_model=Page[Dict[str, Any]],
    responses={
        403: common_responses[403],
        401: common_responses[401],
        400: common_responses[400],
    },
)
@limiter.limit("10/minute")
async def list_accessible_chats(
    request: Request,
    response: Response,
    bots: Optional[str] = Query(
        None,
        description="Comma-separated bot IDs. If provided, only chats where these bots have messages are included.",
    ),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
    params: Params = Depends(),
) -> Page[Dict[str, Any]]:
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)

    requested_bot_ids = await parse_bot_param(bots)
    await check_bot_access(db, current_user.id, requested_bot_ids, is_admin)

    bm = BotMessage
    ub = UserBot
    tc = TelegramChat
    rm = ReadMessages

    last_msg_sub_stmt = select(
        bm.chat_id.label("chat_id"),
        func.max(bm.message_id).label("last_message_id"),
    ).group_by(bm.chat_id)

    if requested_bot_ids:
        last_msg_sub_stmt = last_msg_sub_stmt.where(bm.bot_id.in_(requested_bot_ids))
    elif not is_admin:
        last_msg_sub_stmt = last_msg_sub_stmt.join(ub, ub.bot_id == bm.bot_id).where(
            ub.user_id == current_user.id
        )

    last_msg_sub = last_msg_sub_stmt.subquery()

    last_msg = aliased(TelegramMessage)

    count_q = await db.execute(select(func.count()).select_from(last_msg_sub))
    total = count_q.scalar_one()

    offset = (params.page - 1) * params.size
    limit = params.size

    stmt = (
        select(tc, last_msg, rm.message_thread_id, rm.message_id)
        .join(last_msg_sub, last_msg_sub.c.chat_id == tc.id)
        .join(last_msg, last_msg.id == last_msg_sub.c.last_message_id)
        .outerjoin(rm, and_(rm.chat_id == tc.id, rm.user_id == current_user.id))
        .options(
            joinedload(last_msg.from_user),
            joinedload(last_msg.sender_chat),
            joinedload(last_msg.sender_business_bot),
        )
        .order_by(last_msg.date.desc())
        .offset(offset)
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()

    items: List[Dict[str, Any]] = []

    chat_read_map = defaultdict(list)
    for chat, _, thread_id, msg_id in rows:
        if thread_id is not None:
            chat_read_map[chat.id].append(
                {"message_thread_id": thread_id, "message_id": msg_id}
            )

    for chat, message, *_ in rows:
        chat_dict = chat.to_dict()
        chat_dict["last_message"] = serialize_message(message)
        chat_dict["read_messages"] = chat_read_map.get(chat.id, [])
        items.append(chat_dict)

    pages = (total + params.size - 1) // params.size

    return Page(
        total=total,
        items=items,
        page=params.page,
        size=params.size,
        pages=pages,
    )


@router.get(
    "/{chat_id}",
    response_model=Dict[str, Any],
    responses=common_responses,
)
@limiter.limit("10/minute")
async def get_chat_info(
    chat_id: int,
    request: Request,
    response: Response,
    bots: Optional[str] = Query(
        None, description="Comma-separated bot IDs to filter the last message by"
    ),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    requested_bot_ids = await parse_bot_param(bots)
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)
    await check_bot_access(db, current_user.id, requested_bot_ids, is_admin)

    bm = BotMessage
    ub = UserBot
    tc = TelegramChat
    rm = ReadMessages

    last_msg_sub_stmt = select(
        bm.chat_id.label("chat_id"),
        func.max(bm.message_id).label("last_message_id"),
    ).where(bm.chat_id == chat_id)

    if requested_bot_ids:
        last_msg_sub_stmt = last_msg_sub_stmt.where(bm.bot_id.in_(requested_bot_ids))
    elif not is_admin:
        last_msg_sub_stmt = last_msg_sub_stmt.join(ub, ub.bot_id == bm.bot_id).where(
            ub.user_id == current_user.id
        )

    last_msg_sub = last_msg_sub_stmt.group_by(bm.chat_id).subquery()
    last_msg = aliased(TelegramMessage)

    stmt = (
        select(tc, last_msg, rm.message_thread_id, rm.message_id)
        .join(last_msg_sub, last_msg_sub.c.chat_id == tc.id)
        .join(last_msg, last_msg.id == last_msg_sub.c.last_message_id)
        .outerjoin(rm, and_(rm.chat_id == tc.id, rm.user_id == current_user.id))
        .options(
            joinedload(last_msg.from_user),
            joinedload(last_msg.sender_chat),
            joinedload(last_msg.sender_business_bot),
        )
    )

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        raise HTTPException(
            status_code=404, detail="Chat not found or no accessible messages"
        )

    read_messages = [
        {"message_thread_id": thread_id, "message_id": msg_id}
        for _, _, thread_id, msg_id in rows
        if thread_id is not None
    ]

    chat, message, *_ = rows[0]
    chat_dict: Dict[str, Any] = chat.to_dict()
    chat_dict["last_message"] = serialize_message(message)
    chat_dict["read_messages"] = read_messages

    return chat_dict


@router.get(
    "/{chat_id}/avatar",
    responses={
        **common_responses,
        502: {
            "description": "Telegram API Error",
            "content": {
                "application/json": {"example": {"detail": "Telegram API Error"}}
            },
        },
    },
)
@limiter.limit("10/minute")
async def get_chat_avatar(
    chat_id: int,
    request: Request,
    response: Response,
    bot_id: Optional[int] = Query(None, description="Bot ID to fetch chat info"),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if bot_id is not None:
        telegram_bot = await get_bot_by_id(db, bot_id, current_user)
    else:
        bot_id, telegram_bot = await get_bot_by_chat(db, chat_id, current_user)

    try:
        chat_info = await telegram_bot.get_chat(chat_id)
    except Exception as e:
        logger.error(e)
        raise HTTPException(status_code=502, detail="Telegram API Error")

    await log_chat_full_info(db, chat_info, bot_id)
    await db.commit()

    if not chat_info.photo:
        raise HTTPException(status_code=404, detail="Avatar not found")

    try:
        avatar = await chat_info.photo.get_small_file()
        avatar_bytes = await avatar.download_as_bytearray()
    except Exception as e:
        logger.error(e)
        raise HTTPException(status_code=502, detail="Telegram API Error")

    return Response(content=bytes(avatar_bytes), media_type="image/jpeg")


@router.get(
    "/{chat_id}/messages",
    response_model=Dict[str, Any],
    responses=common_responses,
)
@limiter.limit("10/minute")
async def list_chat_messages_cursor(
    chat_id: int,
    request: Request,
    response: Response,
    bots: Optional[str] = Query(
        None, description="Comma-separated bot ids to filter by. Example: bot=123,456"
    ),
    message_thread_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(
        50, ge=1, le=200, description="Max number of messages to return"
    ),
    before_id: Optional[int] = Query(
        None,
        description="Fetch messages with message_id < before_id (cursor). If omitted, fetch newest.",
    ),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    requested_bot_ids = await parse_bot_param(bots)
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)
    await check_bot_access(db, current_user.id, requested_bot_ids, is_admin)

    tm = TelegramMessage
    bm = BotMessage
    ub = UserBot

    join_condition = and_(bm.chat_id == tm.chat_id, bm.message_id == tm.id)
    stmt = (
        select(tm)
        .join(bm, join_condition)
        .where(bm.chat_id == chat_id)
        .options(
            joinedload(tm.from_user),
            joinedload(tm.sender_chat),
            joinedload(tm.sender_business_bot),
        )
    )

    if requested_bot_ids:
        stmt = stmt.where(bm.bot_id.in_(requested_bot_ids))
    elif not is_admin:
        stmt = stmt.join(ub, ub.bot_id == bm.bot_id).where(
            ub.user_id == current_user.id
        )

    if message_thread_id:
        stmt = stmt.where(tm.message_thread_id == message_thread_id)

    if before_id:
        stmt = stmt.where(tm.id < before_id)

    stmt = stmt.order_by(tm.id.desc()).limit(
        limit + 1
    )  # fetch one extra to detect `has_more`

    result = await db.execute(stmt)
    messages = result.scalars().all()

    if not messages:
        raise HTTPException(
            status_code=404, detail="Chat not found or no accessible messages"
        )

    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    items = [serialize_message(m) for m in messages]
    next_cursor = items[-1]["message_id"] if has_more and items else None

    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


@router.put(
    "/{chat_id}/messages/read",
    status_code=204,
    responses=common_responses,
)
@limiter.limit("10/minute")
async def mark_chat_read(
    chat_id: int,
    body: ReadRequest,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)

    tm = TelegramMessage
    bm = BotMessage
    ub = UserBot
    rm = ReadMessages

    thread_id = body.message_thread_id or 1

    join_condition = and_(bm.chat_id == tm.chat_id, bm.message_id == tm.id)
    last_msg_q = (
        select(func.max(tm.id))
        .select_from(tm)
        .join(bm, join_condition)
        .where(
            bm.chat_id == chat_id, func.coalesce(tm.message_thread_id, 1) == thread_id
        )
    )
    if not is_admin:
        last_msg_q = last_msg_q.join(ub, ub.bot_id == bm.bot_id).where(
            ub.user_id == current_user.id
        )

    last_message_id = (await db.execute(last_msg_q)).scalar_one_or_none()

    if not last_message_id:
        raise HTTPException(
            status_code=404, detail="Chat not found or no accessible messages"
        )

    final_message_id = (
        body.message_id if body.message_id is not None else last_message_id
    )

    if final_message_id > last_message_id:
        raise HTTPException(
            status_code=400,
            detail=f"message_id {final_message_id} is greater than last message id {last_message_id}",
        )

    existing_q = select(
        exists().where(
            rm.user_id == current_user.id,
            rm.chat_id == chat_id,
            rm.message_thread_id == thread_id,
        )
    )
    existing_row = (await db.execute(existing_q)).scalar()

    if existing_row:
        upd_stmt = (
            update(rm)
            .where(
                rm.user_id == current_user.id,
                rm.chat_id == chat_id,
                rm.message_thread_id == thread_id,
            )
            .values(message_id=final_message_id)
        )
        await db.execute(upd_stmt)
    else:
        ins_stmt = insert(rm).values(
            user_id=current_user.id,
            chat_id=chat_id,
            message_thread_id=thread_id,
            message_id=final_message_id,
        )
        await db.execute(ins_stmt)

    await db.commit()

    return Response(status_code=204)


@router.delete(
    "/{chat_id}/messages/read",
    status_code=204,
    responses=common_responses,
)
@limiter.limit("10/minute")
async def delete_chat_read_marks(
    chat_id: int,
    request: Request,
    response: Response,
    message_thread_id: Optional[int] = None,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)

    tm = TelegramMessage
    bm = BotMessage
    ub = UserBot
    rm = ReadMessages

    join_condition = and_(bm.chat_id == tm.chat_id, bm.message_id == tm.id)
    accessible_q = (
        select(func.count())
        .select_from(tm)
        .join(bm, join_condition)
        .where(bm.chat_id == chat_id)
    )
    if not is_admin:
        accessible_q = accessible_q.join(ub, ub.bot_id == bm.bot_id).where(
            ub.user_id == current_user.id
        )

    accessible_count = (await db.execute(accessible_q)).scalar_one()
    if accessible_count == 0:
        raise HTTPException(
            status_code=404, detail="Chat not found or no accessible messages"
        )

    del_stmt = delete(rm).where(rm.user_id == current_user.id, rm.chat_id == chat_id)
    if message_thread_id is not None:
        del_stmt = del_stmt.where(rm.message_thread_id == message_thread_id)

    await db.execute(del_stmt)
    await db.commit()

    return Response(status_code=204)
