from typing import Any, Dict, Union
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi_pagination import Page
from fastapi_pagination.ext.sqlalchemy import paginate
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_authorization, require_role
from app.core.enums import UserRole
from app.core.limiter import limiter
from app.core.utils import update_user_bool_field
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.user import UserResponse, UserUpdateRequest
from app.services.bloom_filter import bloom_filter
from app.schemas.auth import AuthorizedUser

router = APIRouter(prefix="/users", tags=["users"])

common_responses: Dict[Union[int, str], Dict[str, Any]] = {
    404: {
        "description": "User not found",
        "content": {"application/json": {"example": {"detail": "User not found"}}},
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
    "",
    response_model=Page[UserResponse],
    responses={403: common_responses[403]},
    dependencies=[Depends(require_role(UserRole.ADMIN))],
)
@limiter.limit("10/minute")
async def get_all_users(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Page[UserResponse]:
    page: Page[Any] = await paginate(db, select(User).order_by(User.created_at))
    page.items = [UserResponse.model_validate(user) for user in page.items]

    return page


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    responses=common_responses,
)
@limiter.limit("10/minute")
async def get_user(
    user_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    if (
        user_id != current_user.id
        and current_user.role != UserRole.ADMIN
        and current_user.role != UserRole.GOD
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    q = await db.execute(select(User).where(User.id == user_id))
    user = q.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    responses=common_responses,
    dependencies=[Depends(require_role(UserRole.GOD))],
)
@limiter.limit("5/minute")
async def update_user(
    user_id: int,
    user_update: UserUpdateRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    q = await db.execute(select(User).where(User.id == user_id))
    user = q.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = user_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    await db.commit()

    return UserResponse.model_validate(user)


@router.delete(
    "/{user_id}",
    status_code=204,
    responses=common_responses,
)
@limiter.limit("5/minute")
async def delete_user(
    user_id: int,
    request: Request,
    response: Response,
    authorized_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if authorized_user.id != user_id and authorized_user.role != UserRole.GOD:
        raise HTTPException(status_code=403, detail="Forbidden")

    q = await db.execute(
        select(User).options(joinedload(User.sessions)).where(User.id == user_id)
    )
    user = q.unique().scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for session in user.sessions:
        bloom_filter.add(session.access_jti)

    await db.delete(user)
    await db.commit()

    return Response(status_code=204)


@router.post(
    "/{user_id}/ban",
    response_model=UserResponse,
    responses={**common_responses, 409: {"description": "User is already banned"}},
)
@limiter.limit("5/minute")
async def ban_user(
    user_id: int,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    authorized_user: AuthorizedUser = Depends(require_role(UserRole.ADMIN)),
) -> UserResponse:
    user = await update_user_bool_field(
        authorized_user, user_id, db, "is_banned", True, "User is already banned"
    )

    return UserResponse.model_validate(user)


@router.post(
    "/{user_id}/unban",
    response_model=UserResponse,
    responses={**common_responses, 409: {"description": "User is not banned"}},
)
@limiter.limit("5/minute")
async def unban_user(
    user_id: int,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    authorized_user: AuthorizedUser = Depends(require_role(UserRole.ADMIN)),
) -> UserResponse:
    user = await update_user_bool_field(
        authorized_user, user_id, db, "is_banned", False, "User is not banned"
    )

    return UserResponse.model_validate(user)


@router.post(
    "/{user_id}/email/verify",
    response_model=UserResponse,
    responses={
        **common_responses,
        409: {"description": "User email is already verified"},
    },
)
@limiter.limit("5/minute")
async def verify_email(
    user_id: int,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    authorized_user: AuthorizedUser = Depends(require_role(UserRole.ADMIN)),
) -> UserResponse:
    user = await update_user_bool_field(
        authorized_user,
        user_id,
        db,
        "email_verified",
        True,
        "User email is already verified",
    )

    return UserResponse.model_validate(user)


@router.post(
    "/{user_id}/email/revoke-verification",
    response_model=UserResponse,
    responses={**common_responses, 409: {"description": "User email is not verified"}},
)
@limiter.limit("5/minute")
async def revoke_email_verification(
    user_id: int,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    authorized_user: AuthorizedUser = Depends(require_role(UserRole.ADMIN)),
) -> UserResponse:
    user = await update_user_bool_field(
        authorized_user,
        user_id,
        db,
        "email_verified",
        False,
        "User email is not verified",
    )

    return UserResponse.model_validate(user)
