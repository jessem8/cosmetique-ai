from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


BASE = {
    "DATABASE_URL": "postgresql://localhost/cosmetique_test",
    "SECRET_KEY": "test-only-secret-key-with-at-least-32-bytes",
    "AI_SERVICE_TOKEN": "test-only-ai-token-with-at-least-32-bytes",
}


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://*.example.com",
        "http://localhost/path",
        "http://user:password@localhost",
        "javascript:alert(1)",
    ],
)
def test_cors_origins_must_be_exact_http_origins(origin):
    with pytest.raises(ValidationError):
        Settings(
            **BASE,
            APP_ENV="test",
            CORS_ORIGINS=origin,
            AI_SERVICE_URL="https://runtime.example",
        )


def test_production_requires_https_ai_service_and_strong_token():
    with pytest.raises(ValidationError):
        Settings(
            **BASE,
            APP_ENV="production",
            CORS_ORIGINS="https://studio.example",
            AI_SERVICE_URL="http://runtime.example",
        )
    with pytest.raises(ValidationError):
        Settings(
            **{**BASE, "AI_SERVICE_TOKEN": "short"},
            APP_ENV="production",
            CORS_ORIGINS="https://studio.example",
            AI_SERVICE_URL="https://runtime.example",
        )


def test_local_http_ai_service_is_allowed_only_outside_production():
    settings = Settings(
        **BASE,
        APP_ENV="test",
        CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173",
        AI_SERVICE_URL="http://localhost:9000",
    )
    assert settings.cors_origins_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    with pytest.raises(ValidationError):
        Settings(
            **BASE,
            APP_ENV="test",
            CORS_ORIGINS="http://localhost:5173",
            AI_SERVICE_URL="http://remote.example",
        )
