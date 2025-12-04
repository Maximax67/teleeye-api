import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi_pagination import add_pagination
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.limiter import limiter
from app.core.logger import logger
from app.db.session import setup_db
from app.routes import api
from app.core.settings import settings
from app.core.utils import periodic_cleanup


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await setup_db()
    cleanup_task = asyncio.create_task(periodic_cleanup())

    logger.info("App started successfully")

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    logger.info("App shutdown")


origins = settings.ALLOWED_ORIGINS.split(",") if settings.ALLOWED_ORIGINS else []

app = FastAPI(
    lifespan=lifespan,
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
)

app.state.limiter = limiter

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Retry-After",
        "X-RateLimit-Reset",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
    ],
)

add_pagination(app)

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.include_router(api.router)

if settings.ATTACH_FRONTEND:
    app.mount("/", StaticFiles(directory=settings.FRONTEND_PATH, html=True))
