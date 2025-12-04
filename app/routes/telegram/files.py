from typing import Any, Dict, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.core.logger import logger
from app.core.dependencies import require_authorization
from app.db.session import get_db
from app.schemas.auth import AuthorizedUser
from app.schemas.telegram.file import FileInfoResponse
from app.services.telegram.bots import (
    get_file_and_bot_token,
    get_telegram_bot_from_encrypted,
)

router = APIRouter(prefix="/files", tags=["telegram-files"])

common_responses: Dict[Union[int, str], Dict[str, Any]] = {
    502: {
        "description": "Telegram API Error",
        "content": {"application/json": {"example": {"detail": "Telegram API Error"}}},
    },
    404: {
        "description": "File not found",
        "content": {"application/json": {"example": {"detail": "File not found"}}},
    },
    403: {
        "description": "Forbidden",
        "content": {"application/json": {"example": {"detail": "Forbidden"}}},
    },
    401: {
        "description": "Unauthorized",
        "content": {"application/json": {"example": {"detail": "Invalid token"}}},
    },
}


@router.get(
    "/{file_unique_id}",
    responses=common_responses,
)
@limiter.limit("5/minute")
async def get_file(
    file_unique_id: str,
    request: Request,
    response: Response,
    bot_id: Optional[int] = Query(None, description="Bot ID to fetch file"),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    bot_file, token = await get_file_and_bot_token(
        db, file_unique_id, current_user, bot_id, preload_file=True
    )
    telegram_bot = get_telegram_bot_from_encrypted(bot_file.bot_id, token)

    try:
        file = await telegram_bot.get_file(bot_file.file_id)
    except Exception as e:
        logger.error(e)
        raise HTTPException(status_code=502, detail="Telegram API error")

    file_bytes = await file.download_as_bytearray()
    media_type = bot_file.file.mime_type or "application/octet-stream"

    return Response(content=bytes(file_bytes), media_type=media_type)


@router.get(
    "/{file_unique_id}/info",
    responses=common_responses,
    response_model=FileInfoResponse,
)
@limiter.limit("5/minute")
async def get_file_info(
    file_unique_id: str,
    request: Request,
    response: Response,
    bot_id: Optional[int] = Query(None, description="Bot ID to fetch file"),
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    bot_file, _ = await get_file_and_bot_token(
        db, file_unique_id, current_user, bot_id, preload_file=True
    )
    file_info = bot_file.file.to_dict()

    return file_info
