from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Type
from sqlalchemy import (
    BigInteger,
    Boolean,
    String,
    case,
    cast,
    column,
    insert,
    literal,
    null,
    select,
    update,
    values,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from telegram import (
    Animation,
    Audio,
    Chat,
    ChatPhoto,
    Document,
    Message,
    PassportFile,
    PhotoSize,
    Sticker,
    TelegramObject,
    User as UpdateUser,
    Video,
    VideoNote,
    Voice,
)

from app.core.enums import ChatType, EntityCheckResultType, FileType, MessageType
from app.core.utils import (
    deduplicate,
    deduplicate_compound,
    find_instances,
    find_objects_with_attributes,
    remove_fields,
)
from app.db.models.telegram.bot_message import BotMessage
from app.db.models.telegram.bot_file import BotFile
from app.db.models.telegram.chat import TelegramChat
from app.db.models.telegram.message import TelegramMessage
from app.db.models.telegram.file import TelegramFile
from app.db.models.telegram.user import TelegramUser


FILE_TO_TYPE_MAPPING: Dict[Type[TelegramObject], FileType] = {
    PhotoSize: FileType.PHOTO,
    Animation: FileType.ANIMATION,
    Audio: FileType.AUDIO,
    Document: FileType.DOCUMENT,
    Video: FileType.VIDEO,
    VideoNote: FileType.VIDEO_NOTE,
    Voice: FileType.VOICE,
    Sticker: FileType.STICKER,
    PassportFile: FileType.PASSPORT,
}

FILE_EXCLUDED_FIELDS: Set[str] = {
    "file_unique_id",
    "file_id",
    "file_size",
    "mime_type",
    "file_type",
}

CHAT_EXCLUDED_FIELDS: Set[str] = {
    "id",
    "type",
    "title",
    "username",
    "first_name",
    "last_name",
    "is_forum",
    "is_direct_messages",
    "personal_chat",
    "parent_chat",
    "pinned_message",
    "photo",
}

MESSAGE_TYPE_ATTRIBUTE_MAP: Dict[str, MessageType] = {
    "text": MessageType.TEXT,
    "animation": MessageType.ANIMATION,
    "audio": MessageType.AUDIO,
    "document": MessageType.DOCUMENT,
    "paid_media": MessageType.PAID_MEDIA,
    "photo": MessageType.PHOTO,
    "sticker": MessageType.STICKER,
    "story": MessageType.STORY,
    "video": MessageType.VIDEO,
    "video_note": MessageType.VIDEO_NOTE,
    "voice": MessageType.VOICE,
    "checklist": MessageType.CHECKLIST,
    "contact": MessageType.CONTACT,
    "dice": MessageType.DICE,
    "game": MessageType.GAME,
    "poll": MessageType.POLL,
    "venue": MessageType.VENUE,
    "location": MessageType.LOCATION,
    "invoice": MessageType.INVOICE,
    "giveaway": MessageType.GIVEAWAY,
    "passport_data": MessageType.PASSPORT,
}


def get_file_type(obj: object) -> Optional[FileType]:
    for cls, file_type in FILE_TO_TYPE_MAPPING.items():
        if isinstance(obj, cls):
            return file_type

    return None


def non_empty_cte_data(data: List[Any]) -> List[Any]:
    return data or [(None,)]


async def chat_photo_check_entities(
    db: AsyncSession, chat_photo: ChatPhoto, bot_id: int
) -> List[Tuple[str, bool, bool]]:
    file_col = column("file_unique_id", String)
    file_cte = (
        values(file_col)
        .data([(chat_photo.small_file_unique_id,), (chat_photo.big_file_unique_id,)])
        .cte("input_files")
    )

    tf = aliased(TelegramFile)
    bf = aliased(BotFile)

    query = (
        select(
            file_cte.c.file_unique_id,
            case((tf.file_unique_id.is_not(None), True), else_=False).label("exists"),
            case((bf.file_unique_id.is_not(None), True), else_=False).label(
                "bot_relation"
            ),
        )
        .outerjoin(tf, tf.file_unique_id == file_cte.c.file_unique_id)
        .outerjoin(
            bf, (bf.file_unique_id == file_cte.c.file_unique_id) & (bf.bot_id == bot_id)
        )
    )

    result = await db.execute(query)
    data = [tuple(row) for row in result.all()]

    return data


async def insert_chat_photo_entities(
    db: AsyncSession,
    files: List[Tuple[str, bool, bool]],
    bot_files: Dict[str, str],
    bot_id: int,
) -> None:
    files_json: List[Dict[str, str]] = []
    insert_bot_files: List[Tuple[str, str]] = []

    for file_id, is_exists, is_bot_relation in files:
        if not is_exists:
            files_json.append(
                {
                    "file_unique_id": file_id,
                    "file_type": FileType.CHAT_PHOTO.value,
                },
            )

        if not is_bot_relation:
            bot_file_id = bot_files.get(file_id)
            if bot_file_id:
                insert_bot_files.append((file_id, bot_file_id))

    if files_json:
        await bulk_insert_files(db, files_json)

    if insert_bot_files:
        await bulk_insert_bot_files(db, insert_bot_files, bot_id)


async def insert_chat_photo_if_not_exist(
    db: AsyncSession, photo: ChatPhoto, bot_id: int
) -> None:
    bot_files: Dict[str, str] = {
        photo.small_file_unique_id: photo.small_file_id,
        photo.big_file_unique_id: photo.big_file_id,
    }
    res = await chat_photo_check_entities(db, photo, bot_id)
    await insert_chat_photo_entities(db, res, bot_files, bot_id)


async def check_entities(
    db: AsyncSession,
    chat_ids: Iterable[int],
    user_ids: Iterable[int],
    messages: Iterable[Tuple[int, int]],
    file_ids: Iterable[str],
    bot_id: int,
) -> List[
    Tuple[
        Optional[int],
        Optional[int],
        Optional[int],
        Optional[str],
        bool,
        Optional[bool],
        int,
    ]
]:
    chat_col = column("chat_id", BigInteger)
    user_col = column("user_id", BigInteger)
    msg_col = column("message_id", BigInteger)
    file_col = column("file_unique_id", String)

    chat_cte = (
        values(chat_col)
        .data(non_empty_cte_data([(chat_id,) for chat_id in chat_ids]))
        .cte("input_chats")
    )

    user_cte = (
        values(user_col)
        .data(non_empty_cte_data([(user_id,) for user_id in user_ids]))
        .cte("input_users")
    )

    msg_cte = (
        values(chat_col, msg_col)
        .data(non_empty_cte_data([(chat_id, msg_id) for chat_id, msg_id in messages]))
        .cte("input_messages")
    )

    file_cte = (
        values(file_col)
        .data(non_empty_cte_data([(file_unique_id,) for file_unique_id in file_ids]))
        .cte("input_files")
    )

    tc = aliased(TelegramChat)
    tu = aliased(TelegramUser)
    tm = aliased(TelegramMessage)
    tf = aliased(TelegramFile)
    bm = aliased(BotMessage)
    bf = aliased(BotFile)

    chat_query = select(
        cast(chat_cte.c.chat_id, BigInteger),
        cast(None, BigInteger).label("user_id"),
        cast(None, BigInteger).label("message_id"),
        cast(None, String).label("file_unique_id"),
        case((tc.id.is_not(None), True), else_=False).label("exists"),
        cast(None, Boolean).label("bot_relation"),
        literal(EntityCheckResultType.CHAT.value).label("type"),
    )

    user_query = select(
        cast(None, BigInteger).label("chat_id"),
        cast(user_cte.c.user_id, BigInteger),
        cast(None, BigInteger).label("message_id"),
        cast(None, String).label("file_unique_id"),
        case((tu.id.is_not(None), True), else_=False).label("exists"),
        cast(None, Boolean).label("bot_relation"),
        literal(EntityCheckResultType.USER.value).label("type"),
    )

    msg_query = select(
        cast(msg_cte.c.chat_id, BigInteger),
        cast(None, BigInteger).label("user_id"),
        cast(msg_cte.c.message_id, BigInteger),
        cast(None, String).label("file_unique_id"),
        case((tm.id.is_not(None), True), else_=False).label("exists"),
        case((bm.message_id.is_not(None), True), else_=False).label("bot_relation"),
        literal(EntityCheckResultType.MESSAGE.value).label("type"),
    )

    file_query = select(
        cast(None, BigInteger).label("chat_id"),
        cast(None, BigInteger).label("user_id"),
        cast(None, BigInteger).label("message_id"),
        cast(file_cte.c.file_unique_id, String),
        case((tf.file_unique_id.is_not(None), True), else_=False).label("exists"),
        case((bf.file_unique_id.is_not(None), True), else_=False).label("bot_relation"),
        literal(EntityCheckResultType.FILE.value).label("type"),
    )

    full_query = chat_query.union_all(user_query, msg_query, file_query)
    result = await db.execute(full_query)
    data = [tuple(row) for row in result.all()]

    return data


def collect_entities(
    object: Any,
) -> Tuple[
    Dict[int, UpdateUser],
    Dict[int, Chat],
    Dict[Tuple[int, int], Message],
    Dict[str, Dict[str, Any]],
]:
    users = find_instances(object, UpdateUser)
    unique_users = deduplicate(users, "id")

    chats = find_instances(object, Chat)
    unique_chats = deduplicate(chats, "id")

    messages = find_instances(object, Message)
    unique_messages = deduplicate_compound(messages, ("chat_id", "id"))

    files = find_objects_with_attributes(object, ("file_unique_id", "file_id"))
    unique_files = deduplicate(files, "file_unique_id")

    unique_files_json: Dict[str, Dict[str, Any]] = {}
    for file_unique_id, file in unique_files.items():
        file_type = get_file_type(file)
        if file_type is None or not isinstance(file, TelegramObject):
            continue

        file_dict = file.to_dict()
        file_dict["file_type"] = file_type

        unique_files_json[file_unique_id] = file_dict

    return unique_users, unique_chats, unique_messages, unique_files_json


async def bulk_update_users(
    db: AsyncSession, existing_users: Iterable[UpdateUser]
) -> None:
    ids = [u.id for u in existing_users]

    stmt = (
        update(TelegramUser)
        .where(TelegramUser.id.in_(ids))
        .values(
            first_name=case(
                {u.id: u.first_name for u in existing_users}, value=TelegramUser.id
            ),
            last_name=case(
                {u.id: u.last_name for u in existing_users}, value=TelegramUser.id
            ),
            username=case(
                {u.id: u.username for u in existing_users}, value=TelegramUser.id
            ),
            language_code=case(
                {u.id: u.language_code for u in existing_users}, value=TelegramUser.id
            ),
            is_premium=case(
                {u.id: bool(u.is_premium) for u in existing_users},
                value=TelegramUser.id,
            ),
            is_bot=case(
                {u.id: u.is_bot for u in existing_users}, value=TelegramUser.id
            ),
        )
    )
    await db.execute(stmt)


async def bulk_insert_users(db: AsyncSession, new_users: Iterable[UpdateUser]) -> None:
    user_dicts = [
        {
            "id": u.id,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "username": u.username,
            "language_code": u.language_code,
            "is_premium": bool(u.is_premium),
            "is_bot": u.is_bot,
        }
        for u in new_users
    ]

    stmt = insert(TelegramUser).values(user_dicts)
    await db.execute(stmt)


async def bulk_update_chats(db: AsyncSession, existing_chats: Iterable[Chat]) -> None:
    ids = [c.id for c in existing_chats]

    stmt = (
        update(TelegramChat)
        .where(TelegramChat.id.in_(ids))
        .values(
            type=case(
                {c.id: ChatType(c.type) for c in existing_chats}, value=TelegramChat.id
            ),
            title=case({c.id: c.title for c in existing_chats}, value=TelegramChat.id),
            username=case(
                {c.id: c.username for c in existing_chats}, value=TelegramChat.id
            ),
            first_name=case(
                {c.id: c.first_name for c in existing_chats}, value=TelegramChat.id
            ),
            last_name=case(
                {c.id: c.last_name for c in existing_chats}, value=TelegramChat.id
            ),
            is_forum=case(
                {c.id: bool(c.is_forum) for c in existing_chats}, value=TelegramChat.id
            ),
            is_direct_messages=case(
                {c.id: bool(c.is_direct_messages) for c in existing_chats},
                value=TelegramChat.id,
            ),
        )
    )
    await db.execute(stmt)


async def bulk_insert_chats(db: AsyncSession, new_chats: Iterable[Chat]) -> None:
    chat_dicts = [
        {
            "id": c.id,
            "type": ChatType(c.type),
            "title": c.title,
            "username": c.username,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "is_forum": bool(c.is_forum),
            "is_direct_messages": bool(c.is_direct_messages),
        }
        for c in new_chats
    ]

    stmt = insert(TelegramChat).values(chat_dicts)

    await db.execute(stmt)


def get_message_type(message: Message) -> MessageType:
    for attr, msg_type in MESSAGE_TYPE_ATTRIBUTE_MAP.items():
        if getattr(message, attr, None):
            return msg_type

    return MessageType.SERVICE


def get_message_excluded_fields(message: Message) -> Set[str]:
    excluded_fields = {
        "message_id",
        "chat",
        "message_thread_id",
        "text",
        "caption",
        "from",
        "sender_chat",
        "sender_boost_count",
        "sender_business_bot",
        "date",
        "edit_date",
        "business_connection_id",
        "is_topic_message",
        "is_automatic_forward",
        "has_media_spoiler",
        "has_protected_content",
        "is_from_offline",
        "is_paid_post",
        "author_signature",
        "paid_star_count",
        "delete_chat_photo",
        "group_chat_created",
        "supergroup_chat_created",
        "channel_chat_created",
    }

    if message.delete_chat_photo:
        excluded_fields.remove("delete_chat_photo")

    if message.group_chat_created:
        excluded_fields.remove("group_chat_created")

    if message.supergroup_chat_created:
        excluded_fields.remove("supergroup_chat_created")

    if message.channel_chat_created:
        excluded_fields.remove("channel_chat_created")

    return excluded_fields


def make_message_db_object(message: Message) -> TelegramMessage:
    message_dict = message.to_dict()
    excluded_fields = get_message_excluded_fields(message)

    other_data = remove_fields(message_dict, excluded_fields, ("file_id",))
    message_type = get_message_type(message)

    return TelegramMessage(
        id=message.id,
        chat_id=message.chat_id,
        message_type=message_type,
        message_thread_id=message.message_thread_id,
        text=message.text,
        caption=message.caption,
        from_user_id=message.from_user.id if message.from_user else None,
        sender_chat_id=message.sender_chat.id if message.sender_chat else None,
        sender_boost_count=message.sender_boost_count,
        sender_business_bot_id=(
            message.sender_business_bot.id if message.sender_business_bot else None
        ),
        date=message.date,
        edit_date=message.edit_date,
        business_connection_id=message.business_connection_id,
        is_topic_message=bool(message.is_topic_message),
        is_automatic_forward=bool(message.is_automatic_forward),
        has_media_spoiler=bool(message.has_media_spoiler),
        has_protected_content=bool(message.has_protected_content),
        is_from_offline=bool(message.is_from_offline),
        is_paid_post=bool(message.is_paid_post),
        author_signature=message.author_signature,
        paid_star_count=message.paid_star_count,
        other_data=other_data or null(),
    )


def bulk_prepare_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    return [
        {
            "id": msg.id,
            "chat_id": msg.chat_id,
            "message_type": get_message_type(msg),
            "message_thread_id": msg.message_thread_id,
            "text": msg.text,
            "caption": msg.caption,
            "from_user_id": msg.from_user.id if msg.from_user else None,
            "sender_chat_id": msg.sender_chat.id if msg.sender_chat else None,
            "sender_boost_count": msg.sender_boost_count,
            "sender_business_bot_id": (
                msg.sender_business_bot.id if msg.sender_business_bot else None
            ),
            "date": msg.date,
            "edit_date": msg.edit_date,
            "business_connection_id": msg.business_connection_id,
            "is_topic_message": bool(msg.is_topic_message),
            "is_automatic_forward": bool(msg.is_automatic_forward),
            "has_media_spoiler": bool(msg.has_media_spoiler),
            "has_protected_content": bool(msg.has_protected_content),
            "is_from_offline": bool(msg.is_from_offline),
            "is_paid_post": bool(msg.is_paid_post),
            "author_signature": msg.author_signature,
            "paid_star_count": msg.paid_star_count,
            "other_data": remove_fields(
                msg.to_dict(), get_message_excluded_fields(msg), ("file_id",)
            )
            or null(),
        }
        for msg in messages
    ]


async def bulk_insert_messages(db: AsyncSession, messages: List[Message]) -> None:
    dicts = bulk_prepare_messages(messages)
    stmt = insert(TelegramMessage).values(dicts)
    await db.execute(stmt)


def bulk_prepare_files(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "file_unique_id": file["file_unique_id"],
            "file_type": file["file_type"],
            "file_size": file.get("file_size", null()),
            "mime_type": file.get("mime_type", null()),
            "other_data": remove_fields(
                file,
                FILE_EXCLUDED_FIELDS,
            )
            or null(),
        }
        for file in files
    ]


