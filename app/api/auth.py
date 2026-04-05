"""
Authentication API routes.
"""
import hmac
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, get_db
from app.time_utils import serialize_datetime, utc_now
from app.services import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

CSRF_SESSION_KEY = "csrf_token"


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


def get_or_create_csrf_token(request: Request) -> str:
    """Return a stable CSRF token for the current session."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def validate_csrf(request: Request) -> None:
    """Validate CSRF token for form or AJAX requests."""
    session_token = request.session.get(CSRF_SESSION_KEY)
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing CSRF session token"
        )

    request_token = (request.headers.get("X-CSRF-Token") or "").strip()
    if not request_token:
        content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
            form = await request.form()
            request_token = str(form.get("csrf_token") or "").strip()

    if not request_token or not hmac.compare_digest(session_token, request_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token"
        )


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """Get current user from session or token."""
    from app.services import verify_token

    user_id = request.session.get("user_id")
    if user_id:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user and user.is_active:
            return user

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        username = verify_token(token)
        if username:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user and user.is_active:
                return user

    return None


async def require_auth(
    request: Request,
    session: AsyncSession = Depends(get_db)
) -> User:
    """Require authentication for API routes."""
    user = await get_current_user(request, session)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_auth_csrf(
    request: Request,
    session: AsyncSession = Depends(get_db)
) -> User:
    """Require authentication and enforce CSRF for cookie-backed sessions."""
    user = await require_auth(request, session)
    if request.session.get("user_id"):
        await validate_csrf(request)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    login_data: LoginRequest,
    session: AsyncSession = Depends(get_db)
):
    """Login and get JWT token."""
    result = await session.execute(select(User).where(User.username == login_data.username))
    user = result.scalar_one_or_none()

    if not user or not user.is_active or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user.last_login = utc_now()
    await session.commit()

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/login/form")
async def login_form(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_db)
):
    """Login via form submission (for web UI)."""
    await validate_csrf(request)

    result = await session.execute(select(User).where(User.username == username.strip()))
    user = result.scalar_one_or_none()

    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return RedirectResponse(
            url="/login?error=invalid_credentials",
            status_code=status.HTTP_302_FOUND
        )

    user.last_login = utc_now()
    await session.commit()

    request.session.clear()
    request.session["user_id"] = user.id
    get_or_create_csrf_token(request)

    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@router.post("/logout")
async def logout(request: Request):
    """Logout and clear session."""
    await validate_csrf(request)
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


@router.get("/me")
async def get_me(current_user: User = Depends(require_auth)):
    """Get current user info."""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "created_at": serialize_datetime(current_user.created_at),
    }
