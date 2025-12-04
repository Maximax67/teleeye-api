from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.dependencies import (
    require_authorization,
    require_authorization_db,
    require_refresh_token,
)
from app.core.enums import OtpCodeType
from app.core.settings import settings
from app.db.models.otp_code import OtpCode
from app.db.models.session import Session
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import (
    AuthorizedUser,
    AuthorizedUserDb,
    EmailVerifyRequest,
    LoginRequest,
    PasswordForgotRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    RegisterRequest,
    EmailChangeRequest,
    SessionInfo,
    SessionListResponse,
    TokensResponse,
)
from app.schemas.common_responses import DetailResponse
from app.schemas.user import UserResponse
from app.services.auth import (
    hash_password,
    issue_otp,
    logout_current_session,
    issue_token_pair,
    revoke_all_sessions,
    revoke_session_by_jti,
    rotate_refresh_token,
    send_verification_email,
    verify_password,
)
from app.services.email import send_email
from app.core.limiter import limiter
from app.core.utils import get_session_name_from_user_agent
from app.services.bloom_filter import bloom_filter


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokensResponse,
    responses={
        400: {
            "description": "Bad request",
            "content": {"application/json": {"example": {"detail": "Bad request"}}},
        },
        409: {
            "description": "Email already registered",
            "content": {
                "application/json": {"example": {"detail": "Email already registered"}}
            },
        },
    },
)
@limiter.limit("5/minute")
async def register(
    request: Request,
    response: Response,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokensResponse:
    email_exists = await db.execute(select(exists().where(User.email == body.email)))
    if email_exists.scalar():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email,
        username=body.username,
        password_hash=hash_password(body.password),
        email_verified=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    session_name = get_session_name_from_user_agent(request)
    access, refresh, expires_in, refresh_expires_in = await issue_token_pair(
        db, user, session_name
    )

    return TokensResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )


@router.post(
    "/login",
    response_model=TokensResponse,
    responses={
        401: {
            "description": "Invalid credentials",
            "content": {
                "application/json": {"example": {"detail": "Invalid credentials"}}
            },
        },
        403: {
            "description": "User is banned",
            "content": {"application/json": {"example": {"detail": "User is banned"}}},
        },
    },
)
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokensResponse:
    field = User.email if body.email else User.username
    value = body.email or body.username

    q = await db.execute(select(User).where(field == value))
    user = q.scalar_one_or_none()
    if (
        not user
        or not user.password_hash
        or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user.is_banned:
        raise HTTPException(status_code=403, detail="User is banned")

    session_name = get_session_name_from_user_agent(request)
    access, refresh, expires_in, refresh_expires_in = await issue_token_pair(
        db, user, session_name
    )

    return TokensResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )


