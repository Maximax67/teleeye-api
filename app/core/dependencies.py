from typing import Any, Awaitable, Callable, Dict

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import UserRole
from app.db.session import get_db
from app.schemas.auth import AuthorizedUser, AuthorizedUserDb
from app.services.auth import authorize_user, authorize_user_db, validate_refresh_token

bearer_scheme = HTTPBearer(auto_error=True)


def require_authorization(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> AuthorizedUser:
    return authorize_user(credentials)


async def require_authorization_db(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthorizedUserDb:
    return await authorize_user_db(credentials, db)


def require_refresh_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Dict[str, Any]:
    return validate_refresh_token(credentials)


def require_role(
    role: UserRole,
) -> Callable[[HTTPAuthorizationCredentials], AuthorizedUser]:
    def dependency(
        credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    ) -> AuthorizedUser:
        return authorize_user(credentials, role)

    return dependency


def require_role_db(
    role: UserRole,
) -> Callable[
    [HTTPAuthorizationCredentials, AsyncSession], Awaitable[AuthorizedUserDb]
]:
    async def dependency(
        credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
        db: AsyncSession = Depends(get_db),
    ) -> AuthorizedUserDb:
        return await authorize_user_db(credentials, db, role)

    return dependency
