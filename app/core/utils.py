import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Type, TypeVar
from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from user_agents import parse  # type: ignore[import-untyped]

from app.core.enums import UserRole
from app.core.settings import settings
from app.core.logger import logger
from app.db.models.otp_code import OtpCode
from app.db.models.user import User
from app.db.session import async_session
from app.db.models.session import Session
from app.schemas.auth import AuthorizedUser


async def cleanup_old_data() -> None:
    logger.info("Cleanup old database data")

    cutoff_sessions = datetime.now(timezone.utc) - timedelta(
        days=settings.REFRESH_TOKEN_EXPIRES_DAYS
    )
    cutoff_otp = datetime.now(timezone.utc) - timedelta(minutes=settings.OTP_TTL)

    try:
        async with async_session() as db:
            async with db.begin():
                await db.execute(
                    delete(Session).where(Session.updated_at < cutoff_sessions)
                )
                await db.execute(delete(OtpCode).where(OtpCode.created_at < cutoff_otp))
    except Exception as e:
        logger.error(e)


async def periodic_cleanup(interval_seconds: int = 3600) -> None:
    while True:
        await cleanup_old_data()
        await asyncio.sleep(interval_seconds)


def get_session_name_from_user_agent(request: Request) -> str:
    user_agent_str = request.headers.get("user-agent", "")
    user_agent = parse(user_agent_str)

    return str(user_agent).replace(" / ", ", ")


def generate_numeric_otp(n_digits: int) -> str:
    if n_digits <= 0:
        raise ValueError("n_digits must be positive")

    upper = 10**n_digits
    code = secrets.randbelow(upper)

    return f"{code:0{n_digits}d}"


async def update_user_bool_field(
    authorized_user: AuthorizedUser,
    user_id: int,
    db: AsyncSession,
    field_name: str,
    value: bool,
    error_msg: str,
) -> User:
    q = await db.execute(select(User).where(User.id == user_id))
    user = q.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if (
        user.role == UserRole.ADMIN or user.role == UserRole.GOD
    ) and authorized_user.role != UserRole.GOD:
        raise HTTPException(status_code=403, detail="Forbidden")

    current_value = getattr(user, field_name)
    if current_value == value:
        raise HTTPException(status_code=409, detail=error_msg)

    setattr(user, field_name, value)
    await db.commit()

    return user


FindType = TypeVar("FindType")


def find_instances(
    obj: object, target_type: Type[FindType], seen: Optional[Set[int]] = None
) -> List[FindType]:
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return []

    seen.add(obj_id)
    found: List[FindType] = []

    # If the object itself is the type we want
    if isinstance(obj, target_type):
        found.append(obj)

    # Explore attributes
    if isinstance(obj, dict):
        for value in obj.values():
            found.extend(find_instances(value, target_type, seen))

    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            found.extend(find_instances(item, target_type, seen))

    else:
        # Inspect object attributes (ignore private/internal)
        for attr in dir(obj):
            if attr.startswith("_"):
                continue

            try:
                value = getattr(obj, attr)
            except Exception:
                continue

            found.extend(find_instances(value, target_type, seen))

    return found


def find_objects_with_attributes(
    obj: object, required_attrs: Iterable[str], seen: Optional[Set[int]] = None
) -> List[object]:
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return []

    seen.add(obj_id)
    results: List[object] = []

    if all(hasattr(obj, attr) for attr in required_attrs):
        results.append(obj)

    if isinstance(obj, dict):
        for v in obj.values():
            results.extend(find_objects_with_attributes(v, required_attrs, seen))

    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            results.extend(find_objects_with_attributes(item, required_attrs, seen))

    else:
        # Inspect object attributes (ignore private/internal)
        for attr in dir(obj):
            if attr.startswith("_"):
                continue

            try:
                value = getattr(obj, attr)
            except Exception:
                continue

            results.extend(find_objects_with_attributes(value, required_attrs, seen))

    return results


DeduplicateType = TypeVar("DeduplicateType")


def deduplicate(
    objects: List[DeduplicateType], field_name: str
) -> Dict[Any, DeduplicateType]:
    unique: Dict[Any, DeduplicateType] = {}
    for obj in objects:
        try:
            key = getattr(obj, field_name)
        except AttributeError:
            # Skip objects that lack any of the fields
            continue

        if key not in unique:
            unique[key] = obj

    return unique


def deduplicate_compound(
    objects: List[DeduplicateType], field_names: Iterable[str]
) -> Dict[Tuple[Any, ...], DeduplicateType]:
    unique: Dict[Tuple[Any, ...], DeduplicateType] = {}
    for obj in objects:
        try:
            key = tuple(getattr(obj, f) for f in field_names)
        except AttributeError:
            # Skip objects that lack any of the fields
            continue

        if key not in unique:
            unique[key] = obj

    return unique


def remove_fields(
    data: Dict[str, Any],
    exclude: Optional[Iterable[str]] = None,
    exclude_nested: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    if exclude:
        for item in exclude:
            if item in data:
                del data[item]

    if not exclude_nested:
        return data

    def recurse(value: Any) -> Any:
        if isinstance(value, dict):
            for key in list(value.keys()):
                if key in exclude_nested:
                    del value[key]
                else:
                    value[key] = recurse(value[key])
        elif isinstance(value, list):
            return [recurse(v) for v in value]

        return value

    data = recurse(data)

    return data
