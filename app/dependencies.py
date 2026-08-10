from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from crud.users import get_user_by_id
from models.user import APIKey, User
from services.auth import decode_token, hash_api_key

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
settings = get_settings()


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exception

        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception

        user = await get_user_by_id(db, uuid.UUID(user_id))
        if user is None:
            raise credentials_exception

        return user
    except (ValueError, TypeError):
        raise credentials_exception


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user",
        )
    return current_user


async def get_current_admin_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """Require an active user whose email is in the configured admin allowlist."""
    if current_user.email.strip().lower() not in settings.admin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


async def verify_api_key(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Authenticate a request using an API key from the X-API-Key header.

    Hashes the incoming raw key, looks up the stored hash, checks that the
    key is active, updates last_used_at, and returns the associated active user.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or inactive API key",
    )

    key_hash = hash_api_key(x_api_key)

    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash)
    )
    api_key = result.scalar_one_or_none()

    if api_key is None or not api_key.is_active:
        raise credentials_exception

    user = await get_user_by_id(db, api_key.user_id)
    if user is None or not user.is_active:
        raise credentials_exception

    api_key.last_used_at = datetime.now(UTC)
    await db.commit()

    return user
