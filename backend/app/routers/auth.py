"""
Authentication router: register + login with rate limiting.

Security measures:
 - Rate limit: 10 req/min on register, 20 req/min on login
 - Generic error messages (no user enumeration)
 - Bcrypt password hashing (cost 12)
 - JWT Bearer token in response
"""
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.rate_limit import limiter
from app.core.security import create_access_token, hash_password, verify_password
from app.dependencies import DBSession
from app.models import User
from app.schemas import LoginRequest, Token, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["Auth"])
DUMMY_PASSWORD_HASH = (
    "$2b$12$nQTbq3VpEWvDsn7WWHMie.OzLtF88/E4SRGj63bmJ9xlIs8g5erzm"
)


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un compte utilisateur",
)
@limiter.limit("10/minute")
async def register(request: Request, body: UserCreate, db: DBSession) -> User:
    """
    Create a new user account.

    - Rejects duplicate email with a generic message to avoid enumeration.
    - Hashes password with bcrypt (cost 12).
    """
    # Check for existing email without leaking "email exists"
    existing = db.scalar(select(User).where(User.email == body.email))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible de créer ce compte.",  # generic — no enumeration
        )

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible de créer ce compte.",
        ) from exc
    db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=Token,
    summary="Connexion et obtention du token JWT",
)
@limiter.limit("20/minute")
async def login(request: Request, body: LoginRequest, db: DBSession) -> dict:
    """
    Authenticate user and return a JWT Bearer token.

    - Uses constant-time comparison to prevent timing attacks.
    - Generic error on wrong credentials.
    """
    user = db.scalar(select(User).where(User.email == body.email))

    # Always call verify_password to prevent timing attacks even if user not found
    hashed = user.hashed_password if user else DUMMY_PASSWORD_HASH
    valid = verify_password(body.password, hashed)

    if not user or not valid or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(user_id=user.id)
    return {"access_token": token, "token_type": "bearer"}