async def bulk_insert_files(db: AsyncSession, files: List[Dict[str, Any]]) -> None:
    dicts = bulk_prepare_files(files)
    stmt = insert(TelegramFile).values(dicts)
    await db.execute(stmt)


async def bulk_insert_bot_messages(
    db: AsyncSession, data: List[Tuple[int, int]], bot_id: int
) -> None:
    chat_dicts = [
        {
            "bot_id": bot_id,
            "chat_id": chat_id,
            "message_id": message_id,
        }
        for chat_id, message_id in data
    ]

    stmt = insert(BotMessage).values(chat_dicts)

    await db.execute(stmt)


async def bulk_insert_bot_files(
    db: AsyncSession, data: List[Tuple[str, str]], bot_id: int
) -> None:
    chat_dicts = [
        {
            "bot_id": bot_id,
            "file_unique_id": file_unique_id,
            "file_id": file_id,
        }
        for file_unique_id, file_id in data
    ]

    stmt = insert(BotFile).values(chat_dicts)

    await db.execute(stmt)


async def log_object(
    db: AsyncSession, object: Any, bot_id: int
) -> Tuple[bool, bool, bool, bool, bool, bool]:
    unique_users, unique_chats, unique_messages, unique_files_json = collect_entities(
        object
    )
    chat_ids = list(unique_chats.keys())
    user_ids = list(unique_users.keys())
    message_ids = list(unique_messages.keys())
    files_ids = list(unique_files_json.keys())

    check_result = await check_entities(
        db, chat_ids, user_ids, message_ids, files_ids, bot_id
    )

    new_users: List[UpdateUser] = []
    new_chats: List[Chat] = []
    new_messages: List[Message] = []
    new_bot_messages: List[Tuple[int, int]] = []
    new_files: List[Dict[str, Any]] = []
    new_bot_files: List[Tuple[str, str]] = []

    for (
        chat_id,
        user_id,
        message_id,
        file_unique_id,
        exists,
        bot_relation,
        entity_type,
    ) in check_result:
        if entity_type == EntityCheckResultType.USER.value:
            if user_id:
                user_obj = unique_users[user_id]
                if not exists:
                    new_users.append(user_obj)

            continue

        if entity_type == EntityCheckResultType.CHAT.value:
            if chat_id:
                chat_obj = unique_chats[chat_id]
                if not exists:
                    new_chats.append(chat_obj)

            continue

        if entity_type == EntityCheckResultType.MESSAGE.value:
            if chat_id and message_id:
                pk_tuple = (chat_id, message_id)
                msg_obj = unique_messages[pk_tuple]
                if not exists:
                    new_messages.append(msg_obj)

                if not bot_relation:
                    new_bot_messages.append(pk_tuple)

            continue

        if entity_type == EntityCheckResultType.FILE.value:
            if file_unique_id:
                file_obj = unique_files_json[file_unique_id]
                if not exists:
                    new_files.append(file_obj)

                if not bot_relation:
                    file_id = file_obj["file_id"]
                    new_bot_files.append((file_unique_id, file_id))

    if new_users:
        await bulk_insert_users(db, new_users)

    if new_chats:
        await bulk_insert_chats(db, new_chats)

    if new_messages:
        await bulk_insert_messages(db, new_messages)

    if new_bot_messages:
        await bulk_insert_bot_messages(db, new_bot_messages, bot_id)

    if new_files:
        await bulk_insert_files(db, new_files)

    if new_bot_files:
        await bulk_insert_bot_files(db, new_bot_files, bot_id)

    return (
        bool(new_users),
        bool(new_chats),
        bool(new_messages),
        bool(new_bot_messages),
        bool(new_files),
        bool(new_bot_files),
    )


async def update_message(
    db: AsyncSession, message: Message, bot_id: int, skip_log: bool = False
) -> None:
    if not skip_log:
        res = await log_object(db, message, bot_id)
        if res[3]:
            return

    dicts = bulk_prepare_messages([message])
    msg_dict = dicts[0]

    del msg_dict["id"]
    del msg_dict["chat_id"]

    stmt = (
        update(TelegramMessage)
        .where(
            TelegramMessage.id == message.id,
            TelegramMessage.chat_id == message.chat_id,
        )
        .values(msg_dict)
    )
    await db.execute(stmt)
