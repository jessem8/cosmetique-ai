"""
FastAPI dependencies: database session + authenticated user.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models import User

# ── Security scheme ────────────────────────────────────────────────────────
_bearer = HTTPBearer(auto_error=False)

# ── Type aliases ───────────────────────────────────────────────────────────
DBSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DBSession,
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> User:
    """
    Validate Bearer token and return the authenticated User.

    Raises HTTP 401 when:
      - No Authorization header
      - Token is expired or tampered
      - User not found or deactivated
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token d'authentification manquant.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: uuid.UUID | None = decode_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable ou désactivé.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


# Shortcut type alias for routes
CurrentUser = Annotated[User, Depends(get_current_user)]
