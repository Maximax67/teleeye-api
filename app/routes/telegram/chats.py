from collections import defaultdict
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, exists, insert, or_, select, and_, func, update
from sqlalchemy.orm import joinedload, aliased
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_pagination import Page, Params

from app.core.enums import ChatType, UserRole
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
    get_message_options,
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


def _parse_chat_types(chat_types: Optional[str]) -> List[ChatType]:
    """Parse comma-separated chat type string into a list of ChatType enums."""
    if not chat_types:
        return []
    result = []
    for ct in chat_types.split(","):
        ct = ct.strip()
        try:
            result.append(ChatType(ct))
        except ValueError:
            pass
    return result


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
    chat_types: Optional[str] = Query(
        None,
        description="Comma-separated chat types to filter: private,group,supergroup,channel",
    ),
    search: Optional[str] = Query(
        None,
        description="Search in chat title, username or name (case-insensitive)",
    ),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
    params: Params = Depends(),
) -> Page[Dict[str, Any]]:
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)

    requested_bot_ids = await parse_bot_param(bots)
    await check_bot_access(db, current_user.id, requested_bot_ids, is_admin)

    valid_chat_types = _parse_chat_types(chat_types)
    search_term = f"%{search}%" if search else None

    bm = BotMessage
    ub = UserBot
    tc = TelegramChat
    rm = ReadMessages

    # ── Build subquery for latest message per chat ──────────────────────────
    last_msg_sub_stmt = select(
        bm.chat_id.label("chat_id"),
        func.max(bm.message_id).label("last_message_id"),
    )

    # User/bot access filter
    if requested_bot_ids:
        last_msg_sub_stmt = last_msg_sub_stmt.where(bm.bot_id.in_(requested_bot_ids))
    elif not is_admin:
        last_msg_sub_stmt = last_msg_sub_stmt.join(ub, ub.bot_id == bm.bot_id).where(
            ub.user_id == current_user.id
        )

    # Chat type / search filter applied to the subquery so the count is accurate
    if valid_chat_types or search_term:
        last_msg_sub_stmt = last_msg_sub_stmt.join(
            TelegramChat, TelegramChat.id == bm.chat_id
        )
        if valid_chat_types:
            last_msg_sub_stmt = last_msg_sub_stmt.where(
                TelegramChat.type.in_(valid_chat_types)
            )
        if search_term:
            last_msg_sub_stmt = last_msg_sub_stmt.where(
                or_(
                    TelegramChat.title.ilike(search_term),
                    TelegramChat.username.ilike(search_term),
                    TelegramChat.first_name.ilike(search_term),
                    TelegramChat.last_name.ilike(search_term),
                )
            )

    last_msg_sub_stmt = last_msg_sub_stmt.group_by(bm.chat_id)
    last_msg_sub = last_msg_sub_stmt.subquery()

    last_msg = aliased(TelegramMessage)

    # Total count (honours all filters)
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

    chat_read_map: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for chat, _, thread_id, msg_id in rows:
        if thread_id is not None:
            chat_read_map[chat.id].append(
                {"message_thread_id": thread_id, "message_id": msg_id}
            )

    items: List[Dict[str, Any]] = []
    for chat, message, *_ in rows:
        chat_dict = chat.to_dict()
        chat_dict["last_message"] = serialize_message(message)
        chat_dict["read_messages"] = chat_read_map.get(chat.id, [])
        items.append(chat_dict)

    pages = max(1, (total + params.size - 1) // params.size)

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
        None, description="Comma-separated bot ids to filter by."
    ),
    message_thread_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(
        50, ge=1, le=200, description="Max number of messages to return"
    ),
    before_id: Optional[int] = Query(
        None,
        description="Fetch messages with message_id < before_id (load older). Mutually exclusive with after_id.",
    ),
    after_id: Optional[int] = Query(
        None,
        description="Fetch messages with message_id > after_id in ascending order (load newer). Mutually exclusive with before_id.",
    ),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    if before_id is not None and after_id is not None:
        raise HTTPException(
            status_code=400,
            detail="before_id and after_id are mutually exclusive",
        )

    requested_bot_ids = await parse_bot_param(bots)
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)
    await check_bot_access(db, current_user.id, requested_bot_ids, is_admin)

    tm = TelegramMessage
    bm = BotMessage
    ub = UserBot

    join_condition = and_(bm.chat_id == tm.chat_id, bm.message_id == tm.id)
    base_stmt = (
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
        base_stmt = base_stmt.where(bm.bot_id.in_(requested_bot_ids))
    elif not is_admin:
        base_stmt = base_stmt.join(ub, ub.bot_id == bm.bot_id).where(
            ub.user_id == current_user.id
        )

    if message_thread_id:
        base_stmt = base_stmt.where(tm.message_thread_id == message_thread_id)

    if after_id is not None:
        stmt = base_stmt.where(tm.id > after_id).order_by(tm.id.asc()).limit(limit + 1)
        result = await db.execute(stmt)
        messages = result.scalars().all()

        has_more_newer = len(messages) > limit
        if has_more_newer:
            messages = messages[:limit]

        # Assume there are older messages if after_id > 0
        has_more_older = after_id > 0

        items = [serialize_message(m) for m in messages]

        return {
            "items": items,
            "has_more_older": has_more_older,
            "has_more_newer": has_more_newer,
        }

    else:
        stmt = base_stmt
        if before_id is not None:
            stmt = stmt.where(tm.id < before_id)

        stmt = stmt.order_by(tm.id.desc()).limit(limit + 1)
        result = await db.execute(stmt)
        messages = result.scalars().all()

        if not messages:
            raise HTTPException(
                status_code=404, detail="Chat not found or no accessible messages"
            )

        has_more_older = len(messages) > limit
        if has_more_older:
            messages = messages[:limit]

        has_more_newer = before_id is not None

        items = [serialize_message(m) for m in messages]

        return {
            "items": items,
            "has_more_older": has_more_older,
            "has_more_newer": has_more_newer,
        }


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


@router.get(
    "/{chat_id}/threads",
    response_model=Dict[str, Any],
    responses=common_responses,
)
@limiter.limit("10/minute")
async def list_chat_threads(
    chat_id: int,
    request: Request,
    response: Response,
    bots: Optional[str] = Query(
        None, description="Comma-separated bot IDs to filter by"
    ),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """List all message threads in a chat, similar to Telegram's topic list."""
    requested_bot_ids = await parse_bot_param(bots)
    is_admin = current_user.role in (UserRole.ADMIN, UserRole.GOD)
    await check_bot_access(db, current_user.id, requested_bot_ids, is_admin)

    tm = TelegramMessage
    bm = BotMessage
    ub = UserBot
    rm = ReadMessages

    # Build base query for messages in this chat
    join_condition = and_(bm.chat_id == tm.chat_id, bm.message_id == tm.id)
    base_stmt = (
        select(tm)
        .join(bm, join_condition)
        .where(bm.chat_id == chat_id)
    )

    if requested_bot_ids:
        base_stmt = base_stmt.where(bm.bot_id.in_(requested_bot_ids))
    elif not is_admin:
        base_stmt = base_stmt.join(ub, ub.bot_id == bm.bot_id).where(
            ub.user_id == current_user.id
        )

    # Get all messages with thread_id
    thread_stmt = base_stmt.where(tm.message_thread_id.isnot(None)).options(*get_message_options())
    result = await db.execute(thread_stmt)
    messages = result.scalars().all()

    if not messages:
        return {"items": []}

    # Group messages by thread_id and get the latest message for each thread
    threads_map: Dict[int, List[TelegramMessage]] = defaultdict(list)
    for msg in messages:
        thread_id = msg.message_thread_id or 1
        threads_map[thread_id].append(msg)

    # Build thread items with latest message and read status
    items: List[Dict[str, Any]] = []
    for thread_id, thread_messages in threads_map.items():
        # Sort by message_id descending to get the latest
        thread_messages.sort(key=lambda m: m.id, reverse=True)
        latest_msg = thread_messages[0]

        # Get read status for this thread
        read_stmt = select(rm.message_id).where(
            rm.user_id == current_user.id,
            rm.chat_id == chat_id,
            rm.message_thread_id == thread_id,
        )
        read_result = await db.execute(read_stmt)
        read_message_id = read_result.scalar_one_or_none() or 0

        # Count unread messages in this thread
        unread_count = sum(1 for m in thread_messages if m.id > read_message_id)

        items.append({
            "thread_id": thread_id,
            "last_message": serialize_message(latest_msg),
            "unread_count": unread_count,
            "message_count": len(thread_messages),
        })

    # Sort by latest message date descending
    items.sort(key=lambda x: x["last_message"]["date"], reverse=True)

    return {"items": items}
