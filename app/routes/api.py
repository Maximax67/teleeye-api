from typing import Dict
from urllib.parse import urljoin
from fastapi import APIRouter, Request, Response

from app.core.settings import settings
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
def health_check(request: Request, response: Response) -> DetailResponse:
    return DetailResponse(detail="ok")


router.include_router(auth.router)
router.include_router(users.router)
router.include_router(telegram.router)
