from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import bcrypt
import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token
from app.database import get_db
from app.main import app, limiter as app_limiter
from app.routers import auth
from app.schemas import LoginRequest, UserCreate


def test_auth_router_uses_the_single_application_limiter():
    assert auth.limiter is app_limiter


def test_passwords_respect_bcrypts_72_utf8_byte_boundary():
    assert UserCreate.model_validate(
        {"email": "user@example.com", "password": "É" * 35 + "A1"}
    )
    with pytest.raises(ValidationError, match="72 octets"):
        UserCreate.model_validate(
            {"email": "user@example.com", "password": "É" * 36 + "A1"}
        )
    with pytest.raises(ValidationError, match="72 octets"):
        LoginRequest.model_validate(
            {"email": "user@example.com", "password": "É" * 37}
        )


def test_dummy_login_hash_is_valid_cost_12_bcrypt():
    encoded = auth.DUMMY_PASSWORD_HASH.encode("ascii")
    assert encoded.startswith(b"$2b$12$")
    assert bcrypt.checkpw(b"definitely-not-the-dummy", encoded) is False


def test_access_tokens_require_all_registered_security_claims():
    user_id = uuid.uuid4()
    assert decode_access_token(create_access_token(user_id)) == user_id

    missing_exp = jwt.encode(
        {"sub": str(user_id), "iat": datetime.now(timezone.utc)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    assert decode_access_token(missing_exp) is None


def test_concurrent_registration_conflict_is_generic_and_rolls_back(monkeypatch):
    class ConflictingDB:
        rolled_back = False

        def scalar(self, statement):
            return None

        def add(self, value):
            self.value = value

        def commit(self):
            raise IntegrityError("insert", {}, RuntimeError("unique violation"))

        def rollback(self):
            self.rolled_back = True

    db = ConflictingDB()
    monkeypatch.setattr(auth, "hash_password", lambda value: "hashed")
    body = UserCreate(email="new@example.com", password="SecurePass1")

    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.register.__wrapped__(request=None, body=body, db=db))

    assert error.value.status_code == 400
    assert error.value.detail == "Impossible de créer ce compte."
    assert db.rolled_back is True


def test_register_and_login_limits_are_enforced_by_the_shared_middleware(monkeypatch):
    class ExistingUserDB:
        def scalar(self, statement):
            return SimpleNamespace(
                hashed_password=auth.DUMMY_PASSWORD_HASH,
                is_active=True,
            )

    def override_db():
        yield ExistingUserDB()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(auth, "verify_password", lambda plain, hashed: False)
    app_limiter._storage.reset()
    try:
        with TestClient(app) as client:
            register_payload = {
                "email": "existing@example.com",
                "password": "SecurePass1",
            }
            responses = [
                client.post("/api/v1/auth/register", json=register_payload)
                for _ in range(11)
            ]
            assert responses[-1].status_code == 429

            app_limiter._storage.reset()
            login_payload = {
                "email": "missing@example.com",
                "password": "SecurePass1",
            }
            responses = [
                client.post("/api/v1/auth/login", json=login_payload)
                for _ in range(21)
            ]
            assert responses[-1].status_code == 429
    finally:
        app_limiter._storage.reset()
        app.dependency_overrides.clear()