@router.post(
    "/logout",
    response_model=DetailResponse,
    responses={
        401: {
            "description": "Not authenticated or invalid token",
            "content": {
                "application/json": {"example": {"detail": "Not authenticated"}}
            },
        }
    },
)
@limiter.limit("5/minute")
async def logout(
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    await logout_current_session(current_user, db)
    return DetailResponse(detail="Logged out")


@router.post(
    "/logout_all",
    response_model=DetailResponse,
    responses={
        401: {
            "description": "Not authenticated",
            "content": {
                "application/json": {"example": {"detail": "Not authenticated"}}
            },
        },
    },
)
@limiter.limit("2/minute")
async def logout_all(
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    await revoke_all_sessions(db, current_user.id)
    return DetailResponse(detail="All sessions terminated")


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    responses={
        401: {
            "description": "Not authenticated",
            "content": {
                "application/json": {"example": {"detail": "Not authenticated"}}
            },
        },
    },
)
@limiter.limit("5/minute")
async def list_sessions(
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    q = await db.execute(select(Session).where(Session.user_id == current_user.id))
    sessions = q.scalars().all()
    result: List[SessionInfo] = []
    for s in sessions:
        result.append(
            SessionInfo(
                id=s.id,
                name=s.name,
                created_at=s.created_at,
                updated_at=s.updated_at,
                is_current=s.access_jti == current_user.jti,
            )
        )

    return SessionListResponse(sessions=result, limit=settings.MAX_USER_SESSIONS)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionInfo,
    responses={
        401: {
            "description": "Not authenticated",
            "content": {
                "application/json": {"example": {"detail": "Not authenticated"}}
            },
        },
        404: {
            "description": "Session not found",
            "content": {
                "application/json": {"example": {"detail": "Session not found"}}
            },
        },
    },
)
@limiter.limit("5/minute")
async def get_session(
    session_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> SessionInfo:
    q = await db.execute(
        select(Session).where(
            Session.id == session_id, Session.user_id == current_user.id
        )
    )
    s = q.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionInfo(
        id=s.id,
        name=s.name,
        created_at=s.created_at,
        updated_at=s.updated_at,
        is_current=s.access_jti == current_user.jti,
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    responses={
        401: {
            "description": "Not authenticated",
            "content": {
                "application/json": {"example": {"detail": "Not authenticated"}}
            },
        },
        404: {
            "description": "Session not found",
            "content": {
                "application/json": {"example": {"detail": "Session not found"}}
            },
        },
    },
)
@limiter.limit("5/minute")
async def revoke_session(
    session_id: int,
    request: Request,
    response: Response,
    current_user: AuthorizedUser = Depends(require_authorization),
    db: AsyncSession = Depends(get_db),
) -> Response:
    q = await db.execute(
        select(Session).where(
            Session.id == session_id, Session.user_id == current_user.id
        )
    )
    s = q.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")

    await revoke_session_by_jti(db, s.refresh_jti)
    bloom_filter.add(s.access_jti)

    return Response(status_code=204)


@router.post(
    "/refresh",
    response_model=TokensResponse,
    responses={
        401: {
            "description": "Invalid refresh token",
            "content": {
                "application/json": {"example": {"detail": "Invalid refresh token"}}
            },
        },
    },
)
@limiter.limit("5/minute")
async def refresh(
    request: Request,
    response: Response,
    refresh_payload: Dict[str, Any] = Depends(require_refresh_token),
    db: AsyncSession = Depends(get_db),
) -> TokensResponse:
    session_name = get_session_name_from_user_agent(request)
    access, refresh, expires_in, refresh_expires_in = await rotate_refresh_token(
        db, refresh_payload["sub"], refresh_payload["jti"], session_name
    )

    return TokensResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )


@router.get(
    "/me",
    response_model=UserResponse,
    responses={
        401: {
            "description": "Not authenticated or invalid access token",
            "content": {
                "application/json": {"example": {"detail": "Not authenticated"}}
            },
        },
        404: {
            "description": "User not found",
            "content": {"application/json": {"example": {"detail": "User not found"}}},
        },
    },
)
@limiter.limit("10/minute")
async def me(
    request: Request,
    response: Response,
    current_user: AuthorizedUserDb = Depends(require_authorization_db),
) -> UserResponse:
    return UserResponse.model_validate(current_user.user)


@router.post(
    "/email/send-confirmation",
    response_model=DetailResponse,
    responses={
        400: {
            "description": "No email on account",
            "content": {
                "application/json": {"example": {"detail": "No email on account"}}
            },
        },
        401: {
            "description": "Not authenticated",
            "content": {
                "application/json": {"example": {"detail": "Not authenticated"}}
            },
        },
        409: {
            "description": "Email already verified",
            "content": {
                "application/json": {"example": {"detail": "Email already verified"}}
            },
        },
    },
)
@limiter.limit("1/minute")
async def email_send_confirmation(
    request: Request,
    response: Response,
    current_user: AuthorizedUserDb = Depends(require_authorization_db),
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    if not current_user.user.email:
        raise HTTPException(status_code=400, detail="No email on account")

    if current_user.user.email_verified:
        raise HTTPException(status_code=409, detail="Email already verified")

    await send_verification_email(db, current_user.user)

    return DetailResponse(detail="Verification email sent")


@router.post(
    "/email/verify",
    response_model=DetailResponse,
    responses={
        404: {
            "description": "OTP code invalid or user not found",
            "content": {
                "application/json": {
                    "example": {"detail": "OTP code invalid or user not found"}
                }
            },
        },
    },
)
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    response: Response,
    body: EmailVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    q = await db.execute(
        select(OtpCode)
        .options(joinedload(OtpCode.user))
        .where(
            OtpCode.user_id == body.user_id,
            OtpCode.type == OtpCodeType.VERIFY_EMAIL,
            OtpCode.code == body.otp,
            OtpCode.created_at > func.now() - timedelta(minutes=settings.OTP_TTL),
        )
    )
    otp_code = q.scalar_one_or_none()
    if not otp_code:
        raise HTTPException(
            status_code=404, detail="OTP code invalid or user not found"
        )

    await db.delete(otp_code)
    otp_code.user.email_verified = True

    await db.commit()

    return DetailResponse(detail="Email verified")


@router.post(
    "/email/change",
    response_model=DetailResponse,
    responses={
        400: {
            "description": "New email is required or no change",
            "content": {
                "application/json": {
                    "examples": {
                        "required": {
                            "summary": "Missing email",
                            "value": {"detail": "New email is required"},
                        },
                        "no_change": {
                            "summary": "Same email",
                            "value": {"detail": "New email must differ from current"},
                        },
                    }
                }
            },
        },
        401: {
            "description": "Not authenticated",
            "content": {
                "application/json": {"example": {"detail": "Not authenticated"}}
            },
        },
    },
)
@limiter.limit("5/minute")
async def email_change(
    request: Request,
    response: Response,
    body: EmailChangeRequest,
    current_user: AuthorizedUserDb = Depends(require_authorization_db),
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    new_email = body.new_email
    if not new_email:
        raise HTTPException(status_code=400, detail="New email is required")

    if current_user.user.email.lower() == new_email.lower():
        raise HTTPException(
            status_code=400, detail="New email must differ from current"
        )

    current_user.user.email = new_email
    current_user.user.email_verified = False

    await revoke_all_sessions(db, current_user.user.id)
    await send_verification_email(db, current_user.user)

    return DetailResponse(detail="Email updated. Verification sent")


@router.post(
    "/password/forgot",
    response_model=DetailResponse,
)
@limiter.limit("1/minute")
async def password_forgot(
    request: Request,
    response: Response,
    body: PasswordForgotRequest,
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    q = await db.execute(select(User).where(User.email == body.email))
    user = q.scalar_one_or_none()
    if not user:
        return DetailResponse(detail="If the email exists, a reset link has been sent")

    otp = await issue_otp(db, user.id, OtpCodeType.PASSWORD_RESET)

    await send_email(user.email, "Password reset", "reset", user.username, otp)

    return DetailResponse(detail="If the email exists, a reset link has been sent")


@router.post(
    "/password/reset",
    response_model=DetailResponse,
    responses={
        400: {
            "description": "Missing or invalid token",
            "content": {
                "application/json": {
                    "examples": {
                        "missing": {
                            "summary": "Missing token",
                            "value": {"detail": "Missing token"},
                        },
                        "invalid_type": {
                            "summary": "Invalid token type",
                            "value": {"detail": "Invalid token type"},
                        },
                        "invalid_subject": {
                            "summary": "Invalid subject in token",
                            "value": {"detail": "Invalid subject in token"},
                        },
                    }
                }
            },
        },
        404: {
            "description": "User not found",
            "content": {"application/json": {"example": {"detail": "User not found"}}},
        },
        409: {
            "description": "New password is the same as old",
            "content": {
                "application/json": {
                    "example": {"detail": "New password is the same as old"}
                }
            },
        },
    },
)
@limiter.limit("5/minute")
async def password_reset(
    request: Request,
    response: Response,
    body: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    q = await db.execute(
        select(OtpCode)
        .options(selectinload(OtpCode.user))
        .join(OtpCode.user)
        .where(
            OtpCode.type == OtpCodeType.PASSWORD_RESET,
            OtpCode.code == body.otp,
            OtpCode.created_at
            > datetime.now(timezone.utc) - timedelta(minutes=settings.OTP_TTL),
            User.email == body.email,
        )
    )
    otp_code = q.scalar_one_or_none()
    if not otp_code:
        raise HTTPException(
            status_code=404, detail="OTP code invalid or user not found"
        )

    if verify_password(body.new_password, otp_code.user.password_hash):
        raise HTTPException(status_code=409, detail="New password is the same as old")

    otp_code.user.password_hash = hash_password(body.new_password)
    await db.delete(otp_code)
    await revoke_all_sessions(db, otp_code.user.id)

    return DetailResponse(detail="Password reset")


@router.post(
    "/password/change",
    response_model=DetailResponse,
    responses={
        401: {
            "description": "Invalid old password or user not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid old password or user not found"}
                }
            },
        },
    },
)
@limiter.limit("5/minute")
async def password_change(
    request: Request,
    response: Response,
    body: PasswordChangeRequest,
    db: AsyncSession = Depends(get_db),
) -> DetailResponse:
    q = await db.execute(select(User).where(User.email == body.email))
    user = q.scalar_one_or_none()
    if (
        not user
        or not user.password_hash
        or not verify_password(body.old_password, user.password_hash)
    ):
        raise HTTPException(
            status_code=401, detail="Invalid old password or user not found"
        )

    user.password_hash = hash_password(body.new_password)
    await revoke_all_sessions(db, user.id)

    return DetailResponse(detail="Password updated")
