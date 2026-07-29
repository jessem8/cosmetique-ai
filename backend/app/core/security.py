"""
Security utilities: password hashing (bcrypt) + JWT (HS256).

Design decisions:
 - bcrypt cost factor 12 (good balance security/speed)
 - JWT payload contains only user_id (UUID as str)
 - Token expiry is checked by PyJWT automatically
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from jwt import InvalidTokenError

def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt without silent truncation."""
    encoded = plain.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("bcrypt passwords are limited to 72 UTF-8 bytes")
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(encoded, salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        encoded = plain.encode("utf-8")
        if len(encoded) > 72:
            return False
        return bcrypt.checkpw(encoded, hashed.encode("utf-8"))
    except ValueError:
        return False


# ── JWT ────────────────────────────────────────────────────────────────────


from app.core.config import settings

def create_access_token(
    user_id: uuid.UUID,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token.

    Payload:
      sub  = str(user_id)
      iat  = issued-at timestamp
      exp  = expiry timestamp
    """
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": str(user_id),
        "iat": datetime.now(timezone.utc),
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Optional[uuid.UUID]:
    """
    Decode and validate a JWT.

    Returns the user UUID on success, None on any failure.
    Caller is responsible for raising HTTP 401 on None.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"require": ["sub", "iat", "exp"]},
        )
        sub: Optional[str] = payload.get("sub")
        if sub is None:
            return None
        return uuid.UUID(sub)
    except (InvalidTokenError, ValueError):
        return None
