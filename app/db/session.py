from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from app.core.settings import settings


engine = create_async_engine(settings.DATABASE_URL.get_secret_value(), echo=True)
async_session = async_sessionmaker(
    bind=engine, expire_on_commit=False, class_=AsyncSession
)


async def setup_db() -> None:
    if settings.DATABASE_URL.get_secret_value().startswith("sqlite"):
        async with async_session() as db:
            async with db.begin():
                await db.execute(text("PRAGMA foreign_keys=ON"))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as db:
        yield db
