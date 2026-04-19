from typing import Dict
from urllib.parse import urljoin
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.db.session import get_db
from app.schemas.common_responses import DetailResponse
from app.routes import auth, users
from app.routes.telegram import telegram
from app.core.limiter import limiter


router = APIRouter(prefix=settings.API_PREFIX)


@router.get("/", response_model=Dict[str, str], tags=["root"])
@limiter.limit("10/minute")
def info(request: Request, response: Response) -> Dict[str, str]:
    return {
        "title": settings.APP_TITLE,
        "version": settings.APP_VERSION,
        "docs_url": urljoin(str(settings.API_URL), "/docs"),
    }


@router.get("/health", response_model=DetailResponse, tags=["root"])
@limiter.limit("10/minute")
async def health_check(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    try:
        await db.execute(text("SELECT 1"))
        return DetailResponse(detail="ok")
    except Exception:
        response.status_code = 503
        return DetailResponse(detail="database unavailable")


router.include_router(auth.router)
router.include_router(users.router)
router.include_router(telegram.router)
