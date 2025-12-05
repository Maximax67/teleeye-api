from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, Union
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, func, select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

import jwt
from fastapi import HTTPException
from passlib.context import CryptContext
from secrets import token_urlsafe

from app.core.utils import generate_numeric_otp
from app.db.models.otp_code import OtpCode
from app.db.models.session import Session
from app.db.models.user import User
from app.schemas.auth import AuthorizedUser, AuthorizedUserDb
from app.schemas.user import UserResponse
from app.services.email import send_email
from app.core.settings import settings
from app.core.enums import OtpCodeType, TokenType, UserRole
from app.services.bloom_filter import bloom_filter


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_jwt_token(
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    jti: str,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if settings.JWT_ISSUER:
        payload["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE

    payload["jti"] = jti

    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(
        payload,
        settings.JWT_SECRET.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )

    return token


def decode_jwt_token(token: str) -> Dict[str, Any]:
    try:
        payload: Dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE if settings.JWT_AUDIENCE else None,
            issuer=settings.JWT_ISSUER if settings.JWT_ISSUER else None,
            options={
                "verify_aud": bool(settings.JWT_AUDIENCE),
                "verify_iss": bool(settings.JWT_ISSUER),
            },
        )

        jti = payload.get("jti")
        if jti is None or jti in bloom_filter:
            raise HTTPException(status_code=401, detail="Invalid token")

        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def authorize_user(
    credentials: HTTPAuthorizationCredentials,
    role: Optional[UserRole] = None,
) -> AuthorizedUser:
    payload = decode_jwt_token(credentials.credentials)
    if payload.get("type") != TokenType.ACCESS:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid subject in token")

    user_role = payload.get("role")
    if user_role is None or user_role not in UserRole:
        raise HTTPException(status_code=401, detail="Invalid role in token")

    is_email_verified = payload.get("email_verified")
    if is_email_verified is None or not isinstance(is_email_verified, bool):
        raise HTTPException(status_code=401, detail="Invalid email verified in token")

    if role and role != user_role and user_role != UserRole.GOD:
        raise HTTPException(status_code=403, detail="Forbidden")

    jti = payload["jti"]

    return AuthorizedUser(
        id=user_id,
        role=UserRole(user_role),
        is_email_verified=is_email_verified,
        jti=jti,
    )


def validate_refresh_token(credentials: HTTPAuthorizationCredentials) -> Dict[str, Any]:
    payload = decode_jwt_token(credentials.credentials)

    if payload.get("type") != TokenType.REFRESH:
        raise HTTPException(status_code=401, detail="Invalid token")

    if payload.get("sub") is None:
        raise HTTPException(status_code=401, detail="Invalid subject in token")

    return payload


async def authorize_user_db(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
    role: Optional[UserRole] = None,
) -> AuthorizedUserDb:
    authorized_user = authorize_user(credentials, role)

    q = await db.execute(select(User).where(User.id == authorized_user.id))
    user = q.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if role and role != user.role and user.role != UserRole.GOD:
        raise HTTPException(status_code=403, detail="Forbidden")

    return AuthorizedUserDb(user=user, jti=authorized_user.jti)


async def issue_token_pair(
    db: AsyncSession,
    user: User,
    session_name: Optional[str] = None,
    session: Optional[Session] = None,
) -> Tuple[str, str, int, int]:
    if user.is_banned:
        raise HTTPException(status_code=403, detail="User is banned")

    subject = str(user.id)
    access_jti = token_urlsafe(32)
    refresh_jti = token_urlsafe(32)

    if session:
        session.refresh_jti = refresh_jti
        session.access_jti = access_jti
        session.name = session_name
    else:
        sessions_count = await db.execute(
            select(func.count()).where(Session.user_id == user.id)
        )
        if sessions_count.scalar_one() >= settings.MAX_USER_SESSIONS:
            raise HTTPException(
                status_code=403,
                detail=f"User already has the maximum number of sessions ({settings.MAX_USER_SESSIONS})",
            )

        db.add(
            Session(
                user_id=user.id,
                refresh_jti=refresh_jti,
                access_jti=access_jti,
                name=session_name,
            )
        )

    await db.commit()

    access = create_jwt_token(
        subject=subject,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRES_MINUTES),
        jti=access_jti,
        extra_claims={
            "email_verified": user.email_verified,
            "role": user.role.value,
        },
    )
    refresh = create_jwt_token(
        subject=subject,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRES_DAYS),
        jti=refresh_jti,
    )
    expires_in = int(
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRES_MINUTES).total_seconds()
    )
    refresh_expires_in = int(
        timedelta(days=settings.REFRESH_TOKEN_EXPIRES_DAYS).total_seconds()
    )

    return access, refresh, expires_in, refresh_expires_in


async def rotate_refresh_token(
    db: AsyncSession, user_id: int, old_jti: str, session_name: str
) -> Tuple[str, str, int, int]:
    q = await db.execute(
        select(Session)
        .options(joinedload(Session.user))
        .join(Session.user)
        .where(Session.refresh_jti == old_jti, User.id == user_id)
    )
    session = q.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    bloom_filter.add(session.access_jti)

    return await issue_token_pair(db, session.user, session_name, session)


async def revoke_all_sessions(db: AsyncSession, user_id: int) -> None:
    q = await db.execute(select(Session.access_jti).where(Session.user_id == user_id))
    for (access_jti,) in q.all():
        bloom_filter.add(access_jti)

    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.commit()


async def revoke_session_by_jti(db: AsyncSession, jti: str) -> None:
    session = (
        await db.execute(select(Session).where(Session.refresh_jti == jti))
    ).scalar_one_or_none()

    if session:
        bloom_filter.add(session.access_jti)
        await db.delete(session)
        await db.commit()


async def issue_otp(
    db: AsyncSession,
    user_id: int,
    type: OtpCodeType,
) -> str:
    otp_value = generate_numeric_otp(settings.OTP_LENGTH)

    await db.execute(
        delete(OtpCode).where(OtpCode.user_id == user_id, OtpCode.type == type)
    )
    db.add(
        OtpCode(
            user_id=user_id,
            type=type,
            code=otp_value,
        )
    )
    await db.commit()

    return otp_value


async def send_verification_email(
    db: AsyncSession, user: Union[User, UserResponse]
) -> None:
    if user.email_verified:
        return

    otp = await issue_otp(db, user.id, OtpCodeType.VERIFY_EMAIL)

    await send_email(user.email, "Verify your email", "confirm", user.username, otp)


async def logout_current_session(
    current_user: AuthorizedUser, db: AsyncSession
) -> None:
    session = (
        await db.execute(
            select(Session).filter_by(
                user_id=current_user.id, access_jti=current_user.jti
            )
        )
    ).scalar_one_or_none()

    bloom_filter.add(current_user.jti)

    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await db.delete(session)
    await db.commit()
